"""Offline safe classification of D1 /proc evidence; no process interaction."""
import argparse
from collections import Counter
import json
from pathlib import Path


def category(previous, current):
    if not current.get('alive') or current.get('state') in ('Z', 'X', 'x'):
        return 'EXITING'
    wchan = current.get('wchan', '')
    if current.get('state') in ('S', 'D'):
        if 'futex' in wchan:
            return 'FUTEX_WAIT'
        if any(word in wchan for word in ('io_schedule', 'pipe_read', 'pipe_write')):
            return 'IO_WAIT'
        if wchan and wchan != '0':
            return 'KERNEL_WAIT'
    # Interval evidence, not a guessed native instruction pointer from wchan.
    if previous and current.get('state') == 'R' and current.get('syscall') in ('running', -1):
        if current['user_ticks'] > previous.get('user_ticks', current['user_ticks']) and current['system_ticks'] == previous.get('system_ticks'):
            return 'USER_CPU'
    return 'UNKNOWN'


def analyze(root):
    runs = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(root.glob('run-*.json'))]
    rows, classified = [], []
    for run in runs:
        for call in run.get('calls', []):
            if 'child_pid' not in call:
                continue
            events = [e for e in run['events'] if e.get('call') == call['index']]
            child = call['child_events']
            def mark(name, series=events, last=False):
                found = [e['mono'] for e in series if e['event'] == name]
                return found[-1 if last else 0] if found else None
            start, end = mark('wait_start'), mark('wait_end')
            validated, main, reaped = mark('validated'), mark('main_returned', child), mark('reaped')
            term = mark('terminate_requested')
            samples = [s for s in run['samples'] if s['call'] == call['index'] and s['child'].get('alive')]
            previous = None
            counts = Counter()
            for s in samples:
                kind = category(previous, s['child'])
                counts[kind] += 1
                classified.append(dict(run=run['index'], call=call['index'], mono=s['mono'], category=kind,
                    state=s['child']['state'], wchan=s['child'].get('wchan'), syscall=s['child'].get('syscall')))
                previous = s['child']
            first, last = samples[0], samples[-1]
            def delta(field, who='child'):
                return last[who][field] - first[who][field]
            hz = run['clk_tck']
            clean_ms = (end-start)*1000
            bucket = 'FAILED' if call['returncode'] != 0 else 'FAST' if clean_ms < 300 else 'SLOW' if clean_ms >= 400 else 'MID'
            last_py = max((e['mono'] for e in child), default=None)
            post = [s for s in samples if last_py is not None and s['mono'] > last_py]
            post_user = (post[-1]['child']['user_ticks']-post[0]['child']['user_ticks'])*1000/hz if len(post)>1 else None
            sched_first, sched_last = first['child'].get('schedstat'), last['child'].get('schedstat')
            rows.append(dict(run=run['index'], case=run['case'], call=call['index'], pid=call['child_pid'],
                status=call.get('status'), code=call.get('code'), rc=call['returncode'], bucket=bucket,
                clean_wait_ms=clean_ms, validated_to_main_ms=(main-validated)*1000 if main and validated else None,
                main_before_term=bool(main < term) if main and term else None,
                term=term, reaped=reaped, main_returned=main, last_python_marker=last_py,
                last_gc_stop=mark('gc_stop', child, last=True),
                categories=dict(counts), sample_wall_ms=(last['mono']-first['mono'])*1000,
                child_user_ms=delta('user_ticks')*1000/hz, child_system_ms=delta('system_ticks')*1000/hz,
                parent_user_ms=delta('user_ticks', 'parent')*1000/hz,
                parent_system_ms=delta('system_ticks', 'parent')*1000/hz,
                child_voluntary=delta('voluntary'), child_involuntary=delta('involuntary'),
                runqueue_wait_ms=(sched_last[1]-sched_first[1])/1e6 if sched_first and sched_last else None,
                task_counts=sorted(set(len(s['child'].get('task_ids', [])) for s in samples)),
                post_python_task_counts=sorted(set(len(s['child'].get('task_ids', [])) for s in post)),
                post_python_states=dict(Counter(s['child']['state'] for s in post)),
                post_python_wchan=dict(Counter(str(s['child'].get('wchan')) for s in post)),
                post_python_syscall=dict(Counter(str(s['child'].get('syscall')) for s in post)),
                post_python_user_ms=post_user,
                last_child_snapshot=last['child']))
    return dict(runs=len(runs), failures=sum(r.get('exit_code', 0)!=0 for r in runs),
        children=sum(len(r.get('children', [])) for r in runs),
        population=[dict(run=r['index'], case=r['case'], observed=r.get('instrumented'), exit_code=r.get('exit_code')) for r in runs],
        observed_exits=rows, snapshot_classification=classified,
        bucket_counts=dict(Counter(row['bucket'] for row in rows)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = Path(__file__).resolve().parent / '.build'
    assert args.input.resolve().is_relative_to(build) and args.output.resolve().is_relative_to(build)
    data = analyze(args.input)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
    print(json.dumps({k: data[k] for k in ('runs', 'failures', 'children', 'bucket_counts')}))


if __name__ == '__main__':
    main()
