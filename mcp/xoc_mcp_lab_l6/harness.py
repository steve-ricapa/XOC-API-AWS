"""Installed L6 acceptance; own process tree, fictional context, no external I/O."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

from xoc_mcp_lab_l4.harness import (
    TOOLS, ServerProcess, make_envelope, process_resources,
    query, safe_row, stats, summary,
)
from .validation import validate_fixture_result as check_result, validate_large_result


def cgroup_extra():
    result = {}
    for name in ('memory.current', 'memory.peak', 'memory.max', 'memory.events',
                 'pids.current', 'pids.peak', 'pids.max', 'pids.events', 'cpu.max', 'cpu.stat',
                 'memory/memory.usage_in_bytes', 'memory/memory.max_usage_in_bytes',
                 'memory/memory.limit_in_bytes', 'memory/memory.failcnt', 'memory/memory.oom_control',
                 'pids/pids.current', 'pids/pids.max', 'pids/pids.events',
                 'cpu/cpu.cfs_quota_us', 'cpu/cpu.cfs_period_us'):
        path = Path('/sys/fs/cgroup') / name
        if path.is_file():
            result[name] = path.read_text().strip()
    return result


def resource_violations(samples):
    errors = set()
    for sample in samples:
        extra = sample['resources'].get('cgroup_extra', {})
        for key in ('memory.current', 'memory.peak', 'memory/memory.usage_in_bytes',
                    'memory/memory.max_usage_in_bytes'):
            if key in extra and int(extra[key]) > 512*1024*1024:
                errors.add('memory_budget')
        for key in ('pids.current', 'pids.peak', 'pids/pids.current'):
            if key in extra and int(extra[key]) > 128:
                errors.add('pids_budget')
        if int(extra.get('memory/memory.failcnt', '0')):
            errors.add('memory_failcnt')
        for key in ('memory.events', 'memory/memory.oom_control'):
            values = dict(line.split() for line in extra.get(key, '').splitlines())
            if int(values.get('oom_kill', '0')) or int(values.get('under_oom', '0')):
                errors.add('oom')
    return sorted(errors)


class ResourceSampler:
    """Identical 250ms harness sampling on both candidates, bounded history."""
    def __init__(self, server):
        self.server = server
        self.rows = deque(maxlen=1024)
        self.task = asyncio.create_task(self.run())

    async def run(self):
        while True:
            if isinstance(self.server, SupervisedProcess):
                resources = self.server.resources()
                state = dict(self.server.latest)
            else:
                resources = dict(harness=process_resources(os.getpid()), supervisor=None,
                    server=process_resources(self.server.proc.pid), cgroup_extra=cgroup_extra())
                state = {}
            self.rows.append(dict(at=time.monotonic(), resources=resources, control=state))
            await asyncio.sleep(.25)

    async def stop(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        return list(self.rows)


def child_identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        return {'pid': pid, 'ppid': int(fields[1]), 'start_ticks': int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


def enable_subreaper():
    """Only this harness adopts descendants if its own supervisor is killed."""
    if sys.platform != 'linux':
        raise RuntimeError('LAB_L6_LINUX_REQUIRED')
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError('LAB_L6_SUBREAPER_REQUIRED')


class SupervisedProcess:
    """Harness owns supervisor; supervisor alone owns/replaces its MCP child."""

    def __init__(self, profile='none'):
        from .control import ControlChannel
        from xoc_mcp_lab.isolation import clean_environment
        enable_subreaper()
        self.directory = Path(tempfile.mkdtemp(prefix='xoc-l6-'))
        self.events_file = self.directory / 'server.jsonl'
        self.log = (self.directory / 'supervisor.log').open('w+', encoding='utf-8')
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1', 0))
            self.port = port_socket.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}/mcp'
        parent, child = socket.socketpair()
        self.channel = ControlChannel(parent, generation=1)
        self.started = time.monotonic()
        try:
            self.proc = subprocess.Popen([
                sys.executable, '-I', '-B', '-m', 'xoc_mcp_lab_l6.supervisor',
                '--control-fd', str(child.fileno()), '--port', str(self.port),
                '--events-file', str(self.events_file), '--fault-profile', profile,
            ], pass_fds=(child.fileno(),), cwd=tempfile.gettempdir(),
                env=clean_environment(), stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=self.log)
        except BaseException:
            self.channel.close()
            self.log.close()
            raise
        finally:
            child.close()
        self.messages = []
        self.owned_children = {}
        self.latest = {}
        self.generation = 0
        self.server_pid = None
        self.close_evidence = None
        self._pump_task = asyncio.create_task(self._pump())

    def _remember(self, message):
        row = dict(message, received_at=time.monotonic())
        if len(self.messages) >= 8192:
            raise RuntimeError('LAB_L6_HARNESS_CONTROL_LIMIT')
        self.messages.append(row)
        for key in ('state', 'child_generation', 'child_pid', 'occupancy',
                    'jobs', 'heartbeat', 'lease_until', 'attempt'):
            if key in message:
                self.latest[key] = message[key]
        pid = message.get('child_pid')
        if type(pid) is int and pid > 0 and pid not in self.owned_children:
            identity = child_identity(pid)
            if identity and identity['ppid'] == self.proc.pid:
                self.owned_children[pid] = identity
        return row

    async def _pump(self):
        while True:
            for message in self.channel.receive():
                self._remember(message)
            if self.proc.poll() is not None:
                for message in self.channel.receive():
                    self._remember(message)
                return
            await asyncio.sleep(.005)

    def command(self, kind, **fields):
        if self.channel.send(kind, **fields) is None:
            raise RuntimeError('LAB_L6_CONTROL_COMMAND_NOT_SENT')

    async def wait_message(self, predicate, timeout=16, after=0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for row in self.messages[after:]:
                if predicate(row):
                    return row
            if self._pump_task.done():
                self._pump_task.result()
                raise RuntimeError('LAB_L6_SUPERVISOR_EXITED')
            await asyncio.sleep(.005)
        raise TimeoutError('LAB_L6_EXPECTED_CONTROL_EVENT')

    async def wait_ready(self, generation=1):
        row = await self.wait_message(lambda r: r.get('kind') == 'ready'
            and r.get('child_generation', 0) >= generation, timeout=18)
        self.generation = row['child_generation']
        self.server_pid = row['child_pid']
        self.startup_seconds = time.monotonic() - self.started
        self.readiness = row
        return self

    async def status(self):
        index = len(self.messages)
        self.command('status')
        return await self.wait_message(lambda r: r.get('kind') == 'status', 2, index)

    def events(self, generation=None):
        rows = []
        paths = sorted(self.directory.glob('server.g*.jsonl'))
        if self.events_file.exists():
            paths.append(self.events_file)
        for path in paths:
            if generation is not None and f'.g{generation}.' not in path.name:
                continue
            try:
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            rows.append(row)
                    except json.JSONDecodeError:
                        pass  # Final partially flushed line is retried, not parsed as evidence.
            except FileNotFoundError:
                pass
        return rows

    async def wait_event(self, request_id, event='provider_entered', timeout=4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            rows = [r for r in self.events() if r.get('request_id') == request_id
                    and r.get('event') == event]
            if rows:
                return rows
            await asyncio.sleep(.005)
        raise AssertionError('LAB_L6_EXPECTED_REAL_WORK_EVENT')

    async def wait_idle(self, timeout=4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            row = await self.status()
            if row.get('occupancy') == 0 and not row.get('jobs'):
                return row
            await asyncio.sleep(.03)
        raise AssertionError('LAB_L6_EXPECTED_IDLE_CAPACITY')

    def resources(self):
        pid = self.latest.get('child_pid', self.server_pid)
        return {'harness': process_resources(os.getpid()),
                'supervisor': process_resources(self.proc.pid),
                'server': process_resources(pid) if pid else None,
                'cgroup_extra': cgroup_extra()}

    async def _reap_adopted(self):
        """Failure cleanup only, NOT credited as implemented recovery."""
        records = []
        for pid, expected in self.owned_children.items():
            identity = child_identity(pid)
            if identity is None:
                continue
            if identity['start_ticks'] != expected['start_ticks'] or identity['ppid'] != os.getpid():
                raise RuntimeError('LAB_L6_ORPHAN_OWNERSHIP_NOT_PROVEN')
            began = time.monotonic()
            os.kill(pid, signal.SIGTERM)
            reaped = False
            until = began + .65
            while time.monotonic() < until:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    reaped = True
                    break
                await asyncio.sleep(.005)
            forced = not reaped
            if not reaped:
                identity = child_identity(pid)
                if identity is None or identity['start_ticks'] != expected['start_ticks']:
                    raise RuntimeError('LAB_L6_OWNED_PROCESS_CHANGED')
                os.kill(pid, signal.SIGKILL)
                until = time.monotonic() + .35
                while time.monotonic() < until:
                    done, status = os.waitpid(pid, os.WNOHANG)
                    if done:
                        reaped = True
                        break
                    await asyncio.sleep(.005)
            records.append(dict(pid=pid, reaped=reaped, forced=forced,
                                seconds=time.monotonic()-began,
                                process_absent=child_identity(pid) is None))
        return records

    async def close(self):
        if self.close_evidence is not None:
            return self.close_evidence
        began = time.monotonic()
        harness_forced = False
        if self.proc.poll() is None:
            if not self.channel.closed:
                self.command('stop')
            until = time.monotonic()+5
            while self.proc.poll() is None and time.monotonic() < until:
                await asyncio.sleep(.01)
            if self.proc.poll() is None:
                harness_forced = True
                self.proc.terminate()
                until = time.monotonic()+2
                while self.proc.poll() is None and time.monotonic() < until:
                    await asyncio.sleep(.01)
            if self.proc.poll() is None:
                self.proc.kill()
        self.proc.wait(timeout=1)
        pump_error = None
        try:
            await self._pump_task
        except Exception as error:
            pump_error = type(error).__name__
        adopted = await self._reap_adopted()
        self.channel.close()
        self.log.close()
        residual = [pid for pid in self.owned_children if child_identity(pid) is not None]
        self.close_evidence = dict(supervisor_pid=self.proc.pid, returncode=self.proc.returncode,
            supervisor_reaped=True, supervisor_absent=child_identity(self.proc.pid) is None,
            own_server_residual=residual, harness_forced=harness_forced,
            adopted_failure_cleanup=adopted, control_pump_error=pump_error,
            seconds=time.monotonic()-began)
        return self.close_evidence


async def restored(server, generation, *, fault_at):
    await server.wait_ready(generation)
    ready = server.readiness
    begun = time.monotonic()
    result = await query(server, 'admin_tenant_b', TOOLS[3])
    valid = result.get('status') == 'allowed'
    if valid:
        try:
            check_result(result, 'admin_tenant_b', TOOLS[3])
        except AssertionError:
            valid = False
    if not valid:
        server.command('fail_closed', safe_reason='restore_failed')
        await server.wait_message(lambda r: r.get('kind') == 'state'
            and r.get('state') == 'UNAVAILABLE', 4)
    ready_at = ready.get('ready_at', ready.get('monotonic_at', ready['received_at']))
    row = dict(new_query=safe_row(result), new_generation=server.generation,
        public_query_started_at=begun, ready_at=ready_at,
        dispatch_after_ready=begun-ready_at,
        restore_seconds=time.monotonic()-fault_at,
        new_network_query=True, correct=valid)
    assert valid, 'LAB_L6_NEW_NETWORK_QUERY_FAILED'
    assert result['total_seconds'] <= 3.5, 'LAB_L6_NEW_QUERY_BUDGET'
    assert row['dispatch_after_ready'] <= .25, 'LAB_L6_RESTORE_DISPATCH_BUDGET'
    assert row['restore_seconds'] <= 21, 'LAB_L6_RESTORE_BUDGET'
    return row


async def population(candidate=True):
    """Same populations and client instrumentation for original L4 and L6."""
    population_started_at = time.monotonic()
    cold = []
    factory = SupervisedProcess if candidate else ServerProcess
    async def stop(server):
        return await server.close() if candidate else server.close()
    for _ in range(5):
        server = factory()
        cold_sampler = ResourceSampler(server)
        deterministic = []
        expected_identity = None
        def capture_cold(point):
            from .observability import capture
            pid = server.server_pid if candidate else server.proc.pid
            return capture(pid, server.generation if candidate else 1,
                           server.proc.pid if candidate else os.getpid(), expected_identity,
                           point, None if candidate else server.proc.poll())
        try:
            await server.wait_ready()
            from .observability import identity
            pid = server.server_pid if candidate else server.proc.pid
            expected_identity = (server.owned_children.get(pid) if candidate else identity(pid))
            deterministic.append(capture_cold('after_ready'))
            result = await query(server)
            deterministic.append(capture_cold('after_first_cold_query'))
            check_result(result, 'admin_tenant_a', TOOLS[0])
            cold.append(dict(startup_seconds=server.startup_seconds,
                             first=safe_row(result),
                             start_through_first_seconds=time.monotonic()-server.started))
        finally:
            if expected_identity is not None:
                deterministic.append(capture_cold('before_retirement'))
            cleanup = await stop(server)
            cold_samples = await cold_sampler.stop()
        cold[-1]['cleanup'] = cleanup
        # Retain every cold generation's measured phases, including retirement.
        # READY alone is not evidence of each phase's time/resource budget.
        # Both sides use the same sampler and diagnostic collection placement.
        cold[-1]['control'] = list(server.messages) if candidate else []
        cold[-1]['events'] = server.events()
        cold[-1]['resource_samples'] = cold_samples
        cold[-1]['deterministic_process_samples'] = deterministic
    server = factory('mixed')
    sampler = ResourceSampler(server)
    try:
        await server.wait_ready()
        pid = server.server_pid if candidate else server.proc.pid
        before = server.resources() if candidate else process_resources(pid)
        sequential = []
        for name in TOOLS:
            for i in range(20):
                profile = ('admin_tenant_a', 'admin_tenant_b', 'user_a2', 'user_a1')[i % 4]
                result = await query(server, profile, name)
                if result['status'] == 'allowed':
                    check_result(result, profile, name)
                sequential.append(safe_row(result))
        concurrent_results = await asyncio.gather(*[
            query(server, profile, TOOLS[3])
            for profile in ('user_a1', 'admin_tenant_b', 'user_a2', 'user_a1')])
        for profile, row in zip(('user_a1', 'admin_tenant_b', 'user_a2', 'user_a1'), concurrent_results):
            if row['status'] == 'allowed':
                check_result(row, profile, TOOLS[3])
        concurrent = [safe_row(row) for row in concurrent_results]
        large = []
        for _ in range(3):
            row = await query(server, prefix='large')
            if row['status'] == 'allowed':
                validate_large_result(row)
            large.append(safe_row(row))
        rounds = []
        for number in range(5):
            resources_before = server.resources() if candidate else process_resources(pid)
            rows = []
            for i in range(40):
                profile = ('user_a1', 'admin_tenant_b', 'user_a2', 'user_a1')[i % 4]
                row = await query(server, profile, TOOLS[i % 4],
                    prefix='fault-error' if i % 13 == 0 else None)
                if row['status'] == 'allowed':
                    check_result(row, profile, TOOLS[i % 4])
                rows.append(safe_row(row))
            rounds.append(dict(round=number+1, rows=rows, summary=summary(rows),
                before=resources_before,
                after=server.resources() if candidate else process_resources(pid),
                control=await server.status() if candidate else None))
        result = dict(candidate='L6' if candidate else 'L4', cold=cold,
            startup=stats([r['startup_seconds'] for r in cold]),
            sequential=sequential, sequential_summary=summary(sequential),
            concurrent=concurrent, concurrent_summary=summary(concurrent),
            large=large, large_summary=summary(large), stability=rounds,
            resources_before=before,
            resources_after=server.resources() if candidate else process_resources(pid),
            events=server.events(), control=server.messages if candidate else [],
            cgroup_extra=cgroup_extra())
    finally:
        cleanup = await stop(server)
        samples = await sampler.stop()
    result['cleanup'] = cleanup
    result['resource_samples'] = samples
    errors = []
    normal = sequential + concurrent + large + [r['first'] for r in cold]
    for population_name, rows, count in (('hot', sequential, 80), ('concurrent', concurrent, 4), ('large', large, 3)):
        if len(rows) != count or any(r['status'] != 'allowed' for r in rows):
            errors.append(population_name + '_outcomes')
    if any(r['total_seconds'] > 3.5 for r in normal):
        errors.append('normal_request_budget')
    for round_result in rounds:
        for index, row in enumerate(round_result['rows']):
            expected = 'error' if index % 13 == 0 else 'allowed'
            if row['status'] != expected or (expected == 'error' and row['code'] != 'execution_failed'):
                errors.append('stability_outcomes')
            if row['total_seconds'] > 3.5:
                errors.append('stability_request_budget')
    errors.extend(resource_violations(samples))
    for cold_row in cold:
        errors.extend(resource_violations(cold_row['resource_samples']))
    result['acceptance_errors'] = sorted(set(errors))
    result['population_passed'] = not errors
    result['population_started_at'] = population_started_at
    result['population_finished_at'] = time.monotonic()
    return result


async def provenance():
    from xoc_mcp_lab_harness.equivalence import cases
    from xoc_mcp_lab.installed import import_evidence
    from xoc_mcp_lab_l4.wiring import PersistentWiring
    actual = cases()
    reference = json.loads(Path('/offline/reference.json').read_text())
    assert actual == reference, 'LAB_L6_CANONICAL_DRIFT'
    with PersistentWiring():
        origins = import_evidence()
    server = SupervisedProcess()
    comparisons = []
    try:
        await server.wait_ready()
        for case in actual['cases']:
            if case['profile'] == 'invalid_context':
                comparisons.append(dict(profile=case['profile'], name=case['name'],
                    comparison='invalid_L1_identity_no_business'))
                continue
            value = await query(server, case['profile'], case['name'], args=case['arguments'])
            normalized = {k: v for k, v in value.get('result', {}).items()
                          if k not in ('request_id', 'audit_id')}
            assert normalized == case['result'], 'LAB_L6_PROJECTION_MISMATCH'
            comparisons.append(dict(profile=case['profile'], name=case['name'], comparison='identical'))
        result = dict(canonical_origins=origins, comparisons=comparisons,
            canonical_equivalence_cases=len(actual['cases']),
            all_installed=all('site-packages' in row['file'] for row in origins),
            control=server.messages)
    finally:
        cleanup = await server.close()
    result['cleanup'] = cleanup
    return result


def tests():
    import pytest
    import xoc_mcp_lab_harness
    import xoc_mcp_lab_l4
    from xoc_mcp_lab import testing
    from xoc_mcp_lab.installed import require_installed
    testing.BACKEND = require_installed()
    rows = []
    collected_nodeids = []
    class Reporter:
        def pytest_collection_finish(self, session):
            collected_nodeids.extend(item.nodeid for item in session.items)

        def pytest_runtest_logreport(self, report):
            if report.when == 'call' or report.failed or report.skipped:
                rows.append(dict(nodeid=report.nodeid, when=report.when,
                                 outcome=report.outcome, duration=report.duration))
    paths = [Path(module.__file__).parent / 'tests'
             for module in (xoc_mcp_lab_harness, xoc_mcp_lab_l4)]
    paths.append(Path(__file__).parent / 'tests')
    code = int(pytest.main(['-q', '--tb=short', '-p', 'no:cacheprovider',
                           *map(str, paths)], plugins=[Reporter()]))
    return dict(exit_code=code, tests=rows, collected_nodeids=collected_nodeids,
        suite='Unmodified 212 C1 + 92 L4 + additive L6 together, installed candidate',
        passed=sum(r['outcome']=='passed' for r in rows),
        failed=sum(r['outcome']=='failed' for r in rows),
        skipped=sum(r['outcome']=='skipped' for r in rows),
        c1_passed=sum(r['outcome']=='passed' and 'xoc_mcp_lab_harness/' in r['nodeid'] for r in rows),
        l4_passed=sum(r['outcome']=='passed' and 'xoc_mcp_lab_l4/' in r['nodeid'] for r in rows),
        l6_passed=sum(r['outcome']=='passed' and 'xoc_mcp_lab_l6/' in r['nodeid'] for r in rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True,
        choices=('tests', 'smoke', 'recovery', 'benchmark', 'benchmark-l4', 'provenance'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('LAB_L6_NEW_EVIDENCE_REQUIRED')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    from .integrity import require_l6_installed
    require_l6_installed()
    from xoc_mcp_lab_harness.linux_validation import environment
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    env = environment()
    if sys.version_info[:3] != (3, 11, 16):
        raise RuntimeError('LAB_L6_PINNED_PYTHON_REQUIRED')
    code = 0
    with Isolation():
        configure_logs()
        if args.mode == 'tests':
            result = tests()
            code = result['exit_code']
        elif args.mode in ('smoke', 'recovery'):
            from .acceptance import run_cases
            result = asyncio.run(run_cases(['C04'] if args.mode == 'smoke' else None))
            code = int(not result['all_passed'])
        elif args.mode == 'provenance':
            result = asyncio.run(provenance())
        else:
            result = asyncio.run(population(args.mode == 'benchmark'))
            code = int(not result['population_passed'])
    children = Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children').read_text().strip()
    result.update(lab_only=True, environment=env, final_children=children,
                  final_resources=process_resources(os.getpid()), final_cgroup_extra=cgroup_extra())
    if children:
        code = 1
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, sort_keys=True, indent=2)
    print(json.dumps(dict(mode=args.mode, exit_code=code, final_children=children)), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
