"""C01-C24 executable LOCAL acceptance, real owned Linux generations.

Only technical evidence is retained. A failed assertion remains a failed case;
cleanup is recorded separately and is never credited as recovery.
"""
from __future__ import annotations

import asyncio
import time

from xoc_mcp_lab_l4.client import QueryClient
from .harness import (SupervisedProcess, TOOLS, check_result, make_envelope,
                      query, safe_row, restored, child_identity, ResourceSampler, resource_violations)


def matching(server, request_id, event):
    return [r for r in server.events() if r.get('request_id') == request_id
            and r.get('event') == event]


async def business_finished(server, request_id):
    rows = await server.wait_event(request_id, 'worker_finished')
    business = [r for r in rows if r.get('handler_calls') == 1]
    assert len(business) == 1, 'LAB_L6_EXPECTED_SINGLE_BUSINESS_FINISH'
    return business[0]


async def started(server, prefix='fault-block', profile='admin_tenant_a', timeout=.35):
    value = make_envelope(profile, prefix=prefix)
    pending = asyncio.create_task(QueryClient(server.url).run(value, timeout=timeout))
    events = await server.wait_event(value['request_id'])
    return value, pending, events[0]


async def until(when):
    while time.monotonic() < when:
        await asyncio.sleep(min(.01, max(0, when-time.monotonic())))


def lifecycle(row):
    assert row['worker_context_cleared'], 'LAB_L6_CONTEXT_NOT_RESET'
    stamps = [row[k] for k in ('admitted_at', 'worker_started_at', 'callable_finished_at',
                               'context_reset_at', 'future_done_at', 'slot_released_at')]
    assert all(v is not None for v in stamps) and stamps == sorted(stamps), 'LAB_L6_LIFECYCLE_ORDER'
    assert row['writes'] == 0, 'LAB_L6_UNEXPECTED_WRITE'
    return {k: row[k] for k in ('generation', 'internal_admission_id', 'request_id',
        'admitted_at', 'worker_started_at', 'callable_finished_at', 'context_reset_at',
        'future_done_at', 'slot_released_at', 'handler_calls', 'executor_calls', 'reads',
        'writes', 'audit_records', 'worker_context_cleared')}


def audit_matches(worker, profile, name):
    from xoc_mcp_lab.fixtures import PROFILES
    identity = PROFILES[profile]
    records = worker['audit_records']
    assert len(records) == 1, 'LAB_L6_SINGLE_AUDIT_REQUIRED'
    expected = dict(effective_tenant_id=identity[1], user_id=identity[2], role=identity[3],
        source='sophia_chat', request_id=worker['request_id'], tool_name=name)
    assert all(records[0].get(k) == v for k, v in expected.items()), 'LAB_L6_AUDIT_CONTEXT_MISMATCH'


async def recovered(server, generation=1):
    incident = await server.wait_message(lambda r: r.get('kind') == 'incident'
        and r.get('child_generation') == generation, 8)
    measurement = await restored(server, generation+1, fault_at=incident['fault_at'])
    phase = await server.wait_message(lambda r: r.get('kind') == 'phase'
        and r.get('phase') == 'recovery' and r.get('child_generation') == generation+1, 1)
    retired = next(r for r in server.messages if r.get('kind') == 'retired'
                   and r.get('child_generation') == generation)
    assert phase['ok'] and phase['elapsed'] <= 12, 'LAB_L6_RECOVERY_BUDGET'
    assert retired['reaped'] and retired['process_absent'] and not retired['residual'], 'LAB_L6_REAP_REQUIRED'
    assert retired['finished_at'] < server.readiness['ready_at'], 'LAB_L6_GENERATION_OVERLAP'
    detection = incident['decision_at'] - incident['fault_at']
    if incident['safe_reason'] == 'heartbeat_stale':
        assert detection <= 2.25, 'LAB_L6_HEARTBEAT_DETECTION_BUDGET'
    if incident['safe_reason'] in ('work_age_exceeded', 'work_age_control_unconfirmed'):
        oldest = min(j['admitted_at'] for j in incident['jobs'])
        assert incident['decision_at']-oldest <= 5.25, 'LAB_L6_WORK_DETECTION_BUDGET'
    assert not child_identity(retired['child_pid']), 'LAB_L6_OLD_GENERATION_PRESENT'
    return dict(incident=incident, detection_seconds=detection, recovery=phase,
                retirement=retired, restoration=measurement)


async def c01(server):
    rows = []
    for name in TOOLS:
        result = await query(server, name=name)
        check_result(result, 'admin_tenant_a', name)
        assert result['total_seconds'] <= 3.5
        worker = await business_finished(server, result['request_id'])
        assert worker['handler_calls'] == worker['executor_calls'] == 1
        audit_matches(worker, 'admin_tenant_a', name)
        rows.append(dict(client=safe_row(result), lifecycle=lifecycle(worker)))
    status = await server.wait_idle()
    assert status['attempt'] == 0
    return dict(queries=rows, idle=status)


async def c02(server):
    result = await query(server, prefix='fault-error')
    assert result['status'] == 'error' and result['code'] == 'execution_failed'
    worker = await business_finished(server, result['request_id'])
    assert worker['handler_calls'] == worker['executor_calls'] == 1
    b = await query(server, 'admin_tenant_b', TOOLS[3])
    check_result(b, 'admin_tenant_b', TOOLS[3])
    status = await server.wait_idle()
    assert status['attempt'] == 0
    return dict(error=safe_row(result), lifecycle=lifecycle(worker), b=safe_row(b), idle=status)


async def c03(server):
    value, pending, entered = await started(server, 'fault-slow', timeout=.3)
    result = await pending
    assert result['status'] == 'timeout'
    assert not matching(server, value['request_id'], 'worker_finished')
    b = await query(server, 'admin_tenant_b', TOOLS[3])
    check_result(b, 'admin_tenant_b', TOOLS[3])
    worker_b = (await server.wait_event(b['request_id'], 'worker_finished'))[-1]
    assert worker_b['pool']['active'] >= 1
    worker = (await server.wait_event(value['request_id'], 'worker_finished'))[-1]
    assert worker['worker_elapsed_ns'] >= 1_500_000_000
    assert (await server.wait_idle())['attempt'] == 0
    return dict(a=safe_row(result), b=safe_row(b), lifecycle=lifecycle(worker),
        remote_timeout_is_not_server_cancellation=True,
        degraded_observed=any(r.get('state') == 'DEGRADED' for r in server.messages))


async def c04(server):
    value, pending, entered = await started(server)
    b = await query(server, 'admin_tenant_b', TOOLS[3])
    check_result(b, 'admin_tenant_b', TOOLS[3])
    row = (await server.wait_event(b['request_id'], 'worker_finished'))[-1]
    assert row['pool']['active'] == 1
    assert (await pending)['status'] == 'timeout'
    result = await recovered(server)
    assert len(matching(server, value['request_id'], 'provider_entered')) == 1
    assert not matching(server, value['request_id'], 'worker_finished'), 'LAB_L6_FAKE_RELEASE'
    return dict(blocked_request_id=value['request_id'], healthy_before_cut=safe_row(b), **result)


async def c05(server):
    a, pa, _ = await started(server)
    snap = await server.wait_message(lambda r: r.get('kind') == 'snapshot' and r.get('occupancy') == 1, 1)
    admission = snap['jobs'][0]['admitted_at']
    await until(admission+4.1)
    b, pb, _ = await started(server, 'fault-long', 'admin_tenant_b', timeout=5)
    result = await recovered(server)
    rb = await pb
    await pa
    assert rb['status'] != 'allowed', 'LAB_L6_HEALTHY_IN_FLIGHT_NOT_LOST'
    assert len(matching(server, b['request_id'], 'provider_entered')) == 1
    assert not matching(server, b['request_id'], 'worker_finished')
    assert not any(r.get('request_id') == b['request_id'] for r in server.events(2))
    b_entry = matching(server, b['request_id'], 'provider_entered')[0]
    ack = next(r for r in server.messages if r.get('kind') == 'phase' and r.get('phase') == 'close_ack')
    assert ack['acknowledged'] and b_entry['monotonic_ns']/1e9 < ack['started_at']
    retained_at_cut = [r for r in server.messages if r.get('kind') == 'snapshot'
        and r.get('child_generation') == 1 and r.get('sent_at', 0) >= ack['finished_at']
        and any(j['internal_admission_id'] == b_entry['internal_admission_id'] for j in r['jobs'])]
    assert retained_at_cut, 'LAB_L6_HEALTHY_NOT_PROVEN_IN_FLIGHT'
    return dict(healthy_interrupted=safe_row(rb), request_id=b['request_id'],
                admitted_before_ack=b_entry, retained_during_drain=retained_at_cut[-1],
                accepted_loss_not_replayed=True, **result)


async def c06(server):
    values = await asyncio.gather(*[started(server, profile=p) for p in
        ('admin_tenant_a', 'admin_tenant_b', 'user_a2', 'user_a1')])
    full = await server.wait_message(lambda r: r.get('kind') == 'snapshot' and r.get('occupancy') == 4, 1)
    fifth = await query(server)
    assert fifth['code'] == 'lab_capacity_busy', 'LAB_L6_FIFTH_NOT_BUSY'
    assert not matching(server, fifth['request_id'], 'provider_entered')
    result = await recovered(server)
    for value, task, _ in values:
        assert (await task)['status'] == 'timeout'
        assert len(matching(server, value['request_id'], 'provider_entered')) == 1
        assert not matching(server, value['request_id'], 'worker_finished')
    return dict(full=full, fifth=safe_row(fifth), **result)


async def c07(server):
    _, pending, _ = await started(server)
    await server.wait_message(lambda r: r.get('kind') == 'state' and r.get('state') == 'DRAINING', 8)
    draining = await query(server, timeout=.25)
    retired = await server.wait_message(lambda r: r.get('kind') == 'retired', 3)
    absent = await query(server, timeout=.25)
    # This dedicated scenario holds the private probe scheduler for 200ms,
    # after the real listener and CLOSED snapshot, never weakening the gate.
    closed = await server.wait_message(lambda r: r.get('kind') == 'snapshot'
        and r.get('child_generation') == 2 and r.get('gate_state') == 'CLOSED', 5)
    starting = await query(server, timeout=.25)
    result = await recovered(server)
    await pending
    for row in (draining, absent, starting):
        assert row['status'] != 'allowed'
        assert not matching(server, row['request_id'], 'provider_entered')
    return dict(during_draining=safe_row(draining), while_absent=safe_row(absent),
                listener_bound_closed_snapshot=closed, trusted_probe_scheduling_hold=.2,
                during_starting=safe_row(starting), **result)


async def c08(server):
    positive = await c04(server)
    # Negative restoration is a real refused TCP connection, not a fabricated
    # success/ready response. Only the local harness selects this closed port.
    import socket
    from types import SimpleNamespace
    from unittest.mock import patch
    from . import harness
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        endpoint = SimpleNamespace(url=f'http://127.0.0.1:{sock.getsockname()[1]}/mcp')
        original = harness.query
        observed = []
        async def refused(_server, *args, **kwargs):
            result = await original(endpoint, *args, **kwargs)
            observed.append(safe_row(result))
            return result
        index = len(server.messages)
        server.command('test', action='recover')
        incident = await server.wait_message(lambda r: r.get('kind') == 'incident'
            and r.get('child_generation') == 2, 2, index)
        try:
            with patch.object(harness, 'query', refused):
                await restored(server, 3, fault_at=incident['fault_at'])
            raise AssertionError('LAB_L6_FAILED_RESTORE_ACCEPTED')
        except AssertionError as error:
            assert str(error) == 'LAB_L6_NEW_NETWORK_QUERY_FAILED'
    status = await server.status()
    assert len(observed) == 1 and observed[0]['status'] != 'allowed'
    assert status['state'] == 'UNAVAILABLE' and status['child_pid'] is None
    return dict(positive=positive, refused_tcp_query=observed, failed_restore_status=status,
                no_retry=True, negative_control=server.messages[index:])


async def c11(server):
    await server.close()  # Serial scenarios share the one container resource budget.
    evidence = []
    for profile in ('reap_fail', 'replacement_startup_fail', 'replacement_integrity_fail',
                    'replacement_probe_denied', 'replacement_probe_timeout'):
        child = SupervisedProcess(profile)
        try:
            await child.wait_ready()
            child.command('test', action='recover')
            result = await child.wait_message(lambda r: r.get('kind') == 'state'
                and r.get('state') == 'UNAVAILABLE', 14)
            await asyncio.sleep(.3)
            status = await child.status()
            assert status['attempt'] == 1 and status['state'] == 'UNAVAILABLE'
            assert not any(r.get('kind') == 'ready' and r.get('child_generation', 0) > 1 for r in child.messages)
            assert status['child_generation'] <= 2
            if profile == 'reap_fail':
                assert status['residual'] and status['child_generation'] == 1
            evidence.append(dict(profile=profile, unavailable=result, status=status, control=list(child.messages)))
        finally:
            cleanup = await child.close()
        evidence[-1]['cleanup'] = cleanup
    return dict(negative_recoveries=evidence)


async def c12(server):
    rows = []
    for generation in (1, 2):
        server.command('test', action='recover')
        rows.append(await recovered(server, generation))
        assert (await server.status())['attempt'] == generation
    server.command('test', action='recover')
    stopped = await server.wait_message(lambda r: r.get('kind') == 'state'
        and r.get('state') == 'UNAVAILABLE', 4)
    await asyncio.sleep(.3)
    status = await server.status()
    assert status['attempt'] == 2 and status['child_generation'] == 3
    assert len([r for r in server.messages if r.get('kind') == 'ready']) == 3
    assert status['child_pid'] is None
    return dict(recoveries=rows, third_incident=stopped, status=status)


async def c14(server):
    result = await query(server, prefix='fault-response-lost')
    assert result['status'] != 'allowed'
    worker = await business_finished(server, result['request_id'])
    await server.wait_event(result['request_id'], 'delivery_lost')
    assert worker['handler_calls'] == worker['executor_calls'] == worker['reads'] == 1
    assert len(matching(server, result['request_id'], 'provider_entered')) == 1
    b = await query(server, 'admin_tenant_b', TOOLS[3])
    check_result(b, 'admin_tenant_b', TOOLS[3])
    assert (await server.wait_idle())['attempt'] == 0
    return dict(lost=safe_row(result), lifecycle=lifecycle(worker), no_replay=True, b=safe_row(b))


async def c15(server):
    recovery = await c06(server)
    rows = []
    for profile in ('admin_tenant_a', 'admin_tenant_b', 'user_a2'):
        for name in TOOLS:
            row = await query(server, profile, name)
            check_result(row, profile, name)
            worker = await business_finished(server, row['request_id'])
            audit_matches(worker, profile, name)
            rows.append(dict(client=safe_row(row), lifecycle=lifecycle(worker)))
    denied = await query(server, 'admin_tenant_a', TOOLS[1], args={'ticket_id': 'lab-ticket-b1'})
    check_result(denied, 'admin_tenant_a', TOOLS[1], arguments={'ticket_id': 'lab-ticket-b1'})
    assert 'lab-ticket-b1' not in str(denied.get('result', {}).get('data'))
    return dict(recovery=recovery, after=rows, cross_tenant=safe_row(denied))


async def c16(server):
    check_result(await query(server, 'admin_tenant_b', TOOLS[3]), 'admin_tenant_b', TOOLS[3])
    await server.wait_idle()
    resources = [server.resources()]
    recoveries = []
    for generation in (1, 2):
        server.command('test', action='recover')
        recoveries.append(await recovered(server, generation))
        resources.append(server.resources())
    await server.wait_idle()
    return dict(resources=resources, recoveries=recoveries)


async def c18(server):
    index = len(server.messages)
    server.command('test', action='loop_block', duration=10.0)
    await server.wait_message(lambda r: r.get('kind') == 'ack' and r.get('action') == 'loop_block', 1, index)
    result = await recovered(server)
    assert result['incident']['safe_reason'] == 'heartbeat_stale'
    assert not any(r.get('kind') == 'state' and r.get('state') == 'DRAINING'
        and r.get('child_generation') == 1 for r in server.messages)
    return result


async def c19(server):
    value, pending, _ = await started(server)
    index = len(server.messages)
    server.command('test', action='crash')
    await server.wait_message(lambda r: r.get('kind') == 'ack' and r.get('action') == 'crash', 1, index)
    result = await recovered(server)
    await pending
    assert result['retirement']['returncode'] == 86
    assert len(matching(server, value['request_id'], 'provider_entered')) == 1
    return result


async def run_case(case):
    from . import acceptance_context, acceptance_control
    functions = {f'C{i:02}': globals().get(f'c{i:02}') for i in range(1, 25)}
    for i in (9, 10, 13, 21, 22):
        functions[f'C{i:02}'] = getattr(acceptance_context, f'c{i:02}')
    for i in (17, 20, 23, 24):
        functions[f'C{i:02}'] = getattr(acceptance_control, f'c{i:02}')
    server = SupervisedProcess('replacement_startup_hold' if case == 'C07' else 'mixed')
    sampler = ResourceSampler(server)
    row = dict(case=case, passed=False)
    try:
        await server.wait_ready()
        row['before'] = server.resources()
        row['evidence'] = await functions[case](server)
        row['passed'] = True
    except Exception as error:
        # Assertions use fixed lab strings only; no exception repr/raw response.
        row['error_type'] = type(error).__name__
        import traceback
        from pathlib import Path
        last = traceback.extract_tb(error.__traceback__)[-1]
        row['failure_location'] = dict(file=Path(last.filename).name, line=last.lineno)
        code = str(error)
        row['error_code'] = code if code.startswith('LAB_') and len(code) < 120 else 'LAB_L6_ACCEPTANCE_FAILED'
    finally:
        row['after'] = server.resources()
        try:
            row['cleanup'] = await server.close()
        except Exception as error:
            row['cleanup'] = dict(error_type=type(error).__name__, own_server_residual=list(server.owned_children),
                                  supervisor_absent=False)
            row['passed'] = False
        row['resource_samples'] = await sampler.stop()
        row['resource_errors'] = resource_violations(row['resource_samples'])
        row['control'] = list(server.messages)
        row['events'] = server.events()
        if row['resource_errors']:
            row['passed'] = False
            row['error_code'] = 'LAB_L6_RESOURCE_BUDGET'
        if row['cleanup']['own_server_residual'] or not row['cleanup']['supervisor_absent']:
            row['passed'] = False
            row['error_code'] = 'LAB_L6_CLEANUP_FAILED'
    return row


async def run_cases(selected=None):
    names = selected or [f'C{i:02}' for i in range(1, 25)]
    rows = []
    for case in names:
        row = await run_case(case)
        rows.append(row)
        print(f"{case}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
        if row['cleanup'].get('own_server_residual') or not row['cleanup'].get('supervisor_absent'):
            break  # No second live instance to hide a cleanup/ownership failure.
        if row.get('error_code') in ('LAB_FIXTURE_EXACT_RESULT_MISMATCH', 'LAB_FIXTURE_CANARY_EXPOSED',
                                     'LAB_L6_AUDIT_CONTEXT_MISMATCH'):
            break  # Security/projection mismatch is not a recoverable acceptance warning.
    return dict(cases=rows, all_passed=all(r['passed'] for r in rows),
                passed=sum(r['passed'] for r in rows), total=len(rows),
                lab_only=True, remote_providers=False)
