"""Bounded IC-1R.2 campaign, original tests, immutable image, safe telemetry only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

IMAGE = "sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4"
CASES = {
    "A": "test_l2_lifecycle.py::test_concurrent_calls_same_instance_only_one_child",
    "B": "test_worker_lifecycle.py::test_inherited_secrets_never_used",
    "C": "test_worker_lifecycle.py::test_same_request_replay_is_not_distributed_deduplication",
}


def inside(case, instrumented):
    import asyncio
    import contextlib
    from contextvars import ContextVar
    import io
    import os
    import time
    from unittest.mock import patch
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    from xoc_mcp_lab_l6.integrity import require_l6_installed
    require_l6_installed()
    calls, events, samples, processes, outcomes = [], [], [], [], []
    active = ContextVar('ic1r2_call', default=None)

    def proc(pid):
        try:
            stat = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
            status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines())
            fds = {}
            for fd in Path(f'/proc/{pid}/fd').iterdir():
                try:
                    target = os.readlink(fd)
                    kind = 'pipe' if target.startswith('pipe:') else 'socket' if target.startswith('socket:') else 'anon' if target.startswith('anon_inode:') else 'file_or_device'
                    fds[kind] = fds.get(kind, 0) + 1
                except OSError:
                    pass
            return dict(alive=True, state=stat[0], user_ticks=int(stat[11]), system_ticks=int(stat[12]),
                voluntary=int(status['voluntary_ctxt_switches']), involuntary=int(status['nonvoluntary_ctxt_switches']),
                rss_kb=int(status.get('VmRSS', '0 kB').split()[0]), threads=int(status['Threads']), fd_types=fds)
        except (FileNotFoundError, ProcessLookupError):
            return dict(alive=False)

    def event(name, owner=None, **fields):
        pid = owner.process.pid if owner and owner.process else None
        events.append(dict(event=name, mono=time.monotonic(), child_pid=pid,
                           call=active.get(), **fields))

    with Isolation():
        configure_logs()
        from xoc_mcp_lab import budget, query_worker, testing
        from xoc_mcp_lab.installed import require_installed
        from xoc_mcp_lab_harness import __file__ as harness_file
        import pytest
        testing.BACKEND = require_installed()
        original_factory = budget._SDK_CREATE
        original_run = query_worker.QueryWorker._run
        original_wait = budget.ProcessOwner._wait
        original_force = budget.ProcessOwner.force
        original_attach = budget.ProcessOwner.attach
        original_exchange = budget.ProcessOwner.exchange
        stream_patches = []

        async def factory(**kwargs):
            if instrumented:
                assert kwargs['args'][:3] == ['-B', '-m', 'xoc_mcp_lab.verified_server']
                kwargs['args'] = ['-B', '-c', PAYLOAD_CHILD, *kwargs['args'][3:]]
            process = await original_factory(**kwargs)
            processes.append(process)
            if instrumented and process.stdin:
                delegate = process.stdin.aclose
                async def close_stdin():
                    event('stdin_close_requested', child=process.pid)
                    try:
                        return await delegate()
                    finally:
                        event('stdin_close_returned', child=process.pid)
                patcher = patch.object(process.stdin, 'aclose', close_stdin)
                patcher.start()
                stream_patches.append(patcher)
            return process

        def attach(self, process):
            result = original_attach(self, process)
            calls[active.get()]['_owner'] = self
            event('child_attached', self)
            return result

        async def wait(self, seconds):
            event('wait_start', self, seconds=seconds, after_actions=list(self.actions))
            handle = None
            def sample():
                nonlocal handle
                samples.append(dict(mono=time.monotonic(), call=active.get(), child_pid=self.process.pid,
                                    child=proc(self.process.pid), parent=proc(os.getpid())))
                handle = asyncio.get_running_loop().call_later(.04, sample)
            if self.process:
                sample()
            try:
                return await original_wait(self, seconds)
            finally:
                if handle:
                    handle.cancel()
                event('wait_end', self, returncode=self.process.returncode if self.process else None)

        def force(self, kill=False):
            event('kill_requested' if kill else 'terminate_requested', self,
                  child_state=proc(self.process.pid) if self.process else None,
                  parent_state=proc(os.getpid()))
            result = original_force(self, kill=kill)
            event('force_returned', self, actions=list(self.actions))
            return result

        async def exchange(self, *args, **kwargs):
            try:
                return await original_exchange(self, *args, **kwargs)
            except BaseException as exc:
                event('exchange_error', self, category=type(exc).__name__)
                raise

        async def run(self, *args, **kwargs):
            index = len(calls)
            row = dict(index=index, start=time.monotonic(), child_events=[], audits=[])
            calls.append(row)
            token = active.set(index)
            mark_original = kwargs['mark']
            def mark(name):
                mark_original(name)
                event(name, row.get('_owner'))
            kwargs['mark'] = mark
            log = kwargs.get('errlog')
            try:
                value = await original_run(self, *args, **kwargs)
                row.update(status=value.get('status'), code=value.get('code'))
                return value
            finally:
                row['end'] = time.monotonic()
                owner = row.pop('_owner', None)
                if owner and owner.process:
                    row.update(child_pid=owner.process.pid, returncode=owner.process.returncode,
                               actions=list(owner.actions), timed_out=owner.timed_out)
                row['lifecycle'] = self.lifecycle[-1:]
                if log and log is not sys.stderr:
                    position = log.tell()
                    log.seek(0)
                    for line in log:
                        if line.startswith('ic1r2_child '):
                            value = json.loads(line.split(' ', 1)[1])
                            if owner and value.get('pid') == owner.process.pid:
                                row['child_events'].append(value)
                        elif 'lab_call ' in line:
                            value = json.loads(line.split('lab_call ', 1)[1])
                            row['audits'].append({k: value[k] for k in ('handler_calls', 'executor_calls', 'reads', 'writes') if k in value})
                    log.seek(position)
                active.reset(token)

        class Reporter:
            def pytest_runtest_logreport(self, report):
                if report.when == 'call' or report.failed:
                    outcomes.append(dict(phase=report.when, outcome=report.outcome, duration=report.duration))

        log = io.StringIO()
        with contextlib.ExitStack() as patches:
            patches.enter_context(patch.object(budget, '_SDK_CREATE', factory))
            # Control keeps original child/ProcessOwner; only test result and process handle observed.
            if instrumented:
                for obj, name, delegate in ((query_worker.QueryWorker, '_run', run),
                    (budget.ProcessOwner, 'attach', attach), (budget.ProcessOwner, '_wait', wait),
                    (budget.ProcessOwner, 'force', force), (budget.ProcessOwner, 'exchange', exchange)):
                    patches.enter_context(patch.object(obj, name, delegate))
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                rc = int(pytest.main(['-q', '--tb=no', '-p', 'no:cacheprovider',
                    str(Path(harness_file).parent / 'tests' / CASES[case])], plugins=[Reporter()]))
            for patcher in reversed(stream_patches):
                patcher.stop()
        children = [dict(pid=p.pid, returncode=p.returncode, alive=proc(p.pid)['alive']) for p in processes]
        report = dict(case=case, instrumented=instrumented, exit_code=rc, outcomes=outcomes,
            calls=calls, events=events, samples=samples, children=children,
            clk_tck=os.sysconf('SC_CLK_TCK'), raw_log_not_retained=True,
            log_sha256=hashlib.sha256(log.getvalue().encode()).hexdigest())
    print(json.dumps(report))
    return rc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inside', choices=CASES)
    parser.add_argument('--instrumented', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.inside:
        return inside(args.inside, args.instrumented)
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / '.build'):
        raise ValueError('New evidence directory under tests/ic1/.build required')
    image = subprocess.check_output(['docker', 'image', 'inspect', 'xoc-mcp-ic1:baseline', '--format', '{{.Id}}'], text=True).strip()
    assert image == IMAGE
    script = Path(__file__).read_text(encoding='utf-8')
    child = (root / 'ic1r2_child.py').read_text(encoding='utf-8')
    source = ('PAYLOAD_CHILD = ' + repr(child) + '\n' + script).encode()
    # C executes TWO children; seven cycles cost 56, then A/B control+observed cost four.
    plan = [(case, observed) for cycle in range(7) for case in CASES for observed in (False, True)]
    plan += [(case, observed) for case in ('A', 'B') for observed in (False, True)]
    output.mkdir(parents=True)
    def save(path, data):
        with path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
    save(output / 'plan.json', dict(image=image, runs=plan, max_stdio_children=60,
        script_sha256=hashlib.sha256(script.encode()).hexdigest(),
        child_sha256=hashlib.sha256(child.encode()).hexdigest()))
    summaries, count = [], 0
    for index, (case, observed) in enumerate(plan, 1):
        name = 'xoc-ic1r2-' + uuid4().hex[:12]
        command = ['docker', 'run', '--rm', '-i', '--name', name, '--label', 'xoc.ic1r2.run=' + name,
            '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=134217728',
            '--memory', '512m', '--memory-swap', '512m', '--cpus', '2', '--pids-limit', '128',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--platform', 'linux/amd64',
            '--entrypoint', '/opt/xoc-l2/bin/python', image, '-I', '-B', '-', '--inside', case]
        if observed:
            command.append('--instrumented')
        try:
            result = subprocess.run(command, input=source, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            label = subprocess.check_output(['docker', 'inspect', '--format', '{{index .Config.Labels "xoc.ic1r2.run"}}', name], text=True).strip()
            if label == name:
                subprocess.run(['docker', 'rm', '--force', name], capture_output=True, check=True)
            save(output / f'run-{index:02}.json', dict(harness_timeout=True, owned_container_removed=label == name))
            raise
        try:
            report = json.loads(result.stdout)
        except ValueError:
            report = dict(runner_error=True, stdout_hash=hashlib.sha256(result.stdout).hexdigest())
        report.update(index=index, image=image, docker_exit=result.returncode,
            stderr_bytes=len(result.stderr), stderr_hash=hashlib.sha256(result.stderr).hexdigest())
        save(output / f'run-{index:02}.json', report)
        count += len(report.get('children', []))
        summary = dict(index=index, case=case, instrumented=observed, exit_code=result.returncode,
                       stdio_children=count, runner_error=report.get('runner_error', False))
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
        if summary['runner_error'] or any(c['alive'] for c in report.get('children', [])) or count > 60:
            save(output / 'summary.json', summaries)
            return 3
    save(output / 'summary.json', summaries)
    return int(any(row['exit_code'] for row in summaries))


if __name__ == '__main__':
    raise SystemExit(main())
