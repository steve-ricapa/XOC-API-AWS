"""Summarize existing L4 evidence only; no workload, cloud, or Git writes."""
import hashlib
import argparse
import json
from pathlib import Path

from l4_summarize import stats

LAB = Path(__file__).resolve().parents[1]
RUN = LAB / 'l4-runs/l4-final03-20260916'
C1 = LAB / 'l4-runs/l4-final02-20260916/results'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, data):
    with (LAB / 'reports' / name).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', default=RUN.name)
    parser.add_argument('--suffix', default='')
    parser.add_argument('--windows', default='l4-windows-final.json')
    args = parser.parse_args()
    import re
    if (not re.fullmatch(r'[A-Za-z0-9_-]+', args.run) or
            not re.fullmatch(r'[-A-Za-z0-9_]*', args.suffix) or
            Path(args.windows).name != args.windows):
        raise ValueError('LAB_SAFE_REPORT_NAMES_REQUIRED')
    run = LAB / 'l4-runs' / args.run
    data = read(run / 'results/benchmark/benchmark.json')
    tests = read(run / 'results/tests/tests.json')
    windows = read(LAB / 'reports' / args.windows)
    provenance = read(run / 'results/provenance/provenance.json')
    builds = {}
    for path in sorted((run / 'build-a').iterdir()):
        if path.is_file():
            other = run / 'build-b' / path.name
            builds[path.name] = dict(sha256=sha(path), bytes=path.stat().st_size,
                                    identical=other.is_file() and sha(other) == sha(path))
    assert len(builds) == 7 and all(v['identical'] for v in builds.values())
    baseline = read(LAB / 'reports/l4-baseline.json')
    original_changed = [p for p, digest in baseline['files'].items()
                        if not (LAB / p).is_file() or sha(LAB / p) != digest]
    assert not original_changed, original_changed
    source_paths = sorted([
        *LAB.glob('xoc_mcp_lab_l4/*.py'), *LAB.glob('tests/test_l4*.py'),
        *LAB.glob('packaging/*l4*'), *LAB.glob('scripts/l4*'),
    ])
    save('l4-source-changes' + args.suffix + '.json', dict(
        scope='XOC-MCP-LAB only; additive files',
        original_files_changed=original_changed,
        files=[dict(path=p.relative_to(LAB).as_posix(), sha256=sha(p), bytes=p.stat().st_size)
               for p in source_paths if p.is_file()],
        reports=[p.name for p in sorted((LAB/'reports').glob('l4-*')) if p.is_file()],
        generated_artifacts='l4-runs/; prior L1/L2/L3 retained; no Git operations'))
    client_stages = {}
    for name in ('preparation_seconds', 'initialize_seconds', 'call_seconds', 'request_cleanup_seconds'):
        client_stages[name] = stats([r['measurements'][name] for r in data['sequential']
                                    if r.get('measurements', {}).get(name) is not None])
    decisions = {r['request_id'] for r in data['denied']}
    denied_events = [e for e in data['events'] if e.get('method') == 'tools/call'
                     and e.get('request_id') in decisions]
    c1_tests = read(C1 / 'c1tests/c1tests/tests.json')
    final = dict(
        run_id=run.name, decision='B', local_only=True,
        linux_l4={k: tests.get(k) for k in ('passed','failed','skipped','exit_code','final_children',
                                          'suite','c1_passed','l4_passed')},
        linux_c1=c1_tests,
        windows=windows,
        reproducibility=builds,
        canonical_origins=provenance.get('canonical_origins'),
        core_revision='ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9',
        core_wheel_sha256='df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41',
        hot_client_stages_seconds=client_stages,
        denies=[{k: r.get(k) for k in ('status','code','total_seconds','tool')} for r in data['denied']],
        deny_summary=dict(n=len(data['denied']),
                          seconds=stats([r['total_seconds'] for r in data['denied']]),
                          handlers=sum(e['handler_calls'] for e in denied_events),
                          reads=sum(e['reads'] for e in denied_events)),
        healthy_after_denials=data['availability_after_denials'],
        c1_timeouts=[{k:r.get(k) for k in ('tool','status','worker_status','code','total_seconds','lifecycle','processes')}
                     for r in data['c1_comparable'] if r['status'] != 'allowed'],
        c1_concurrent_processes=data['c1_concurrent_processes'],
        final_children=data['final_children'],
        actual_socket_state_count_measured=False,
        limitations=['fds are not a TCP connection count',
                     'SDK serialization is included in dispatch, not a separate exact span',
                     'bounded rounds do not establish indefinite memory stability',
                     'four non-cooperative workers require whole-server termination',
                     'private ES256 fixture header is not production MCP OAuth'],
    )
    # Keep test node-level details in their existing report; do not duplicate them.
    final['linux_c1'].pop('tests', None)
    save('l4-validation' + args.suffix + '.json', final)
    print(json.dumps(dict(reproducible=True, original_lab_preserved=True,
                         linux_l4=final['linux_l4'], decision='B'), indent=2))


if __name__ == '__main__':
    main()
