"""Reduce saved evidence; never run workloads or edit prior L1/L2/L3 reports."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

LAB = Path(__file__).resolve().parents[1]


def stats(values):
    values = sorted(values)
    if not values:
        return None
    return dict(n=len(values), minimum=values[0], median=statistics.median(values),
        p95_nearest_rank=values[math.ceil(.95*len(values))-1], maximum=values[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    root=(LAB/'l4-runs'/args.run).resolve()
    if not root.is_relative_to(LAB/'l4-runs'):
        raise ValueError('LAB run required')
    source=root/'results/benchmark/benchmark.json'
    data=json.loads(source.read_text())
    events=data['events']
    requests=[e for e in events if e.get('event')=='request_finished']
    calls=[e for e in requests if e.get('method')=='tools/call']
    workers=[e for e in events if e.get('event')=='worker_finished']
    denials=[e for e in calls if e.get('request_id') in {r['request_id'] for r in data['denied']}]
    def compact_resource(value):
        cg=value.get('cgroup',{})
        return dict(rss=value.get('VmRSS'),hwm=value.get('VmHWM'),threads=value.get('Threads'),
            fds=value.get('fds'),tasks=value.get('tasks'),cpu_ticks=value.get('cpu_ticks'),cgroup=cg)
    def round_runtime(round_row):
        request_id=round_row['rows'][-1]['request_id']
        candidates=[r for r in requests if r.get('request_id')==request_id]
        last=candidates[-1] if candidates else {}
        return {k:last.get(k) for k in ('pending_requests','live_request_scopes','pool','threads','tasks')}
    result=dict(run_id=args.run,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        c1=data['c1_comparable_summary'],c1_concurrent=data.get('c1_concurrent_summary'),
        hot=data['sequential_summary'],concurrent=data['concurrent_summary'],
        large=data['large_summary'],cold=dict(startup=data['startup'],
            first_query=stats([c['first']['total_seconds'] for c in data['cold']]),
            start_through_first=stats([c['start_through_first_seconds'] for c in data['cold']]),
            full_lifecycle=stats([c['full_lifecycle_seconds'] for c in data['cold']]),
            shutdown=stats([c['cleanup']['global_shutdown_seconds'] for c in data['cold']])),
        main_server=dict(http_requests=len(requests),business_attempts=len(calls),
            handlers=sum(e['handler_calls'] for e in calls),reads=sum(e['reads'] for e in calls),
            all_contexts_reset=all(e['context_cleared'] for e in requests),
            all_worker_contexts_reset=all(e['worker_context_cleared'] for e in workers),
            max_pending=max(e['pending_requests'] for e in requests),
            max_live_scopes=max(e['live_request_scopes'] for e in requests),
            last=dict((k,requests[-1].get(k)) for k in ('pool','pending_requests','live_request_scopes','threads','tasks')),
            wire_max=max(e.get('output_bytes') or 0 for e in requests),
            writes=sum(e['writes'] for e in calls),external_attempts=sum(e['network_attempts']+e['provider_attempts'] for e in calls)),
        stage_seconds={name:stats([e[name]/1e9 for e in requests if e.get(name) is not None])
            for name in ('verify_bind_ns','body_receive_ns','dispatch_ns','context_reset_ns','response_guard_ns')},
        worker_seconds=stats([e['worker_elapsed_ns']/1e9 for e in workers]),
        denied_handlers=sum(e['handler_calls'] for e in denials),denied_reads=sum(e['reads'] for e in denials),
        finite_fault=data['finite_fault'],blocked=data['faults'],
        rounds=[dict(round=r['round'],summary=r['summary'],runtime=round_runtime(r),before=compact_resource(r['before']),
            after=compact_resource(r['after'])) for r in data['stability']],
        resource_before=compact_resource(data['resources_before']),resource_after=compact_resource(data['resources_after']),
        cleanup=data['cleanup'],final_children=data['final_children'],final_resources=data['final_resources'])
    output=Path(args.output).resolve()
    if not output.is_relative_to(LAB):
        raise ValueError('LAB output required')
    with output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2)
    concise={k:v for k,v in result.items() if k not in ('blocked','finite_fault','final_resources','resource_before','resource_after')}
    print(json.dumps(concise,indent=2))


if __name__=='__main__':
    main()
