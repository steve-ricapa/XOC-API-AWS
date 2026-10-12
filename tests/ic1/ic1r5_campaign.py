"""Host orchestration for innocuous O1-O8; no real MCP experiment entrypoint."""
import argparse
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parent
EVIDENCE=ROOT/'.build/ic1r5-20261008'
BASELINE='sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4'
AUX='sha256:f19a9c80f4c0fb7dccb33f27a041ba0503788d400a4f7498bcc467d4aef9a970'
BOOT='import json,sys; exec(compile(json.loads(sys.stdin.readline()), "<ic1r5-diagnostic>", "exec"))'

def save(path,data):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(data,stream,indent=2)

def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]

def preflight():
    EVIDENCE.mkdir(exist_ok=False)
    repos={}
    for repo in (ROOT.parents[1],ROOT.parents[2]/'XOC-MCP'):
        fields={}
        for name,args in [('status',['status','--short']),('branch',['branch','--show-current']),
            ('head',['rev-parse','HEAD']),('diff',['diff','--no-ext-diff']),('diff_check',['diff','--check'])]:
            result=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True)
            fields[name+'_sha256' if name=='diff' else name]=hashlib.sha256(result.stdout.encode()).hexdigest() if name=='diff' else result.stdout
        repos[repo.name]=fields
    for image in (BASELINE,AUX):
        assert json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]['Id']==image
    old=ROOT/'.build/ic1r4-20261008'
    save(EVIDENCE/'preflight.json',dict(repos=repos,baseline=BASELINE,auxiliary=AUX,
        r4_hashes={str(p.relative_to(old)):hashlib.sha256(p.read_bytes()).hexdigest() for p in old.rglob('*') if p.is_file()},
        script_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('ic1r5_*.py')}))

class Stream:
    def __init__(self,process,path):
        self.process=process
        self.queue=queue.Queue()
        self.rows=[]
        self.error=None
        self.path=path
        self.thread=threading.Thread(target=self.read,daemon=True)
        self.thread.start()
    def read(self):
        # Every sanitized event is flushed individually; never wait for final JSON.
        with self.path.open('x',encoding='utf-8') as file:
            try:
                for line in self.process.stdout:
                    row=json.loads(line)
                    file.write(json.dumps(row,separators=(',',':'))+'\n')
                    file.flush()
                    self.rows.append(row)
                    self.queue.put(row)
            except (ValueError,OSError) as error:
                self.error=type(error).__name__
            finally:
                self.queue.put(None)
    def wait(self,predicate,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            row=self.queue.get(timeout=max(.001,end-time.monotonic()))
            if row is None:
                raise RuntimeError('diagnostic_stream_ended')
            if predicate(row):
                return row
        raise TimeoutError('diagnostic_event_wait')

def send(process,value):
    process.stdin.write(json.dumps(value)+'\n')
    process.stdin.flush()

def launch(name,generation,image,entry,source,config,pid_container=None):
    limits=['--memory','256m','--memory-swap','256m','--cpus','1','--pids-limit','64'] if pid_container else [
        '--memory','512m','--memory-swap','512m','--cpus','2','--pids-limit','128']
    command=['docker','run','-i','--name',name,'--label','xoc.ic1r5.generation='+generation,
        '--network','none','--read-only','--user','10001:10001','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,size=134217728',
        *limits,*(['--pid','container:'+pid_container] if pid_container else []),
        '--entrypoint',entry,image,'-I','-B','-u','-c',BOOT]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    send(process,source)
    send(process,config)
    return process

def run(directory,index,kind,observed,inject=None,mismatch=None,late=False,race=None):
    generation=uuid.uuid4().hex
    name='xoc-ic1r5-'+generation[:12]
    run_dir=directory/f'run-{index:03}'
    run_dir.mkdir()
    summary=dict(index=index,target=kind,observed=observed,inject=inject,mismatch=mismatch,
                 late=late,race=race,generation=generation)
    owned=[]
    target=observer=None
    streams=[]
    try:
        target=launch(name,generation,BASELINE,'/opt/xoc-l2/bin/python',
            (ROOT/'ic1r5_targets.py').read_text(),dict(generation=generation,target=kind))
        owned.append(name)
        target_stream=Stream(target,run_dir/'target.jsonl')
        streams.append(target_stream)
        ready=target_stream.wait(lambda e:e['event']=='target_ready')
        container=inspect(name)
        assert container['Config']['Labels']['xoc.ic1r5.generation']==generation
        assert container['Image']==BASELINE
        binding=dict(generation=generation,container_id=container['Id'],container_label=generation,
                     parent_start=ready['parent']['start_ticks'],target=ready['target'])
        summary['binding']=binding
        if observed:
            observer_name=name+'-gdb'
            observer=launch(observer_name,generation,AUX,'/var/lang/bin/python',
                (ROOT/'ic1r5_observer.py').read_text(),binding,container['Id'])
            owned.append(observer_name)
            observer_stream=Stream(observer,run_dir/'observer.jsonl')
            streams.append(observer_stream)
            observer_stream.wait(lambda e:e['event']=='observer_ready',timeout=20)
            info=inspect(observer_name)
            assert info['Config']['Labels']['xoc.ic1r5.generation']==generation
            assert info['HostConfig']['PidMode']=='container:'+container['Id']
        send(target,dict(command='go'))
        window='finalization_start' if kind=='T3' else 'observe_window'
        target_stream.wait(lambda e:e['event']=='target_marker' and e['marker']['event']==window)
        if observed:
            requested=json.loads(json.dumps(binding))
            if mismatch=='pid': requested['target']['pid']+=99999
            elif mismatch=='start_ticks': requested['target']['start_ticks']+=1
            elif mismatch=='generation': requested['generation']='different-generation'
            elif mismatch=='executable': requested['target']['executable']='/not-the-owned-python'
            elif mismatch=='container_label': requested['container_label']='different-label'
            if late:
                target_stream.wait(lambda e:e['event']=='target_reaped')
            if race=='during_attach':
                # Let the short target approach natural exit before the request.
                # Actual outcome is retained; no assertion that it died mid-attach.
                time.sleep(.295)
            send(observer,dict(requested=requested,inject=inject,race=race))
            result=observer_stream.wait(lambda e:e['event']=='observation_result',timeout=3)
            summary['observation']=result
            observer_stream.wait(lambda e:e['event']=='observer_exit',timeout=3)
            observer.wait(timeout=3)
            summary['observer_returncode']=observer.returncode
        if kind=='T2':
            send(target,dict(command='eof'))
        if not any(e['event']=='target_reaped' for e in target_stream.rows):
            target_stream.wait(lambda e:e['event']=='target_reaped',timeout=6)
        reaped=next(e for e in target_stream.rows if e['event']=='target_reaped')
        summary['target_returncode']=reaped['returncode']
        markers=[e['marker'] for e in target_stream.rows if e['event']=='target_marker']
        start=next(e['mono'] for e in markers if e['event']=='work_start')
        summary['target_runtime_ms']=(reaped['mono']-start)*1000
        summary['target_reaped']=True
        send(target,dict(command='finish'))
        # Host docker CLI completion is not the target lifetime (already reaped).
        target.wait(timeout=10)
        summary['manager_returncode']=target.returncode
    except Exception as error:
        summary['harness_error']=type(error).__name__
    finally:
        # No force-removing a running target to claim PASS. Kill only observer
        # container if necessary, then allow the finite innocuous target to exit.
        if observer is not None and observer.poll() is None:
            info=inspect(name+'-gdb')
            assert info['Config']['Labels']['xoc.ic1r5.generation']==generation
            subprocess.run(['docker','kill',info['Id']],capture_output=True,check=True)
            observer.wait(timeout=3)
            summary['observer_emergency_cleanup']=True
        if target is not None and target.poll() is None:
            try:
                if kind=='T2': send(target,dict(command='eof'))
                send(target,dict(command='finish'))
                target.wait(timeout=7)
            except (OSError,subprocess.TimeoutExpired):
                summary['target_cleanup_failed']=True
        for process in (target,observer):
            if process is not None and process.poll() is not None:
                error=process.stderr.read()
                summary['target_stderr_present' if process is target else 'observer_stderr_present']=bool(error)
                if error:
                    summary['diagnostic_stderr_sha256']=hashlib.sha256(error.encode()).hexdigest()
        for stream in streams:
            stream.thread.join(timeout=1)
        remaining=[]
        for item in reversed(owned):
            info=inspect(item)
            assert info['Config']['Labels']['xoc.ic1r5.generation']==generation
            if info['State']['Running']:
                remaining.append(item)
            else:
                subprocess.run(['docker','rm',info['Id']],capture_output=True,check=True)
        summary['containers_remaining']=remaining
        observation=summary.get('observation',{})
        after=observation.get('target_after',{})
        if not isinstance(after,dict):
            summary['harness_error']='ObserverResultSchemaError'
            after={}
        summary['residual_trace']=after.get('state') in ('t','T') or bool(after.get('tracer_pid',0))
        base_ok=summary.get('target_returncode')==0 and not remaining and not summary.get('harness_error')
        if not observed:
            passed=base_ok
        elif mismatch:
            passed=base_ok and observation.get('status')=='IDENTITY_REJECTED' and not observation.get('attach_sent')
        elif inject:
            expected={'timeout':'PhaseTimeout','parser':'MIParseError','channel_cut':'EOFError'}[inject]
            passed=base_ok and observation.get('attach_ack') and observation.get('injected') and observation.get('status')==expected and not summary['residual_trace']
        elif late:
            passed=base_ok and observation.get('status')=='TARGET_EXITED' and not observation.get('attach_sent')
        elif race in ('during_stack','before_detach'):
            passed=base_ok and observation.get('status')=='TARGET_EXITED_RACE' and not summary['residual_trace']
        elif race=='during_attach':
            passed=base_ok and not summary['residual_trace'] and observation.get('status') in ('TARGET_EXITED','CAPTURED','ATTACH_FAILED')
        else:
            passed=base_ok and observation.get('status')=='CAPTURED' and observation.get('attach_ack') and observation.get('detach_ack') and not summary['residual_trace'] and any(f.get('symbol') for f in observation.get('frames',[]))
        summary['passed']=bool(passed)
        save(run_dir/'summary.json',summary)
        print(json.dumps({k:summary.get(k) for k in ('index','target','observed','inject','mismatch','passed','target_returncode','harness_error','residual_trace')}),flush=True)
    return summary

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--gate',choices=['pilot','O1','O2','O3','O4','O5','O6'])
    parser.add_argument('--name')
    args=parser.parse_args()
    if args.preflight:
        preflight()
        print('Preflight recorded; immutable images present.')
        return
    assert (EVIDENCE/'preflight.json').exists()
    directory=EVIDENCE/(args.name or args.gate)
    directory.mkdir(exist_ok=False)
    if args.gate in ('pilot','O1','O2','O3','O4'):
        kind,count={'pilot':('T1',1),'O1':('T1',10),'O2':('T2',5),'O3':('T3',10),'O4':('T4',10)}[args.gate]
        plan=[dict(kind=kind,observed=observed,late=args.gate=='O4' and observed and i%4==0,
                   race=('during_attach','during_stack','before_detach')[(i%4)-1] if args.gate=='O4' and observed and i%4 else None)
              for i in range(count) for observed in (False,True)]
    elif args.gate=='O5':
        plan=[dict(kind='T1',observed=True,inject=value) for value in ('timeout','parser','channel_cut')]
    else:
        plan=[dict(kind='T4',observed=True,mismatch=value) for value in ('pid','start_ticks','generation','executable','container_label')]
    save(directory/'plan.json',dict(gate=args.gate,runs=plan,
        scripts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('ic1r5_*.py')}))
    results=[]
    for index,case in enumerate(plan,1):
        result=run(directory,index,**case)
        results.append(result)
        if not result['passed']:
            break
    passed=len(results)==len(plan) and all(r['passed'] for r in results)
    save(directory/'result.json',dict(gate=args.gate,passed=passed,
                                     completed=len(results),planned=len(plan)))
    return 0 if passed else 1

if __name__=='__main__':
    raise SystemExit(main())
