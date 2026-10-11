"""Lee paquete Azure con Entra en memoria y compara fuentes, sin claves/SAS."""
import hashlib, io, json, subprocess, urllib.request, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
REPO=ROOT.parent/'XOC_MICROSERVICIO_AGENTES'
OUT=ROOT/'tests/ic1/.build/ac2r'
SUB='884feb5e-8492-4d88-8777-6391393372f5'

def main():
    result=subprocess.run(['az.cmd','account','get-access-token','--resource','https://storage.azure.com/',
        '--subscription',SUB,'-o','json'],capture_output=True,text=True,timeout=45)
    if result.returncode: raise RuntimeError('ENTRA_TOKEN_UNAVAILABLE')
    token=json.loads(result.stdout)['accessToken']
    request=urllib.request.Request('https://rgsophiapoc8952.blob.core.windows.net/scm-releases/scm-latest-prod-a-xoc-cons.zip',
        headers={'Authorization':'Bearer '+token,'x-ms-version':'2023-11-03'})
    with urllib.request.urlopen(request,timeout=45) as response:
        artifact=response.read(100_000_001)
        metadata={k:response.headers.get(k) for k in ('ETag','Last-Modified','Content-Length')}
    del token, request, result
    if len(artifact)>100_000_000: raise ValueError('ARTIFACT_TOO_LARGE')
    OUT.mkdir(parents=True,exist_ok=True)
    if artifact[:4] == b'hsqs':
        # Azure Linux can publish a SquashFS image with a .zip suffix. Preserve
        # the downloaded binary only in ignored evidence, never in Git.
        (OUT/'azure-deployed.squashfs').write_bytes(artifact)
        metadata.update(format='squashfs',sha256=hashlib.sha256(artifact).hexdigest())
        (OUT/'azure-artifact-metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        print(json.dumps(metadata))
        return
    archive=zipfile.ZipFile(io.BytesIO(artifact))
    def source(path):
        return path=='function_app.py' or (path.startswith(('sophia_agent/','shared/','victor_agent/','domain/')) and path.endswith('.py'))
    sources={n:archive.read(n) for n in archive.namelist() if source(n)}
    hashes={n:hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest() for n,b in sources.items()}
    revisions=subprocess.check_output(['git','rev-list','--all','--max-count=150'],cwd=REPO,text=True).splitlines()
    comparisons=[]
    for commit in revisions:
        tree=subprocess.check_output(['git','ls-tree','-r',commit],cwd=REPO,text=True).splitlines()
        git_sources={row.split('\t',1)[1]:row.split()[2] for row in tree if '\t' in row and source(row.split('\t',1)[1])}
        different=sorted(n for n in set(hashes)|set(git_sources) if hashes.get(n)!=git_sources.get(n))
        comparisons.append({'commit':commit,'different':different,'match_count':sum(hashes.get(n)==h for n,h in git_sources.items())})
    best=min(comparisons,key=lambda x:len(x['different']))
    snapshot=OUT/'azure-source'
    snapshot.mkdir(parents=True,exist_ok=True)
    # Only SOPHIA source selected for inspection. Never unpack secrets, deps,
    # executable artifacts or VICTOR source, and never import downloaded code.
    for name,data in sources.items():
        if name=='function_app.py' or name.startswith(('sophia_agent/','shared/')):
            target=(snapshot/name).resolve()
            if not target.is_relative_to(snapshot.resolve()): raise ValueError('INVALID_ARCHIVE_PATH')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
    report={'artifact_sha256':hashlib.sha256(artifact).hexdigest(),'metadata':metadata,
        'deployed_source_status':'EXACT_COMMIT' if not best['different'] else 'DERIVED',
        'closest':best,'source_count':len(sources),
        'source_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in sources.items()},
        'governed_files_present':[n for n in sources if 'gateway_' in n or 'thread_operations' in n],
        'no_package_or_secrets_persisted':True}
    (OUT/'azure-source-proof.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__': main()
