"""Run original host smokes, persisting summaries but no raw stderr/token logs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--backend',required=True)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    if args.output.exists():raise FileExistsError('New output required')
    rows=[]
    for name in ('smoke.py','smoke_l1.py'):
        process=subprocess.run([sys.executable,'-B',str(ROOT/name),'--backend',args.backend],
                               cwd=ROOT,capture_output=True,timeout=180)
        row=dict(script=name,exit_code=process.returncode,
                 stderr_sha256=hashlib.sha256(process.stderr).hexdigest(),raw_stderr_saved=False)
        if process.returncode==0:row['summary']=json.loads(process.stdout)
        rows.append(row)
        if process.returncode:break
    result=dict(lab_only=True,smokes=rows,passed=len(rows)==2 and all(r['exit_code']==0 for r in rows))
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(dict(passed=result['passed'],smokes=len(rows))))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
