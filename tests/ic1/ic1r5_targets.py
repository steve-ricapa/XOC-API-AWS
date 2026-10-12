"""Innocuous IC-1R.5 target manager. No MCP imports, network or business data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

TARGET = r'''
import atexit, gc, json, os, sys, time
def emit(event):
    print(json.dumps(dict(event=event,mono=time.monotonic(),pid=os.getpid())),flush=True)
def cpu(seconds):
    finish=time.process_time()+seconds
    value=0
    marked=False
    begin=time.process_time()
    while time.process_time()<finish:
        value=(value+17)%997
        if not marked and time.process_time()-begin>.1:
            marked=True
            emit('observe_window')
emit('ready')
assert sys.stdin.readline().strip()=='go'
emit('work_start')
kind=sys.argv[1]
if kind=='T1':
    cpu(1.0)
elif kind=='T2':
    emit('observe_window')
    sys.stdin.read()
    emit('eof_received')
elif kind=='T3':
    import asyncio, logging, weakref, threading
    # Synthetic, declared at-exit work in BOTH control and observed targets.
    # Not an emulation or explanation of the historical MCP teardown.
    cycles=[]
    for _ in range(15000):
        item=[]
        item.append(item)
        cycles.append(item)
    def finalization():
        emit('finalization_start')
        cycles.clear()
        gc.collect()
        cpu(.6)
        emit('finalization_end')
    atexit.register(finalization)
    emit('main_returned')
elif kind=='T4':
    cpu(.4)
emit('main_end')
'''

def identity(pid):
    root=Path('/proc')/str(pid)
    fields=(root/'stat').read_text().rsplit(')',1)[1].split()
    return dict(pid=pid,ppid=int(fields[1]),start_ticks=int(fields[19]),
                executable=os.readlink(root/'exe'),uid=root.stat().st_uid)

def main():
    config=json.loads(sys.stdin.readline())
    lock=threading.Lock()
    def emit(event,**fields):
        with lock:
            print(json.dumps(dict(event=event,generation=config['generation'],
                                 mono=time.monotonic(),**fields)),flush=True)
    target=subprocess.Popen(['/opt/xoc-l2/bin/python','-I','-B','-u','-c',TARGET,config['target']],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    ready=json.loads(target.stdout.readline())
    assert ready['event']=='ready'
    emit('target_ready',target=identity(target.pid),parent=identity(os.getpid()))
    def monitor():
        for line in target.stdout:
            emit('target_marker',marker=json.loads(line))
        target.wait()
        error=target.stderr.read()
        emit('target_reaped',returncode=target.returncode,stderr_present=bool(error))
    watcher=threading.Thread(target=monitor)
    watcher.start()
    for line in sys.stdin:
        command=json.loads(line)['command']
        if command=='go':
            target.stdin.write('go\n')
            target.stdin.flush()
        elif command=='eof':
            target.stdin.close()
        elif command=='finish':
            break
    # No kill/terminate target. Inocuous targets must finish naturally.
    watcher.join(timeout=5)
    emit('manager_exit',target_alive=target.poll() is None)
    return int(target.poll() is None)

if __name__=='__main__':
    raise SystemExit(main())
