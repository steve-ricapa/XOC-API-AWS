"""IC-1R.3: inventory then bounded /proc-only extension of preserved R.2 harness."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4

IMAGE = 'sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4'
R2_SHA = '104dd5ea5d54b96ed9e79f322a7629518526a7fc5a1787854af43b6cc0f7c2e5'
CHILD_SHA = '2cb9390093bb388e39dbeefcd56cf8007c182c6f9ab837ec4f7d43a744408f1c'

D0_SOURCE = r'''
import json, os, shutil, subprocess, sys
from pathlib import Path
tools = []
for name in ('strace', 'gdb', 'gstack', 'pstack', 'perf', 'py-spy'):
    path = shutil.which(name)
    version = None
    if path and name not in ('gstack', 'pstack'):
        result = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=5)
        version = (result.stdout or result.stderr).splitlines()[:1]
    tools.append(dict(tool=name, available=bool(path), version=version,
        usable_without_extra_privileges='not_tested' if path else 'no'))
# Harmless owned child waits for pipe EOF, no sleeps, no workload or credentials.
child = subprocess.Popen([sys.executable, '-I', '-B', '-c',
    'import sys; print("ready", flush=True); sys.stdin.read()'], stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
assert child.stdout.readline().strip() == 'ready'
access = []
try:
    for name in ('stack', 'wchan', 'syscall', 'stat', 'status', 'sched', 'task'):
        try:
            path = Path(f'/proc/{child.pid}/{name}')
            if name == 'task':
                list(path.iterdir())
            else:
                path.read_bytes()  # Probe readability; no raw content retained.
            access.append(dict(node=name, readable=True))
        except OSError as exc:
            access.append(dict(node=name, readable=False, errno=exc.errno))
finally:
    child.communicate('', timeout=5)
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
print(json.dumps(dict(tools=tools, proc_access=access, uid=os.getuid(),
    cap_effective=status['CapEff'].strip(), seccomp=status.get('Seccomp', '').strip(),
    no_new_privs=status.get('NoNewPrivs', '').strip(), inventory_child_rc=child.returncode)))
'''

EXTRA_SOURCE = r'''
def proc_extra(pid):
    from pathlib import Path
    import re
    result = {}
    for name in ('wchan', 'syscall', 'sched', 'schedstat'):
        try:
            raw = Path(f'/proc/{pid}/{name}').read_text()
            if name == 'wchan':
                value = raw.strip()
                result['wchan'] = value if re.fullmatch(r'[A-Za-z0-9_]+', value) else 'redacted'
            elif name == 'syscall':
                # Discard registers, arguments, stack/instruction addresses.
                first = raw.split()[0]
                result['syscall'] = first if first == 'running' else int(first)
            elif name == 'schedstat':
                result['schedstat'] = [int(n) for n in raw.split()[:3]]
            else:
                permitted = {'se.sum_exec_runtime', 'nr_switches', 'nr_voluntary_switches',
                    'nr_involuntary_switches', 'se.nr_migrations'}
                result['sched'] = {}
                for line in raw.splitlines():
                    if ':' in line:
                        key, value = (s.strip() for s in line.split(':', 1))
                        if key in permitted:
                            result['sched'][key] = float(value)
        except (OSError, ValueError, IndexError) as exc:
            result[name + '_unavailable'] = getattr(exc, 'errno', None) or type(exc).__name__
    try:
        result['task_ids'] = sorted(int(p.name) for p in Path(f'/proc/{pid}/task').iterdir())
    except OSError as exc:
        result['task_unavailable'] = exc.errno
    return result
'''


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)


def container(source, args):
    name = 'xoc-ic1r3-' + uuid4().hex[:12]
    command = ['docker', 'run', '--rm', '-i', '--name', name, '--label', 'xoc.ic1r3.run=' + name,
        '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=134217728',
        '--memory', '512m', '--memory-swap', '512m', '--cpus', '2', '--pids-limit', '128',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--platform', 'linux/amd64',
        '--entrypoint', '/opt/xoc-l2/bin/python', IMAGE, '-I', '-B', '-', *args]
    try:
        result = subprocess.run(command, input=source.encode(), capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        label = subprocess.check_output(['docker', 'inspect', '--format',
            '{{index .Config.Labels "xoc.ic1r3.run"}}', name], text=True).strip()
        if label == name:
            subprocess.run(['docker', 'rm', '--force', name], capture_output=True, check=True)
        raise
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = dict(runner_error=True, stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
    report.update(image=IMAGE, docker_exit=result.returncode,
                  stderr_bytes=len(result.stderr), stderr_sha256=hashlib.sha256(result.stderr).hexdigest())
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('d0', 'd1'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / '.build'):
        raise ValueError('New evidence directory required')
    assert subprocess.check_output(['docker', 'image', 'inspect', 'xoc-mcp-ic1:baseline',
        '--format', '{{.Id}}'], text=True).strip() == IMAGE
    output.mkdir(parents=True)
    if args.phase == 'd0':
        report = container(D0_SOURCE, [])
        save(output / 'inventory.json', report)
        print(json.dumps(report))
        return report['docker_exit']
    original = (root / 'ic1r2_probe.py').read_text(encoding='utf-8')
    child = (root / 'ic1r2_child.py').read_text(encoding='utf-8')
    assert hashlib.sha256(original.encode()).hexdigest() == R2_SHA
    assert hashlib.sha256(child.encode()).hexdigest() == CHILD_SHA
    # Extend ONLY the diagnostic observer in memory; originals are never edited.
    changes = {'threads=int(status[\'Threads\']), fd_types=fds)':
               'threads=int(status[\'Threads\']), fd_types=fds, **proc_extra(pid))',
               'call_later(.04, sample)': 'call_later(.025, sample)'}
    derived = original
    for old, new in changes.items():
        assert derived.count(old) == 1
        derived = derived.replace(old, new)
    source = 'PAYLOAD_CHILD = ' + repr(child) + '\n' + EXTRA_SOURCE + '\n' + derived
    # Ten controls intercalated with twenty B observations, plus A/C in alternate cycles.
    plan = []
    for cycle in range(10):
        plan += [('B', False), ('B', True), ('B', True), ('A' if cycle % 2 == 0 else 'C', True)]
    save(output / 'plan.json', dict(image=IMAGE, runs=plan, max_tests=40, max_stdio_children=45,
        base_observer_sha256=R2_SHA, child_sha256=CHILD_SHA,
        derived_payload_sha256=hashlib.sha256(source.encode()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    summaries = []
    for index, (case, observed) in enumerate(plan, 1):
        report = container(source, ['--inside', case, *(['--instrumented'] if observed else [])])
        report.update(index=index)
        save(output / f'run-{index:02}.json', report)
        summaries.append(dict(index=index, case=case, observed=observed, exit_code=report['docker_exit']))
        print(json.dumps(summaries[-1]), flush=True)
        if report.get('runner_error') or any(c['alive'] for c in report.get('children', [])):
            save(output / 'summary.json', summaries)
            return 3
        # Pause population at any observed failure for human/model classification;
        # never treat a single green as a stopping criterion.
        if observed and report['docker_exit']:
            save(output / 'summary.json', summaries)
            return 1
    save(output / 'summary.json', summaries)
    return int(any(row['exit_code'] for row in summaries))


if __name__ == '__main__':
    raise SystemExit(main())
