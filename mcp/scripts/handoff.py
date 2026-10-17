"""LAB_ONLY transfer tooling. No cloud, source-control writes, or business changes."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = "public.ecr.aws/lambda/python@sha256:cc940054e77d4a160c611a20c09cb8ab9325c09d72555a164329eb3e46957d80"
CORE = "df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41"
def module(name):
    spec=importlib.util.spec_from_file_location("handoff_"+name,ROOT/"packaging"/(name+".py"))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value
def same_copy(source,target):
    data=source.read_bytes()
    if target.exists():
        if target.read_bytes()!=data: raise ValueError("Existing input differs: "+str(target))
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("xb") as stream:stream.write(data)
def prepare():
    destinations={"c1":"l3-runs/l3-c1-final-build/build-a","l4":"l4-runs/l4-final04-20260916/build-a"}
    for label,rel in destinations.items():
        origin=ROOT/"artifacts"/label
        manifest=json.loads((origin/"artifact-manifest.json").read_text())
        for entry in manifest["artifacts"]:
            file=ROOT/"artifacts/wheels"/entry["filename"]
            if hashlib.sha256(file.read_bytes()).hexdigest()!=entry["sha256"]:raise ValueError("Artifact drift")
            same_copy(file,ROOT/rel/file.name)
        for file in origin.glob("*.json"):same_copy(file,ROOT/rel/file.name)
    same_copy(ROOT/"fixtures/reference.json",ROOT/"reports/l2-reference-windows.json")
    same_copy(ROOT/"fixtures/reference.json",ROOT/"l4-runs/l4-final04-20260916/execution/offline/reference.json")
    same_copy(ROOT/"fixtures/l4-baseline-tests.json",ROOT/"l4-runs/l4-final04-20260916/results/tests/tests.json")
    return {"prepared":True,"runtime_checkout_required":False}
def verify_core(backend,output):
    b=module("build_artifacts")
    output.mkdir(parents=True,exist_ok=False)
    b.export(backend,output/"sources")
    result=b.build(output/"sources",output/"wheels",
                   ROOT/"packaging/requirements-linux-runtime.lock.txt",
                   ROOT/"packaging/requirements-linux-test.lock.txt")
    wheel=next((output/"wheels").glob("xoc_tool_gateway_core-*.whl"))
    actual=hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual!=CORE:raise ValueError("Canonical core mismatch")
    return {"core_sha256":actual,"source":"exact Git blobs; not working-tree copies","other_wheels":"development export only, NOT baseline inputs"}
def build(output):
    prepare()
    b=module("l6_build")
    output.mkdir(parents=True,exist_ok=False)
    b.export(output/"sources")
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode="w:") as archive:
        for path in sorted((output/"sources").rglob("*")):
            if path.is_symlink():raise ValueError("Linked build source")
            if path.is_file():archive.add(path,arcname="sources/"+path.relative_to(output/"sources").as_posix(),recursive=False)
        for name in ("l6_build.py","l4_build.py","build_artifacts.py"):
            archive.add(ROOT/"packaging"/name,arcname="tools/"+name,recursive=False)
    subprocess.run(["docker","image","inspect",BASE],check=True,stdout=subprocess.DEVNULL)
    command=["docker","run","--rm","--name","xoc-handoff-builder-"+uuid4().hex[:12],*b.LIMITS,
             "-i","--entrypoint","/var/lang/bin/python",BASE,"-I","-B","-c",b.BUILD_BOOTSTRAP]
    result=subprocess.run(command,input=payload.getvalue(),capture_output=True,timeout=180,check=True)
    target=output/"artifacts";target.mkdir()
    with tarfile.open(fileobj=io.BytesIO(result.stdout),mode="r:") as archive:
        for entry in archive.getmembers():
            if not entry.isfile() or Path(entry.name).name!=entry.name or entry.size>8*1024*1024:raise ValueError("Unsafe build output")
            with (target/entry.name).open("xb") as stream:stream.write(archive.extractfile(entry).read())
    manifest=b.validate_build(target)
    return {"artifacts":str(target),"l6":manifest["artifacts"][-1],"source_manifest":manifest["source_manifest_sha256"]}
def context(artifacts,wheelhouse,output):
    prepare()
    b=module("l6_build")
    b.validate_build(artifacts)
    # The original context builder verifies each public wheel against the unchanged locks.
    b.OLD.context(ROOT/"l4-runs/l4-final04-20260916/build-a",wheelhouse.parent,output)
    target=output/"l6";target.mkdir()
    for path in artifacts.iterdir():
        if path.is_file():same_copy(path,target/path.name)
    for name in ("l6_build.py","l4_build.py","build_artifacts.py"):same_copy(ROOT/"packaging"/name,target/name)
    same_copy(ROOT/"packaging/Dockerfile.handoff",output/"Dockerfile.handoff")
    return {"context":str(output),"offline_install":True}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    s=p.add_subparsers(dest="action",required=True)
    s.add_parser("prepare")
    q=s.add_parser("verify-core");q.add_argument("--backend",type=Path,required=True);q.add_argument("--output",type=Path,required=True)
    q=s.add_parser("build");q.add_argument("--output",type=Path,required=True)
    q=s.add_parser("context");q.add_argument("--artifacts",type=Path,required=True);q.add_argument("--wheelhouse",type=Path,required=True);q.add_argument("--output",type=Path,required=True)
    q=s.add_parser("compare");q.add_argument("--first",type=Path,required=True);q.add_argument("--second",type=Path,required=True)
    q=s.add_parser("run");q.add_argument("--image",required=True);q.add_argument("--phase",choices=("tests","smoke","recovery","provenance","benchmark","benchmark-l4"),required=True);q.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    if a.action=="prepare":result=prepare()
    elif a.action=="verify-core":result=verify_core(a.backend,a.output)
    elif a.action=="build":result=build(a.output)
    elif a.action=="context":result=context(a.artifacts,a.wheelhouse,a.output)
    elif a.action=="compare":result=module("l6_build").compare(a.first,a.second)
    else:result=module("l6_build").run(a.image,"xoc-l6-handoff-"+uuid4().hex[:12],a.phase,a.output)
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
