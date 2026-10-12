"""Post-STOP evidence-only checks; never launches GDB or a target."""
import ast
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess

ROOT=Path(__file__).resolve().parent
EVIDENCE=ROOT/'.build/ic1r5-20261008'

def distribution(values):
    return dict(n=len(values),min=min(values),median=statistics.median(values),max=max(values)) if values else dict(n=0)

def leaves(value,path=''):
    if isinstance(value,dict):
        for key,item in value.items():
            yield ('field',key,path)
            yield from leaves(item,path+'.'+key)
    elif isinstance(value,list):
        for index,item in enumerate(value):
            yield from leaves(item,path+'['+str(index)+']')
    elif isinstance(value,str):
        yield ('value',value,path)

def main():
    before=json.loads((EVIDENCE/'preflight.json').read_text())
    runs=[]
    for directory in sorted(EVIDENCE.glob('*/run-*')):
        targets=[json.loads(line) for line in (directory/'target.jsonl').read_text().splitlines()]
        observer_path=directory/'observer.jsonl'
        events=[json.loads(line) for line in observer_path.read_text().splitlines()] if observer_path.exists() else []
        result=next((e for e in events if e['event']=='observation_result'),{})
        target_exit=next((e for e in targets if e['event']=='target_reaped'),{})
        start=next((e['marker']['mono'] for e in targets if e['event']=='target_marker' and e['marker']['event']=='work_start'),None)
        end=target_exit.get('mono')
        summary_path=directory/'summary.json'
        summary=json.loads(summary_path.read_text()) if summary_path.exists() else None
        actual_after=[e for e in events if e['event']=='target_after_detach']
        attach_sent=[e for e in events if e['event']=='command_sent' and e['phase']=='attach']
        upper_bound=(actual_after[-1]['mono']-attach_sent[0]['mono'])*1000 if actual_after and attach_sent else None
        plan=json.loads((directory.parent/'plan.json').read_text())
        kind=plan['runs'][int(directory.name.split('-')[-1])-1]['kind']
        row=dict(directory=directory.relative_to(EVIDENCE).as_posix(),target=kind,observed=bool(events),
            target_returncode=target_exit.get('returncode'),target_runtime_ms=(end-start)*1000 if start and end else None,
            observer_status=result.get('status'),attach_ack=result.get('attach_ack'),detach_ack=result.get('detach_ack'),
            # Raw early R5 stop_upper_bound_ms incorrectly used MI notification.
            # Preserve raw, but recompute conservative attach-to-clear bounds.
            attach_to_clear_upper_ms=upper_bound,
            sampled_stop_lower_ms=result.get('timings',{}).get('stop_lower_bound_ms'),
            sampled_stop_upper_ms=result.get('timings',{}).get('stop_upper_bound_ms') if 'stop_sampling_ms' in result.get('timings',{}) else None,
            timings={k:v for k,v in result.get('timings',{}).items() if k in ('attach_ms','stack_ms','detach_ms','total_ms')},
            post_detach=actual_after[-1] if actual_after else None,
            summary_present=summary is not None,passed=summary.get('passed') if summary else None,
            partial_events=len(events),timeouts=[e for e in events if e['event']=='phase_timeout'],
            symbol_names=sorted({e['symbol'] for e in events if e['event']=='frame' and e.get('symbol')}),
            observer_exit=next((e.get('returncode') for e in events if e['event']=='observer_exit'),None))
        runs.append(row)
    forbidden_fields={'env','environ','jwt','access_token','refresh_token','token','key','arguments','args','locals','toolresult','tool_result'}
    patterns={
        'jwt':re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'),
        'aws_access':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
        'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        'bearer':re.compile(r'(?i)\bBearer\s+[A-Za-z0-9_.-]{16,}'),
        'personal_path':re.compile(r'(?i)(?:[A-Z]:\\Users\\|/home/[^/]+/|/Users/[^/]+/)'),
        'fixture_data':re.compile(r'LAB_FICTIONAL_NOT_A_CREDENTIAL|LAB_TICKETS|LAB_DOCUMENTS'),
    }
    findings=[]
    scanned=0
    for path in sorted(EVIDENCE.rglob('*')):
        if path.suffix not in ('.json','.jsonl') or not path.is_file():
            continue
        values=[json.loads(line) for line in path.read_text().splitlines()] if path.suffix=='.jsonl' else [json.loads(path.read_text())]
        scanned+=1
        for value in values:
            for kind,item,position in leaves(value):
                if kind=='field' and item.lower() in forbidden_fields:
                    findings.append(dict(file=str(path.relative_to(EVIDENCE)),category='forbidden_field',position=position))
                elif kind=='value':
                    for name,pattern in patterns.items():
                        if pattern.search(item):
                            findings.append(dict(file=str(path.relative_to(EVIDENCE)),category=name,position=position))
    preserved=True
    old=ROOT/'.build/ic1r4-20261008'
    for name,digest in before['r4_hashes'].items():
        preserved &= hashlib.sha256((old/name).read_bytes()).hexdigest()==digest
    repos={}
    for repo in (ROOT.parents[1],ROOT.parents[2]/'XOC-MCP'):
        head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True)
        diff=subprocess.check_output(['git','-C',str(repo),'diff','--no-ext-diff'],text=True)
        check=subprocess.run(['git','-C',str(repo),'diff','--check'],capture_output=True,text=True)
        staged=subprocess.check_output(['git','-C',str(repo),'diff','--cached','--name-only'],text=True)
        repos[repo.name]=dict(head=head.strip(),head_unchanged=head==before['repos'][repo.name]['head'],
            tracked_diff_unchanged=hashlib.sha256(diff.encode()).hexdigest()==before['repos'][repo.name]['diff_sha256'],
            diff_check=check.returncode,staged_empty=not staged.strip())
    mcp=ROOT.parents[2]/'XOC-MCP'
    matches=[]
    for line in (mcp/'HANDOFF_SHA256SUMS.txt').read_text().splitlines():
        digest,name=line.split(None,1)
        matches.append(hashlib.sha256((mcp/name.strip()).read_bytes()).hexdigest()==digest)
    containers=subprocess.check_output(['docker','ps','-a','--filter','label=xoc.ic1r5.generation',
        '--format','{{.ID}} {{.Status}}'],text=True).strip()
    images={tag:subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
            for tag in ('xoc-mcp-ic1:baseline','xoc-ic1r4-gdb:20261008')}
    for script in ROOT.glob('ic1r5_*.py'):
        ast.parse(script.read_text(),filename=script.name)
    last=[r for r in runs if r['directory'].startswith('pilot-04/')]
    aggregates={}
    for metric in ('attach_ms','stack_ms','detach_ms','total_ms'):
        aggregates[metric]=distribution([r['timings'][metric] for r in runs if metric in r['timings']])
    aggregates['stop_upper_bound_ms']=distribution([r['sampled_stop_upper_ms'] if r['sampled_stop_upper_ms'] is not None else r['attach_to_clear_upper_ms'] for r in runs if r['attach_to_clear_upper_ms'] is not None])
    runtimes={kind:{mode:distribution([r['target_runtime_ms'] for r in runs if r['target']==kind and r['observed']==observed and r['target_runtime_ms'] is not None])
                    for mode,observed in [('control',False),('observed',True)]}
              for kind in ('T1','T2','T3','T4')}
    output=dict(observer_ready=False,decision='D_ABANDON_GDB_FOR_CURRENT_CONTRACT',
        mcp_tests=0,runs=runs,total_targets=len(runs),target_rc0=sum(r['target_returncode']==0 for r in runs),
        observed=sum(r['observed'] for r in runs),controls=sum(not r['observed'] for r in runs),
        residual_tracing=sum(r['post_detach']['state'] in ('t','T') or r['post_detach'].get('tracer_pid',0)!=0 for r in runs if r['post_detach']),
        timeouts=sum(len(r['timeouts']) for r in runs),partial_records=sum(r['partial_events'] for r in runs),
        historical_mixed_version_metrics=aggregates,historical_target_runtimes=runtimes,last_candidate=last,
        security_scan=dict(files=scanned,findings=findings,universal_absence_claim=False),
        cleanup=dict(containers=containers,all_recorded_targets_rc0=all(r['target_returncode']==0 for r in runs)),
        r4_preserved=preserved,repos=repos,handoff=dict(total=len(matches),matching=sum(matches)),images=images,
        script_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('ic1r5_*.py')})
    with (EVIDENCE/'analysis-final.json').open('x',encoding='utf-8') as stream:
        json.dump(output,stream,indent=2)
    print(json.dumps({k:v for k,v in output.items() if k not in ('runs','script_hashes')},indent=2))

if __name__=='__main__':
    main()
