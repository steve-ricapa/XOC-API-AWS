"""Read-only repository evidence and lab byte manifest; no Git writes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import datetime

LAB = Path(__file__).resolve().parents[1]
EXCLUDED = {'.venv', '__pycache__', '.pytest_cache', 'l2-runs', 'l3-runs', 'l4-runs', '.git'}

def capture():
    files = {}
    for path in LAB.rglob('*'):
        rel = path.relative_to(LAB)
        if any(p in EXCLUDED for p in rel.parts) or not path.is_file():
            continue
        if path.name.startswith('l4-') or path.name.startswith('l4_'):
            continue
        files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    repos = []
    for name in ('XOC-API-AWS', 'XOC-APP-MOBILE', 'XOC_PAGE', 'proxy-sophia-xoc', 'XOC_MICROSERVICIO_AGENTES'):
        def git(*args):
            return subprocess.check_output(['git', '-C', str(LAB.parent/name), *args], text=True).strip()
        repos.append(dict(repo=name, branch=git('branch','--show-current'), head=git('rev-parse','HEAD'),
                          status=git('-c','core.quotepath=false','status','--short').splitlines()))
    return dict(at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), files=files, repositories=repos)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--compare')
    args = parser.parse_args()
    result = capture()
    if args.compare:
        before = json.loads(Path(args.compare).read_text())
        result['preserved'] = all(result['files'].get(p)==v for p,v in before['files'].items())
        result['changed_original_files'] = [p for p,v in before['files'].items() if result['files'].get(p)!=v]
        result['repositories_unchanged'] = before['repositories']==result['repositories']
    output = Path(args.output).resolve()
    if not output.is_relative_to(LAB):
        raise ValueError('LAB output required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='files'}, indent=2))
