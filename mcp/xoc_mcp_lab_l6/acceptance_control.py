"""Installed C17/C20/C23/C24 control acceptance, safe technical evidence only.

Direct protocol/guard subchecks are explicitly labelled. Actual retirement,
supervisor death, lease expiry and recovery run independent Linux processes.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

from .control import ControlChannel, MAX_BACKLOG


async def _test_ack(server, action, **fields):
    index = len(server.messages)
    server.command("test", action=action, **fields)
    return await server.wait_message(lambda row: row.get("kind") == "ack"
        and row.get("command") == "test" and row.get("action") == action, 2, index)


async def c17(server):
    from .harness import query, restored, safe_row, check_result, TOOLS
    old_generation = server.generation
    index = len(server.messages)
    fault_at = time.monotonic()
    await _test_ack(server, "crash")
    restoration = await restored(server, old_generation + 1, fault_at=fault_at)
    slow = asyncio.create_task(query(server, prefix="fault-slow", timeout=3.5))
    # Technical control confirms a real occupied slot before transit injection.
    occupied = await server.wait_message(lambda row: row.get("kind") == "snapshot"
        and row.get("child_generation") == server.generation and row.get("occupancy") == 1,
        2, index)
    before = await server.status()
    ack = await _test_ack(server, "stale_snapshot")
    await asyncio.sleep(.04)
    after = await server.status()
    assert after["rejected"] >= before["rejected"] + 1
    assert after["child_generation"] == before["child_generation"] == old_generation + 1
    assert after["attempt"] == before["attempt"] == 1
    assert after["occupancy"] == before["occupancy"] == 1
    assert after["gate_state"] == before["gate_state"] == "OPEN"
    assert after["jobs"][0]["internal_admission_id"] == before["jobs"][0]["internal_admission_id"]
    healthy = await query(server, "admin_tenant_b", TOOLS[3])
    check_result(healthy, "admin_tenant_b", TOOLS[3])
    slow_result = await slow
    check_result(slow_result, "admin_tenant_a", TOOLS[0])
    await server.wait_idle()
    return dict(simulated_in_transit=True, retired_process_did_not_execute=True,
                restoration=restoration, occupied=occupied, injection_ack=ack,
                before=before, after=after, healthy=safe_row(healthy), slow=safe_row(slow_result),
                control=server.messages[index:])


async def _lease_channel_checks():
    """Real socket framing + direct gate checks; NOT a recovery-process mock."""
    from .pool import AdmissionGate
    left, right = socket.socketpair()
    emitter, receiver = ControlChannel(left, 7), ControlChannel(right, 7)
    gate = AdmissionGate(7, threading.RLock())
    issued = time.monotonic()
    assert gate.open(7, 1, issued + .10, ready=True)
    try:
        emitter.send("lease", lease_until=issued + .10)
        await asyncio.sleep(.13)  # Deliberately retain emitted renewal unread.
        received = receiver.receive()[0]
        received_at = time.monotonic()
        accepted = gate.renew(7, received["seq"] + 1, received["lease_until"])
        assert not accepted and not gate.permits()
        absolute_until = gate.lease_until
        assert absolute_until == issued + .10
        assert gate.renew(7, 3, time.monotonic() + 1)
        assert not gate.permits()  # A fresh renewal cannot undo confirmed closure.
        assert not gate.renew(6, 500, time.monotonic() + 1)
        assert not gate.renew(7, 3, time.monotonic() + 1)
        saturation_started = time.monotonic()
        for _ in range(50000):
            emitter.send("lease", lease_until=saturation_started + 1)
            if emitter.dropped:
                break
        assert emitter.dropped and emitter.backlog <= MAX_BACKLOG
        return dict(kind="direct_gate_and_real_bounded_socket_subcheck", issued_at=issued,
                    received_at=received_at, emitted_lease_until=received["lease_until"],
                    retained_absolute_until=absolute_until, late_renewal_accepted=accepted,
                    fresh_renewal_reopened=False, backlog=emitter.backlog, dropped=emitter.dropped,
                    saturation_seconds=time.monotonic()-saturation_started)
    finally:
        emitter.close()
        receiver.close()


async def _supervisor_death():
    from .harness import SupervisedProcess, query, safe_row, child_identity, check_result, TOOLS
    server = SupervisedProcess("mixed")
    rows = []
    try:
        await server.wait_ready()
        # Before expiry, loop resumes normally and a new read succeeds.
        await _test_ack(server, "loop_block", duration=.12)
        healthy = await query(server, "admin_tenant_b", TOOLS[3])
        check_result(healthy, "admin_tenant_b", TOOLS[3])
        blocked = await query(server, prefix="fault-block", timeout=.3)
        await server.wait_event(blocked["request_id"])
        snapshot = await server.status()
        assert snapshot["occupancy"] == 1
        resume_after_expiry = await _test_ack(server, "loop_block", duration=1.20)
        supervisor_pid = server.proc.pid
        child_pid = server.server_pid
        killed_at = time.monotonic()
        # Exact owned Popen handle; harness becomes subreaper for explicit cleanup.
        server.proc.kill()
        server.proc.wait(timeout=.35)
        await server._pump_task
        # The latest issued lease is absolute and cannot outlive kill+1 second.
        cutoff = max(snapshot["lease_until"], killed_at + 1.0,
                     resume_after_expiry["sent_at"] + 1.25)
        await asyncio.sleep(max(0, cutoff - time.monotonic()) + .06)
        checked_at = time.monotonic()
        for _ in range(3):
            rows.append(await query(server, "admin_tenant_b", TOOLS[3], timeout=.5))
        assert all(row["status"] != "allowed" for row in rows)
        assert child_identity(child_pid) is not None
        events = server.events()
        rejected_ids = {row["request_id"] for row in rows}
        assert not any(row.get("event") == "provider_entered" and row.get("request_id") in rejected_ids for row in events)
        snapshots = [row for row in events if row.get("event") == "control_snapshot"
                     and row.get("monotonic", 0) >= checked_at]
        # Event-file data supplements guard/business checks; it is not control authority.
        result = dict(supervisor_pid=supervisor_pid, child_pid=child_pid,
            supervisor_returncode=server.proc.returncode, killed_at=killed_at,
            last_observed_absolute_lease=snapshot["lease_until"], expired_guard_checked_at=checked_at,
            live_work_before=snapshot, before_expiry_resume_query=safe_row(healthy),
            resume_after_expiry_injection=resume_after_expiry,
            retained_query=safe_row(blocked), after_expiry_queries=[safe_row(r) for r in rows],
            after_expiry_provider_entries=0, child_remained_alive=True,
            restoration_claimed=False, event_snapshots=snapshots[-4:], control=server.messages)
    finally:
        cleanup = await server.close()
    result["explicit_harness_failure_cleanup"] = cleanup
    assert cleanup["supervisor_reaped"] and not cleanup["own_server_residual"]
    assert cleanup["adopted_failure_cleanup"]
    return result


async def c20(server):
    from .harness import query, restored, safe_row
    checks = await _lease_channel_checks()
    await _test_ack(server, "noack")
    index = len(server.messages)
    generation = server.generation
    server.command("test", action="recover")
    incident = await server.wait_message(lambda row: row.get("kind") == "incident", 2, index)
    residual_rows = []
    for _ in range(6):
        started = time.monotonic()
        row = await query(server, timeout=.35)
        residual_rows.append(dict(started_at=started, finished_at=time.monotonic(), **safe_row(row)))
        if time.monotonic() - incident["decision_at"] >= .30:
            break
        await asyncio.sleep(.015)
    restoration = await restored(server, generation + 1, fault_at=incident["fault_at"])
    retired = next(row for row in server.messages[index:] if row.get("kind") == "retired"
                   and row.get("child_generation") == generation)
    close_phase = next(row for row in server.messages[index:] if row.get("kind") == "phase"
                      and row.get("phase") == "close_ack")
    assert close_phase["acknowledged"] is False
    assert not any(row.get("kind") == "state" and row.get("state") == "DRAINING"
                   for row in server.messages[index:] if row.get("child_generation") == generation)
    assert retired["reaped"] and retired["process_absent"]
    ids = {row["request_id"] for row in residual_rows}
    entries = [row for row in server.events(generation) if row.get("event") == "provider_entered"
               and row.get("request_id") in ids]
    # The experiment explicitly allows this residual window; measure, never hide it.
    entered_after_decision = [row for row in entries
        if row.get("monotonic_ns", 0) / 1e9 >= incident["decision_at"]]
    cutoff = min(incident["last_lease_until"], retired["finished_at"])
    assert entered_after_decision, "LAB_L6_NOACK_RESIDUAL_WINDOW_NOT_OBSERVED"
    assert all(row["monotonic_ns"] / 1e9 <= cutoff for row in entered_after_decision)
    residual = dict(incident=incident, close_phase=close_phase, retired=retired,
                   candidate_window_seconds=max(0, cutoff-incident["decision_at"]),
                   queries=residual_rows, residual_provider_entries=len(entered_after_decision),
                   entries=entered_after_decision, restoration=restoration,
                   control=server.messages[index:])
    ipc_index = len(server.messages)
    ipc_generation = server.generation
    ipc_fault_at = time.monotonic()
    server.command("test", action="close_control")
    ipc_restoration = await restored(server, ipc_generation + 1, fault_at=ipc_fault_at)
    ipc_control = server.messages[ipc_index:]
    assert any(row.get("kind") == "incident" and row.get("safe_reason") in ("control_lost", "lease_send_failed")
               for row in ipc_control)
    cleanup = await server.close()
    death = await _supervisor_death()
    return dict(absolute_lease_and_backlog=checks, no_ack=residual,
                ipc_loss=dict(restoration=ipc_restoration, control=ipc_control),
                first_supervisor_cleanup=cleanup, supervisor_death=death)


async def c23(server):
    from .harness import query, safe_row, check_result, TOOLS
    before = await server.status()
    block_ack = await _test_ack(server, "observer_block")
    outcomes = []
    try:
        for index in range(12):
            profile = "admin_tenant_b" if index % 2 else "admin_tenant_a"
            value = await query(server, profile, TOOLS[3])
            check_result(value, profile, TOOLS[3])
            outcomes.append(safe_row(value))
        await asyncio.sleep(.28)
        after = await server.status()
        assert after["heartbeat"] > before["heartbeat"]
        assert after["occupancy"] == 0 and after["jobs"] == []
        assert after["attempt"] == before["attempt"] == 0
        assert after["child_generation"] == before["child_generation"]
        assert after["state"] == "READY"
    finally:
        unblock_ack = await _test_ack(server, "observer_unblock")
    await asyncio.sleep(.08)
    ids = {row["request_id"] for row in outcomes}
    finished = [row for row in server.events() if row.get("event") == "worker_finished"
                and row.get("request_id") in ids]
    business_finished = [row for row in finished if row.get("handler_calls") == 1]
    technical_finished = [row for row in finished if row.get("handler_calls") == 0]
    assert len(business_finished) == len(outcomes)
    assert len(technical_finished) == len(outcomes)
    for row in finished:
        assert row["callable_finished_at"] <= row["context_reset_at"] <= row["future_done_at"] <= row["slot_released_at"]
    return dict(before=before, block_ack=block_ack, after_while_diagnostics_blocked=after,
                unblock_ack=unblock_ack, outcomes=outcomes, real_unit_finishes=finished,
                business_units=len(business_finished), technical_list_units=len(technical_finished),
                blocked_diagnostic_thread_separate_from_pool=True)


async def c24(server):
    from .harness import SupervisedProcess
    initial_cleanup = await server.close()
    subcases = []
    for profile in ("process_fail", "channel_fail", "listener_fail"):
        own = SupervisedProcess(profile)
        try:
            unavailable = await own.wait_message(lambda row: row.get("kind") == "state"
                and row.get("state") == "UNAVAILABLE", 9)
            status = await own.status()
            assert not any(row.get("kind") == "ready" for row in own.messages)
            assert status["state"] == "UNAVAILABLE" and status["attempt"] == 0
            assert status["child_pid"] is None and not status["residual"]
            subcase = dict(profile=profile, simulated_resource_failure=True,
                unavailable=unavailable, status=status, control=own.messages)
        finally:
            cleanup = await own.close()
        assert not cleanup["own_server_residual"] and not cleanup["harness_forced"]
        subcase["cleanup"] = cleanup
        subcases.append(subcase)
    return dict(initial_cleanup=initial_cleanup, subcases=subcases,
                host_resources_not_exhausted=True, global_limits_unchanged=True)
