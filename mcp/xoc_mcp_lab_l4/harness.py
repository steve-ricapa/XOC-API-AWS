"""Installed-only L4 Linux acceptance. Fictional assertions, no provider credentials."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


TOOLS = ('xoc.tickets.list', 'xoc.tickets.get', 'xoc.documents.list', 'xoc.notifications.unread_count')


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    return dict(n=len(values), minimum=min(values), median=statistics.median(values),
        p95_nearest_rank=ordered[math.ceil(.95*len(values))-1], maximum=max(values))


def summary(rows):
    return dict(n=len(rows), by_status={status: dict(n=sum(r['status']==status for r in rows),
        seconds=stats([r['total_seconds'] for r in rows if r['status']==status]))
        for status in sorted({r['status'] for r in rows})},
        over_3_5=sum(r['total_seconds']>3.5 for r in rows),
        over_5=sum(r['total_seconds']>5 for r in rows), seconds=stats([r['total_seconds'] for r in rows]))


def process_resources(pid):
    result = dict(pid=pid)
    try:
        status = Path(f'/proc/{pid}/status').read_text()
        for key in ('VmRSS', 'VmHWM', 'Threads'):
            line = next(line for line in status.splitlines() if line.startswith(key+':'))
            result[key] = int(line.split()[1]) * (1024 if key!='Threads' else 1)
        result['fds'] = len(list(Path(f'/proc/{pid}/fd').iterdir()))
        result['tasks'] = len(list(Path(f'/proc/{pid}/task').iterdir()))
        raw = Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()
        result['cpu_ticks'] = int(raw[11])+int(raw[12])
    except (OSError, StopIteration):
        result['unavailable'] = True
    try:
        from xoc_mcp_lab_harness.resources import snapshot
        result['cgroup'] = snapshot()
    except OSError:
        pass
    return result


class ServerProcess:
    """Own one server and files; only this exact child may be terminated."""
    def __init__(self, profile='none'):
        self.directory = Path(tempfile.mkdtemp(prefix='xoc-l4-'))
        self.ready = self.directory/'ready.json'
        self.events_file = self.directory/'events.jsonl'
        self.log = (self.directory/'server.log').open('w+', encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}/mcp'
        self.started = time.monotonic()
        from xoc_mcp_lab.isolation import clean_environment
        self.proc = subprocess.Popen([sys.executable, '-I', '-B', '-m', 'xoc_mcp_lab_l4.server',
            '--port', str(self.port), '--ready-file', str(self.ready), '--events-file', str(self.events_file),
            '--fault-profile', profile], cwd=tempfile.gettempdir(), env=clean_environment(),
            stdout=self.log, stderr=self.log)
        self.close_evidence = None

    async def wait_ready(self):
        end = time.monotonic()+15
        while time.monotonic()<end:
            if self.ready.exists():
                try:
                    self.readiness = json.loads(self.ready.read_text())
                    self.startup_seconds = time.monotonic()-self.started
                    return self
                except json.JSONDecodeError:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError('LAB_L4_SERVER_START_FAILED')
            await asyncio.sleep(.01)
        raise TimeoutError('LAB_L4_SERVER_NOT_READY')

    def events(self):
        if not self.events_file.exists():
            return []
        rows = []
        for line in self.events_file.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return rows

    async def wait_event(self, request_id, *, count=1, timeout=3):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            found = [e for e in self.events() if e.get('request_id')==request_id and e.get('event')=='provider_entered']
            if len(found)>=count:
                return found
            await asyncio.sleep(.005)
        raise AssertionError('LAB_FAULT_NOT_ENTERED')

    def close(self):
        if self.close_evidence is not None:
            return self.close_evidence
        start = time.monotonic()
        before = process_resources(self.proc.pid)
        forced = False
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=.65)
            except subprocess.TimeoutExpired:
                forced = True
                self.proc.kill()
                self.proc.wait(timeout=1)
        self.log.close()
        self.close_evidence = dict(global_shutdown_seconds=time.monotonic()-start,
            pid=self.proc.pid, returncode=self.proc.returncode, forced_kill=forced,
            process_absent=not Path(f'/proc/{self.proc.pid}').exists() if sys.platform=='linux' else self.proc.poll() is not None,
            before=before, global_impact='terminates every in-flight request in this server')
        return self.close_evidence


def make_envelope(profile='admin_tenant_a', name=TOOLS[0], *, prefix=None, args=None, patch=None):
    from xoc_mcp_lab_harness.signing import envelope
    if args is None:
        args = {'ticket_id': 'lab-ticket-b1' if profile=='admin_tenant_b' else 'lab-ticket-a1'} if name==TOOLS[1] else {}
    claims = dict(patch or {})
    if prefix:
        claims['request_id'] = 'lab-'+prefix+'-'+uuid4().hex[:12]
    value = envelope(profile, name, args, claims_patch=claims)
    if 'request_id' in claims:
        value['request_id'] = claims['request_id']
    return value


async def query(server, profile='admin_tenant_a', name=TOOLS[0], *, prefix=None, timeout=3.5, args=None, patch=None):
    from .client import QueryClient
    value = make_envelope(profile, name, prefix=prefix, args=args, patch=patch)
    result = await QueryClient(server.url).run(value, timeout=timeout)
    result['request_id'] = value['request_id']
    result['tool'] = name
    return result


def safe_row(result):
    return {k:v for k,v in result.items() if k not in ('result', 'tools')}


def check_result(result, profile, name):
    assert result['status']=='allowed', ('LAB_EXPECTED_ALLOWED', result.get('code'))
    payload = result['result']
    serialized = json.dumps(payload)
    assert 'LAB_CANARY_NEVER_PUBLIC' not in serialized
    if name==TOOLS[3]:
        expected = {'user_a1':2, 'user_a2':5, 'admin_tenant_a':2, 'admin_tenant_b':3}[profile]
        assert payload['data']['count']==expected
    if name in TOOLS[:3]:
        forbidden = 'lab-ticket-a' if profile=='admin_tenant_b' else 'lab-ticket-b'
        assert forbidden not in serialized
        forbidden = 'lab-document-a' if profile=='admin_tenant_b' else 'lab-document-b'
        assert forbidden not in serialized


async def smoke():
    from .client import QueryClient
    server = ServerProcess('mixed')
    rows = []
    try:
        await server.wait_ready()
        value = make_envelope()
        first = await QueryClient(server.url).run(value, include_list=True)
        assert first['status']=='allowed' and set(first['tools'])==set(TOOLS)
        rows.append(safe_row(first))
        for profile in ('user_a1','admin_tenant_b','user_a2','user_a1'):
            for name in TOOLS:
                result = await query(server, profile, name)
                check_result(result, profile, name)
                rows.append(safe_row(result))
        for profile in ('superadmin', 'admin_xoc_without_delegation'):
            result = await query(server, profile)
            assert result['status']=='denied'
            rows.append(safe_row(result))
        delegated = await query(server, 'admin_xoc_delegated_a')
        assert delegated['status']=='allowed'
        rows.append(safe_row(delegated))
        for name,args in (('xoc.evil.execute',{}),('xoc.dashboard.summary',{}),
                ('xoc.integrations.summary',{}),('xoc.tickets.list',{'tenantId':91002}),
                ('xoc.tickets.get',{'ticket_id':'lab-ticket-b1'})):
            result = await query(server, name=name, args=args)
            assert result['status']!='allowed'
            rows.append(safe_row(result))
        repeated = QueryClient(server.url)
        assert (await repeated.run(value))['status']=='allowed'
        assert (await repeated.run(value))['code']=='lab_call_limit'
        # A new independent wrapper may use the same still-valid signed assertion.
        assert (await QueryClient(server.url).run(value))['status']=='allowed'
        return dict(status='passed', rows=rows, summary=summary(rows), events=server.events(),
            startup_seconds=server.startup_seconds)
    finally:
        cleanup = server.close()
        if 'rows' in locals():
            smoke.last_cleanup = cleanup


async def benchmark():
    # Same actual C1 QueryWorker bytes, envelope profiles, tools, args and 3.5s
    # budget as L4. Sequential phase; no builds or other own workloads overlap.
    from xoc_mcp_lab.query_worker import QueryWorker
    from xoc_mcp_lab_harness.observe import ProcessObserver, audit_events
    c1_rows = []
    for name in TOOLS:
        for i in range(20):
            profile = ('admin_tenant_a','admin_tenant_b','user_a2','user_a1')[i%4]
            value = make_envelope(profile,name)
            worker = QueryWorker()
            with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as log, ProcessObserver() as observed:
                start = time.monotonic()
                result = await worker.run(value, timeout_seconds=3.5, errlog=log)
                total = time.monotonic()-start
                events = audit_events(log)
                processes = observed.evidence()
            payload = result.get('result',{})
            status = payload.get('status') if result['status']=='completed' else result['status']
            if any(item.get('timeout') for item in worker.lifecycle):
                status = 'timeout'
            if status=='allowed':
                check_result(dict(status=status,result=payload),profile,name)
            c1_rows.append(dict(status=status,worker_status=result['status'],code=result.get('code'),tool=name,total_seconds=total,
                measurements=worker.measurements, lifecycle=worker.lifecycle, processes=processes,
                handlers=sum(e['handler_calls'] for e in events),reads=sum(e['reads'] for e in events)))
        print(json.dumps({'l4_progress':'c1_comparable','n':len(c1_rows)}),flush=True)
    c1_concurrent_workers = [QueryWorker() for _ in range(4)]
    c1_concurrent_values = [make_envelope(profile,TOOLS[3])
        for profile in ('user_a1','admin_tenant_b','user_a2','user_a1')]
    with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as log, ProcessObserver() as observed:
        async def c1_parallel(worker,value):
            began=time.monotonic()
            result=await worker.run(value,timeout_seconds=3.5,errlog=log)
            status=result.get('result',{}).get('status') if result['status']=='completed' else result['status']
            if any(item.get('timeout') for item in worker.lifecycle):
                status='timeout'
            return dict(status=status,worker_status=result['status'],code=result.get('code'),
                total_seconds=time.monotonic()-began, lifecycle=worker.lifecycle)
        c1_pressure_before=process_resources(os.getpid())
        c1_parallel_rows=await asyncio.gather(*[c1_parallel(worker,value)
            for worker,value in zip(c1_concurrent_workers,c1_concurrent_values)])
        c1_parallel_processes=observed.evidence()
        c1_pressure_after=process_resources(os.getpid())
    cold = []
    for _ in range(5):
        server = ServerProcess()
        try:
            await server.wait_ready()
            result = await query(server)
            cold.append(dict(startup_seconds=server.startup_seconds, first=safe_row(result),
                start_through_first_seconds=time.monotonic()-server.started))
        finally:
            close = server.close()
        cold[-1]['cleanup'] = close
        cold[-1]['full_lifecycle_seconds'] = time.monotonic()-server.started
    server = ServerProcess('mixed')
    sequential, concurrent, large, rounds, denials = [], [], [], [], []
    try:
        await server.wait_ready()
        resources_before = process_resources(server.proc.pid)
        for name in TOOLS:
            for i in range(20):
                profile = ('admin_tenant_a','admin_tenant_b','user_a2','user_a1')[i%4]
                result = await query(server, profile, name)
                if result['status']=='allowed':
                    check_result(result, profile, name)
                sequential.append(safe_row(result))
            print(json.dumps({'l4_progress': 'sequential', 'n':len(sequential)}), flush=True)
        concurrent = [safe_row(r) for r in await asyncio.gather(*[
            query(server, ('user_a1','admin_tenant_b','user_a2','user_a1')[i], TOOLS[3]) for i in range(4)])]
        for profile in ('superadmin','admin_xoc_without_delegation'):
            denials.append(safe_row(await query(server, profile)))
        denials.append(safe_row(await query(server, name='xoc.evil.execute')))
        after_denials = safe_row(await query(server))
        from .client import QueryClient
        finite_value = make_envelope(prefix='fault-slow')
        finite_task = asyncio.create_task(QueryClient(server.url).run(finite_value, timeout=.3))
        await server.wait_event(finite_value['request_id'])
        finite_b = await query(server,'admin_tenant_b',TOOLS[3])
        finite_a = await finite_task
        finish_deadline = time.monotonic()+4
        finite_finish = []
        while not finite_finish and time.monotonic()<finish_deadline:
            finite_finish = [e for e in server.events() if e.get('event')=='worker_finished'
                and e.get('request_id')==finite_value['request_id']]
            await asyncio.sleep(.005)
        assert finite_finish, 'LAB_FINITE_FAULT_NOT_CLEANED'
        finite_fault = dict(a=safe_row(finite_a), b=safe_row(finite_b),
            actual_worker_finished=finite_finish[-1], after_recovery=safe_row(await query(server)))
        for _ in range(3):
            large.append(safe_row(await query(server, prefix='large')))
        # Five rounds, forty independent requests each, SAME server and no restart.
        for n in range(5):
            before = process_resources(server.proc.pid)
            rows = []
            for i in range(40):
                profile = ('user_a1','admin_tenant_b','user_a2','user_a1')[i%4]
                result = await query(server, profile, TOOLS[i%4], prefix='fault-error' if i%13==0 else None)
                rows.append(safe_row(result))
            rounds.append(dict(round=n+1, before=before, after=process_resources(server.proc.pid),
                rows=rows, summary=summary(rows)))
            print(json.dumps({'l4_progress':'stability','round':n+1}), flush=True)
        main_events = server.events()
        resources_after = process_resources(server.proc.pid)
    finally:
        main_cleanup = server.close()
    # Blocking deliberately ignores deadline and cancellation. Only own server kill clears it.
    blocked = ServerProcess('mixed')
    try:
        await blocked.wait_ready()
        from .client import QueryClient
        value = make_envelope(prefix='fault-block')
        task = asyncio.create_task(QueryClient(blocked.url).run(value, timeout=1))
        entered = await blocked.wait_event(value['request_id'])
        b = await query(blocked, 'admin_tenant_b', TOOLS[3])
        a = await task
        # All four slots occupied by actual provider calls; fifth must not enter.
        values = [make_envelope(prefix='fault-block') for _ in range(3)]
        pending = [asyncio.create_task(QueryClient(blocked.url).run(v, timeout=1)) for v in values]
        for value_i in values:
            await blocked.wait_event(value_i['request_id'])
        fifth = await query(blocked)
        others = await asyncio.gather(*pending)
        fault = dict(provider_entered=entered, a=safe_row(a), b=safe_row(b),
            other_blocked=[safe_row(r) for r in others], fifth=safe_row(fifth),
            before_kill=process_resources(blocked.proc.pid), events=blocked.events(),
            timeout_does_not_stop_thread=True)
    finally:
        blocked_cleanup = blocked.close()
    fault['cleanup'] = blocked_cleanup
    return dict(lab_only=True, c1_comparable=c1_rows,c1_comparable_summary=summary(c1_rows),
        c1_concurrent=c1_parallel_rows,c1_concurrent_summary=summary(c1_parallel_rows),
        c1_concurrent_processes=c1_parallel_processes,
        c1_pressure_before=c1_pressure_before,c1_pressure_after=c1_pressure_after,
        cold=cold, startup=stats([r['startup_seconds'] for r in cold]),
        sequential=sequential, sequential_summary=summary(sequential),
        concurrent=concurrent, concurrent_summary=summary(concurrent),
        denied=denials, availability_after_denials=after_denials, large=large, large_summary=summary(large),
        stability=rounds, faults=fault, finite_fault=finite_fault,
        resources_before=resources_before, resources_after=resources_after,
        events=main_events, cleanup=main_cleanup,
        limitations=['No cloud SLA; host shared with unrelated containers',
            'RSS/cgroup snapshots, not exhaustive allocation profiling',
            'Stateless cancellation notification does not reach another POST session',
            'A blocked thread retains slot/context until completion or global process kill'])


async def provenance():
    from xoc_mcp_lab_harness.equivalence import cases
    from xoc_mcp_lab.installed import import_evidence
    from .wiring import PersistentWiring
    actual = cases()
    expected = json.loads(Path('/offline/reference.json').read_text())
    assert actual == expected, 'LAB_C1_REFERENCE_DIFFERENCE'
    with PersistentWiring():
        from xoc_mcp_lab.adapter import Adapter
        origins = import_evidence()
    server = ServerProcess()
    comparisons = []
    try:
        await server.wait_ready()
        for case in actual['cases']:
            if case['profile']=='invalid_context':
                # L1 deliberately rejects these null nonidentities before gateway.
                comparisons.append(dict(profile=case['profile'], name=case['name'],
                    comparison='not_equivalent_by_design_invalid_L1_identity', no_business=True))
                continue
            result = await query(server,case['profile'],case['name'],args=case['arguments'])
            normalized = {k:v for k,v in result.get('result',{}).items() if k not in ('request_id','audit_id')}
            assert normalized == case['result'], ('LAB_HTTP_PROJECTION_DIFFERENCE',case['profile'],case['name'])
            comparisons.append(dict(profile=case['profile'],name=case['name'],comparison='identical'))
        return dict(canonical_origins=origins,all_installed=all('site-packages' in r['file'] for r in origins),
            canonical_equivalence_cases=len(actual['cases']), http_comparisons=comparisons,
            http_identical=sum(r['comparison']=='identical' for r in comparisons),
            l1_invalid_identity_cases=sum(r['comparison']!='identical' for r in comparisons))
    finally:
        provenance.last_cleanup=server.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('tests','smoke','benchmark','provenance'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    from .integrity import require_l4_installed
    require_l4_installed()
    from xoc_mcp_lab_harness.linux_validation import environment
    env = environment()
    assert sys.version_info[:3] == (3,11,16)
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    code = 0
    with Isolation():
        configure_logs()
        if args.mode in ('smoke','benchmark'):
            result = asyncio.run(smoke() if args.mode=='smoke' else benchmark())
            if args.mode=='smoke':
                result['cleanup'] = smoke.last_cleanup
        elif args.mode=='tests':
            import pytest
            import xoc_mcp_lab_harness
            from xoc_mcp_lab import testing
            from xoc_mcp_lab.installed import require_installed
            testing.BACKEND = require_installed()
            rows = []
            class Reporter:
                def pytest_runtest_logreport(self, report):
                    if report.when=='call' or report.failed or report.skipped:
                        rows.append(dict(nodeid=report.nodeid, when=report.when, outcome=report.outcome,
                            duration=report.duration))
            # Run the unchanged 212 C1 tests and new L4 tests together against
            # installed wheels in the candidate image, not merely separately.
            c1_tests = Path(xoc_mcp_lab_harness.__file__).parent/'tests'
            code = int(pytest.main(['-q','--tb=short','-p','no:cacheprovider',
                str(c1_tests), str(Path(__file__).parent/'tests')], plugins=[Reporter()]))
            result = dict(exit_code=code, tests=rows, passed=sum(r['outcome']=='passed' for r in rows),
                failed=sum(r['outcome']=='failed' for r in rows), skipped=sum(r['outcome']=='skipped' for r in rows),
                suite='unchanged C1 plus L4 together in installed candidate',
                c1_passed=sum(r['outcome']=='passed' and 'xoc_mcp_lab_harness/' in r['nodeid'] for r in rows),
                l4_passed=sum(r['outcome']=='passed' and 'xoc_mcp_lab_l4/' in r['nodeid'] for r in rows))
        else:
            result = asyncio.run(provenance())
            result['cleanup'] = provenance.last_cleanup
    children = Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children').read_text().strip()
    result.update(environment=env, final_children=children, final_resources=process_resources(os.getpid()))
    assert not children, 'LAB_CHILDREN_REMAIN'
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'l4_mode':args.mode, 'exit_code':code, 'children':children}), flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
