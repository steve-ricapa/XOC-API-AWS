"""Inspección local SquashFS sin red, privilegios ni ejecución de sus fuentes."""
import hashlib,json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'tests/ic1/.build/ac2r'
REPO=ROOT.parent/'XOC_MICROSERVICIO_AGENTES'
IMAGE='sha256:476e47aa3b0b015e1735a01ac3ec93b59847c19a2fe7d4fdc1c15a3f0f8239bc'

def inspect(args):
    r=subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--memory','256m','--pids-limit','64',
        '--mount',f'type=bind,source={OUT/"azure-deployed.squashfs"},target=/artifact,readonly',IMAGE,*args],capture_output=True,timeout=30)
    if r.returncode: raise RuntimeError('ARTIFACT_INSPECTION_FAILED')
    return r.stdout

def source(path):
    return path=='function_app.py' or (path.startswith(('sophia_agent/','shared/','victor_agent/','domain/')) and path.endswith('.py'))

def main():
    names=[n.removeprefix('squashfs-root/') for n in inspect(['-l','/artifact']).decode().splitlines() if n.startswith('squashfs-root/')]
    sources={n:inspect(['-cat','/artifact',n]) for n in names if source(n)}
    hashes={n:hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest() for n,b in sources.items()}
    revisions=subprocess.check_output(['git','rev-list','--all','--max-count=150'],cwd=REPO,text=True).splitlines()
    comparisons=[]
    for revision in revisions:
        tree=subprocess.check_output(['git','ls-tree','-r',revision],cwd=REPO,text=True).splitlines()
        git_sources={row.split('\t',1)[1]:row.split()[2] for row in tree if '\t' in row and source(row.split('\t',1)[1])}
        different=sorted(n for n in set(hashes)|set(git_sources) if hashes.get(n)!=git_sources.get(n))
        comparisons.append({'commit':revision,'different':different,'matches':sum(hashes.get(n)==h for n,h in git_sources.items())})
    best=min(comparisons,key=lambda x:len(x['different']))
    for name,data in sources.items():
        if name=='function_app.py' or name.startswith(('sophia_agent/','shared/')):
            target=(OUT/'azure-source'/name).resolve()
            if not target.is_relative_to((OUT/'azure-source').resolve()): raise ValueError('INVALID_PATH')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
    report={'DEPLOYED_SOURCE_STATUS':'EXACT_COMMIT' if not best['different'] else 'DERIVED',
        'closest':best,'sources':len(sources),'hashes':{n:hashlib.sha256(b).hexdigest() for n,b in sources.items()},
        'governed_files_present':[n for n in sources if 'gateway_' in n or 'thread_operations' in n],
        'inspector_image':IMAGE,'no_deployed_code_executed':True}
    (OUT/'azure-source-proof.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__':main()
