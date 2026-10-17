"""Add only L6, preserving four original wheels, installed files and locks.

All build/runtime inputs are local. No network, checkout mounts, backend reads,
old-module monkeypatches, installs in the host venv, or automatic Docker setup.
The old stdlib builder and safe-file helpers are imported without modification.
"""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

LAB = Path(__file__).resolve().parents[1]
L4_IMAGE = "sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9"
L4_VERSION = "0.4.0+lab.27a62d14058a"
L4_SHA256 = "764352dc77a7c467f6c8c3162a4f93e4fcca0e21b7933db0e2a4557669dcfb5d"
L4_MANIFEST_SHA256 = "30eea94fbaea60c66359aeb89ddcd01e5b77cdeb04b232cf3a2c5ffe479935b7"
L4_DIRECTORY = LAB / "l4-runs/l4-final04-20260916/build-a"
BUILDER_INPUTS = {
    "l4_build.py": "74a711889124574199800ac53d5f7625a92d81152b86a68c1ca25363feeb59b8",
    "build_artifacts.py": "7365e575dd4da80482c42865c18311f8e6a985d7b08cbde7906b1148040ba484",
}


def _old_helpers():
    path = Path(__file__).with_name("l4_build.py")
    spec = importlib.util.spec_from_file_location("xoc_l6_immutable_l4_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for filename, expected in BUILDER_INPUTS.items():
        module.checked_file(Path(__file__).with_name(filename), expected)
    return module


OLD = _old_helpers()
digest, encoded, checked_file = OLD.digest, OLD.encoded, OLD.checked_file
relative_file, new_directory = OLD.relative_file, OLD.new_directory
extract_evidence, infrastructure_error = OLD.extract_evidence, OLD.infrastructure_error
LIMITS = tuple(OLD.LIMITS)
LOCKS = dict(OLD.LOCKS)
CORE_SHA256 = OLD.CORE_SHA256
PRESERVED_WHEELS = {**OLD.C1_WHEELS,
    "xoc_mcp_lab_l4-" + L4_VERSION + "-py3-none-any.whl": L4_SHA256}
PHASES = ("tests", "smoke", "recovery", "benchmark", "benchmark-l4", "provenance")


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encoded(value))


def validate_l4(directory):
    manifest = json.loads(checked_file(directory / "artifact-manifest.json", L4_MANIFEST_SHA256))
    if {e["filename"]: e["sha256"] for e in manifest["artifacts"]} != PRESERVED_WHEELS:
        raise ValueError("L6 requires the exact four final04 wheels")
    for filename, expected in PRESERVED_WHEELS.items():
        checked_file(directory / filename, expected)
    checked_file(directory / "gateway-manifest.json", OLD.GATEWAY_MANIFEST_SHA256)
    checked_file(directory / "source-manifest.json", manifest["source_manifest_sha256"])
    return manifest


def copy_l4(source, destination):
    validate_l4(source)
    new_directory(destination)
    for filename in (*PRESERVED_WHEELS, "artifact-manifest.json", "gateway-manifest.json", "source-manifest.json"):
        shutil.copyfile(source / filename, destination / filename)


def export(destination):
    validate_l4(L4_DIRECTORY)
    files = {}
    for path in sorted((LAB / "xoc_mcp_lab_l6").glob("*.py")):
        if path.is_symlink():
            raise ValueError("Linked L6 source")
        files[path.relative_to(LAB).as_posix()] = path.read_bytes()
    for path in sorted((LAB / "tests").glob("test_l6*.py")):
        if path.is_symlink():
            raise ValueError("Linked L6 test")
        files["xoc_mcp_lab_l6/tests/" + path.name] = path.read_bytes()
    required = ("supervisor", "control", "pool", "server", "probe", "harness", "integrity", "__init__")
    if any("xoc_mcp_lab_l6/" + name + ".py" not in files for name in required):
        raise ValueError("Incomplete L6 source export")
    if not any("/tests/" in p for p in files):
        raise ValueError("L6 tests are required")
    # Installed packaging tests use these exact snapshots, never a checkout.
    for name in ("l6_build.py", "Dockerfile.l6", *BUILDER_INPUTS):
        path = Path(__file__).with_name(name)
        files["xoc_mcp_lab_l6/_build/" + name] = path.read_bytes()
    for name in ("l6.ps1", "l6_preservation.ps1"):
        files["xoc_mcp_lab_l6/_build/" + name] = (LAB / "scripts" / name).read_bytes()
    source = dict(schema="xoc-l6-source-v1", lab_only=True, l4_image=L4_IMAGE,
                  preserved_wheels=PRESERVED_WHEELS, builder_inputs=BUILDER_INPUTS,
                  files={name: digest(data) for name, data in sorted(files.items())})
    new_directory(destination)
    for name, data in files.items():
        path = relative_file(destination / "l6", name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    write_new(destination / "source-manifest.json", source)
    copy_l4(L4_DIRECTORY, destination / "l4")
    (destination / "locks").mkdir()
    for filename, expected in LOCKS.items():
        (destination / "locks" / filename).write_bytes(checked_file(LAB / "packaging" / filename, expected))
    return source


def preserved_files(directory):
    files = {}
    for filename in PRESERVED_WHEELS:
        with zipfile.ZipFile(directory / filename) as archive:
            for member in archive.namelist():
                if member.endswith(".dist-info/RECORD"):
                    continue
                relative_file(Path("/installed"), member)
                sha = digest(archive.read(member))
                if member in files and files[member] != sha:
                    raise ValueError("Preserved wheel collision")
                files[member] = sha
    return dict(sorted(files.items()))


def build(staging, destination):
    prior = validate_l4(staging / "l4")
    for filename, expected in LOCKS.items():
        checked_file(staging / "locks" / filename, expected)
    raw = (staging / "source-manifest.json").read_bytes()
    source = json.loads(raw)
    if (source.get("schema") != "xoc-l6-source-v1" or source.get("l4_image") != L4_IMAGE
            or source.get("preserved_wheels") != PRESERVED_WHEELS
            or source.get("builder_inputs") != BUILDER_INPUTS):
        raise ValueError("Wrong L6 source manifest")
    files = {name: checked_file(relative_file(staging / "l6", name), sha)
             for name, sha in source["files"].items()}
    if any(not name.startswith("xoc_mcp_lab_l6/") or name.endswith((".pth", ".pyc", ".pyd")) for name in files):
        raise ValueError("L6 cannot replace an existing package")
    version = "0.6.0+lab." + digest(raw)[:12]
    marker = dict(schema="xoc-l6-artifact-v1", lab_only=True, version=version,
                  l4_image=L4_IMAGE, l4_sha256=L4_SHA256, core_sha256=CORE_SHA256,
                  sdk="1.30.0", protocol="2025-11-25", source_manifest_sha256=digest(raw),
                  files=source["files"], preserved_files=preserved_files(staging / "l4"), locks=LOCKS)
    files["xoc_mcp_lab_l6/_artifact.json"] = encoded(marker)
    new_directory(destination)
    for filename, expected in PRESERVED_WHEELS.items():
        (destination / filename).write_bytes(checked_file(staging / "l4" / filename, expected))
    artifact = OLD.builder().wheel("xoc_mcp_lab_l6", version, files,
                                  ["xoc-mcp-lab-l4==" + L4_VERSION], destination)
    manifest = dict(schema="xoc-l6-build-v1", lab_only=True, fixture_only=True,
                    l4_image=L4_IMAGE, base_image=OLD.BASE_IMAGE, core_sha256=CORE_SHA256,
                    l4_sha256=L4_SHA256, sdk="1.30.0", protocol="2025-11-25",
                    source_manifest_sha256=digest(raw), locks=LOCKS,
                    builder="Unmodified L2 stdlib wheel(); ZIP_STORED; fixed epoch/modes/order",
                    builder_inputs=BUILDER_INPUTS, artifacts=[*prior["artifacts"], artifact])
    write_new(destination / "artifact-manifest.json", manifest)
    (destination / "source-manifest.json").write_bytes(raw)
    shutil.copyfile(staging / "l4/gateway-manifest.json", destination / "gateway-manifest.json")
    return manifest


def validate_build(directory):
    manifest = json.loads((directory / "artifact-manifest.json").read_bytes())
    if (manifest.get("schema") != "xoc-l6-build-v1" or manifest.get("l4_image") != L4_IMAGE
            or manifest.get("locks") != LOCKS or len(manifest["artifacts"]) != 5
            or {e["filename"]: e["sha256"] for e in manifest["artifacts"][:4]} != PRESERVED_WHEELS):
        raise ValueError("L6 must preserve exactly four wheels and add one")
    last = manifest["artifacts"][-1]
    if not re.fullmatch(r"xoc_mcp_lab_l6-0\.6\.0\+lab\.[a-f0-9]{12}-py3-none-any\.whl", last["filename"]):
        raise ValueError("Wrong L6 wheel selection")
    for entry in manifest["artifacts"]:
        checked_file(relative_file(directory, entry["filename"]), entry["sha256"])
    checked_file(directory / "source-manifest.json", manifest["source_manifest_sha256"])
    return manifest


def compare(first, second):
    validate_build(first)
    validate_build(second)
    a = {p.name: digest(p.read_bytes()) for p in first.iterdir() if p.is_file()}
    b = {p.name: digest(p.read_bytes()) for p in second.iterdir() if p.is_file()}
    if a != b:
        raise ValueError("Independent L6 builds differ")
    return dict(lab_only=True, independent_builds_identical=True, preserved_wheels=PRESERVED_WHEELS, files=a)


def docker(*arguments, **kwargs):
    kwargs.setdefault("timeout", 30)
    return subprocess.run(["docker", *map(str, arguments)], check=True, **kwargs)


def inspect_image(image):
    return json.loads(docker("image", "inspect", image, capture_output=True, text=True).stdout)[0]


def safe_name(name):
    if not re.fullmatch(r"xoc-l6-[a-z0-9][a-z0-9-]{2,90}", name):
        raise ValueError("L6 must own its distinct container name")


def validate_owner(raw, owner):
    safe_name(owner["name"])
    if (not re.fullmatch(r"[a-f0-9]{64}", owner["id"])
            or raw["Id"] != owner["id"] or raw["Name"] != "/" + owner["name"]
            or raw["Config"].get("Labels", {}).get("io.xoc.lab") != "l6"
            or raw["Config"].get("Labels", {}).get("io.xoc.l6.owner") != owner["name"]):
        raise ValueError("Refusing to operate on an unowned container")


def container_controls(owner):
    raw = json.loads(docker("inspect", owner["id"], capture_output=True, text=True).stdout)[0]
    validate_owner(raw, owner)
    host, config = raw["HostConfig"], raw["Config"]
    if (host["NetworkMode"] != "none" or host["Memory"] != 536870912
            or host["NanoCpus"] != 2000000000 or host["PidsLimit"] != 128
            or host["ReadonlyRootfs"] is not True or config["User"] != "10001:10001"
            or host.get("Binds") or host.get("PortBindings")
            or any(m["Type"] == "bind" for m in raw.get("Mounts", []))
            or "ALL" not in host.get("CapDrop", [])
            or not any(s.startswith("no-new-privileges") for s in host.get("SecurityOpt", []))
            or host.get("Tmpfs") != {"/tmp": "rw,nosuid,nodev,size=128m,mode=1777"}):
        raise ValueError("L6 container resource/isolation mismatch")
    return dict(id=raw["Id"], name=raw["Name"], image=raw["Image"],
                state={key: raw["State"].get(key) for key in ("Status", "Running", "ExitCode", "OOMKilled", "Error")},
                controls=dict(network="none", memory=host["Memory"], nanocpus=host["NanoCpus"],
                              pids_limit=host["PidsLimit"], user=config["User"], readonly=True,
                              bind_mounts=[], port_bindings={}, cap_drop=host["CapDrop"],
                              security_opt=host["SecurityOpt"], tmpfs=host["Tmpfs"]))


def create_owned(image, name, command, interactive=False):
    safe_name(name)
    created = docker("create", "--name", name, "--label", "io.xoc.lab=l6",
                     "--label", "io.xoc.l6.owner=" + name, *LIMITS,
                     *(["-i"] if interactive else []), "--entrypoint", command[0],
                     image, *command[1:], capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{64}", created):
        raise ValueError("Unrecognized owned container ID")
    return dict(id=created, name=name, lab_only=True)


BUILD_BOOTSTRAP = '''import io, pathlib, sys, tarfile
root=pathlib.Path('/tmp/l6-builder'); root.mkdir()
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()),mode='r:') as archive:
    for member in archive.getmembers():
        target=root/member.name
        if not member.isfile() or not target.resolve().is_relative_to(root): raise ValueError('unsafe build input')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(archive.extractfile(member).read())
sys.path.insert(0,str(root/'tools'))
import l6_build
l6_build.OLD.require_linux_target()
output=root/'output'
l6_build.build(root/'sources',output)
buffer=io.BytesIO()
with tarfile.open(fileobj=buffer,mode='w:') as archive:
    for path in sorted(output.iterdir()): archive.add(path,arcname=path.name,recursive=False)
sys.stdout.buffer.write(buffer.getvalue())
'''


def docker_build(staging, destination, image, name):
    safe_name(name)
    owner_path = destination.with_name(destination.name + "-owner.json")
    process_path = destination.with_name(destination.name + "-process.json")
    if destination.exists() or process_path.exists() or owner_path.exists():
        raise FileExistsError(destination)
    actual = inspect_image(image)["Id"]
    if actual != L4_IMAGE:
        raise ValueError("Builder requires exact local final04 L4 image")
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise ValueError("Linked staging input")
            if path.is_file():
                archive.add(path, arcname="sources/" + path.relative_to(staging).as_posix(), recursive=False)
        for filename in ("l6_build.py", *BUILDER_INPUTS):
            archive.add(Path(__file__).with_name(filename), arcname="tools/" + filename, recursive=False)
    owner = create_owned(actual, name, ["/var/lang/bin/python", "-I", "-B", "-c", BUILD_BOOTSTRAP], True)
    write_new(owner_path, dict(owner, operation="wheel_build"))
    evidence = dict(owner=owner, before=container_controls(owner), operation="wheel_build", recovery_proof=False)
    try:
        result = docker("start", "--attach", "--interactive", owner["id"],
                        input=payload.getvalue(), capture_output=True, timeout=180)
        # Reject the entire archive before writing any build output.
        entries = {}
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                if (not member.isfile() or Path(member.name).name != member.name
                        or member.name in entries or member.size > 32 * 1024 * 1024):
                    raise ValueError("Unsafe build output")
                entries[member.name] = archive.extractfile(member).read()
        new_directory(destination)
        for filename, data in entries.items():
            (destination / filename).write_bytes(data)
        return validate_build(destination)
    except Exception as error:
        evidence["failure"] = dict(error_type=type(error).__name__, infrastructure=infrastructure_error(error))
        raise
    finally:
        observed = container_controls(owner)
        if observed["state"]["Running"]:
            docker("stop", "--time", "2", owner["id"], capture_output=True)
        evidence["final"] = container_controls(owner)
        docker("rm", owner["id"], capture_output=True)
        evidence["owned_container_removed"] = True
        write_new(process_path, evidence)


def context(artifacts, destination):
    validate_build(artifacts)
    new_directory(destination)
    target = destination / "l6"
    target.mkdir()
    for path in sorted(artifacts.iterdir()):
        if path.is_file():
            shutil.copyfile(path, target / path.name)
    for filename in ("l6_build.py", *BUILDER_INPUTS):
        shutil.copyfile(Path(__file__).with_name(filename), target / filename)
    shutil.copyfile(Path(__file__).with_name("Dockerfile.l6"), destination / "Dockerfile.l6")
    return dict(lab_only=True, l4_image=L4_IMAGE,
                files={p.relative_to(destination).as_posix(): digest(p.read_bytes())
                       for p in sorted(destination.rglob("*")) if p.is_file()})


def install():
    """Image-build only: pip installs the fifth wheel, not core/L4/dependencies."""
    OLD.require_linux_target()
    source = Path("/offline-l6")
    manifest = validate_build(source)
    pinned = preserved_files(source)
    site = Path("/opt/xoc-l2/lib/python3.11/site-packages")
    for relative, expected in pinned.items():
        checked_file(relative_file(site, relative), expected)
    entry = manifest["artifacts"][-1]
    wheel = source / entry["filename"]
    metadata = entry["filename"].removesuffix("-py3-none-any.whl") + ".dist-info/"
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            relative_file(site, name)
            if not name.startswith(("xoc_mcp_lab_l6/", metadata)) or name.endswith((".pth", ".pyc", ".pyd")):
                raise ValueError("L6 wheel attempted to modify foreign package")
    python = "/opt/xoc-l2/bin/python"
    subprocess.run([python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
                    "install", "--no-index", "--no-deps", "--no-cache-dir", "--no-compile", str(wheel)], check=True)
    for relative, expected in pinned.items():
        checked_file(relative_file(site, relative), expected)
    subprocess.run([python, "-I", "-B", "-m", "pip", "--isolated", "check"], check=True)
    subprocess.run([python, "-I", "-B", "-c",
                    "from xoc_mcp_lab_l6.integrity import require_l6_installed; require_l6_installed()"], check=True)


KEEPALIVE = '''import json, pathlib, subprocess, sys, time
root=pathlib.Path('/tmp/results'); root.mkdir(exist_ok=False)
with (root/'runner.log').open('w',encoding='utf-8') as log:
    result=subprocess.run(sys.argv[1:],stdin=subprocess.DEVNULL,stdout=log,stderr=log)
(root/'container-process.json').write_text(json.dumps({'exit_code':result.returncode,'direct_child_reaped':True,'not_application_recovery_proof':True})+'\\n')
print('XOC_L6_EXPORT_READY',flush=True)
deadline=time.monotonic()+1800
while not (root/'export-complete').exists() and time.monotonic()<deadline: time.sleep(.1)
sys.exit(result.returncode)
'''


def export_results(owner, destination):
    container_controls(owner)
    result = docker("exec", owner["id"], "/opt/xoc-l2/bin/python", "-I", "-B", "-c",
                    OLD.EXPORT_RESULTS, capture_output=True)
    return extract_evidence(result.stdout, destination)


def cleanup_owned(owner, destination, manual=False):
    before = container_controls(owner)
    if before["state"]["Running"]:
        docker("stop", "--time", "2", owner["id"], capture_output=True)
    final = container_controls(owner)
    if final["state"]["Running"]:
        raise RuntimeError("Owned container still running after bounded stop")
    docker("rm", owner["id"], capture_output=True)
    result = dict(lab_only=True, owner=owner, before=before, final=final,
                  owned_container_removed=True, foreign_containers_touched=False,
                  manual_stop=manual, recovery_proof=False,
                  application_cleanup_proof="harness evidence, not container removal")
    write_new(destination / ("manual-container-cleanup.json" if manual else "container-cleanup.json"), result)
    return result


def run(image, name, phase, destination):
    safe_name(name)
    if phase not in PHASES:
        raise ValueError("Unknown L6 phase")
    new_directory(destination)
    actual = inspect_image(image)["Id"]
    python = "/opt/xoc-l2/bin/python"
    command = [python, "-I", "-B", "-m", "xoc_mcp_lab_l6.harness", "--mode", phase,
               "--output", "/tmp/results/" + phase + ".json"]
    owner = create_owned(actual, name, [python, "-I", "-B", "-c", KEEPALIVE, *command])
    write_new(destination / "owned-container.json", owner)
    exported = False
    try:
        before = container_controls(owner)
        write_new(destination / "container-before.json", before)
        docker("start", owner["id"], capture_output=True)
        deadline = time.monotonic() + 1800
        while True:
            logs = docker("logs", "--tail", "3", owner["id"], capture_output=True, text=True)
            if "XOC_L6_EXPORT_READY" in logs.stdout:
                break
            if not container_controls(owner)["state"]["Running"]:
                raise RuntimeError("Owned L6 container stopped before evidence export")
            if time.monotonic() >= deadline:
                raise TimeoutError("L6 phase exceeded finite 1800 second runner cap")
            time.sleep(1)
        export = export_results(owner, destination)
        exported = True
        write_new(destination / "evidence-export.json", export)
        write_new(destination / "container-before-exit.json", container_controls(owner))
        docker("exec", owner["id"], python, "-I", "-B", "-c",
               "from pathlib import Path; Path('/tmp/results/export-complete').touch(exist_ok=False)", capture_output=True)
        exit_code = int(docker("wait", owner["id"], capture_output=True, text=True).stdout.strip())
        final = container_controls(owner)
        write_new(destination / "container-final.json", final)
        recorded = json.loads((destination / "container-process.json").read_bytes())["exit_code"]
        if exit_code != recorded or exit_code:
            raise RuntimeError("L6 phase failed; retained evidence and exit code " + str(exit_code))
        return dict(lab_only=True, phase=phase, exit_code=exit_code, output=str(destination),
                    copied_before_container_exit=True, image=actual)
    except Exception as error:
        export_error = None
        if not exported:
            try:
                # Partial data may still be changing; a separate snapshot never
                # replaces a successfully retained artifact from this run.
                export_results(owner, destination / "failure-snapshot")
                exported = True
            except Exception as failure:
                export_error = dict(error_type=type(failure).__name__, infrastructure=infrastructure_error(failure))
        write_new(destination / "runner-failure.json", dict(
            lab_only=True, error_type=type(error).__name__, infrastructure=infrastructure_error(error),
            partial_evidence_copied=exported, export_error=export_error,
            retained_container_for_manual_export=not exported))
        raise
    finally:
        # No --rm, no name matching, and no removal as recovery evidence.
        # If export itself failed, retain the owned container for explicit Stop.
        if exported:
            cleanup_owned(owner, destination)


def stop(run_directory):
    """Manual finite cleanup of exact recorded runtime IDs in one L6 RunId."""
    marker = json.loads((run_directory / "run.json").read_bytes())
    if marker.get("schema") != "xoc-l6-run-v1" or marker.get("run_id") != run_directory.name:
        raise ValueError("Not an owned L6 run directory")
    results = []
    # Builders also have exact ownership records. Retain any completed partial
    # artifact files before manual cleanup; source snapshots already live here.
    for path in sorted(run_directory.glob("build-*-owner.json")):
        if path.is_symlink():
            raise ValueError("Linked builder owner file")
        prefix = path.name.removesuffix("-owner.json")
        process_path = path.with_name(prefix + "-process.json")
        manual_path = path.with_name(prefix + "-manual-stop")
        if process_path.exists() or manual_path.exists():
            continue
        owner = json.loads(path.read_bytes())
        if not owner["name"].startswith("xoc-l6-" + marker["run_id"] + "-"):
            raise ValueError("Builder owner belongs to another RunId")
        observed = container_controls(owner)
        new_directory(manual_path)
        if observed["state"]["Running"]:
            try:
                script = OLD.EXPORT_RESULTS.replace("/tmp/results", "/tmp/l6-builder/output")
                payload = docker("exec", owner["id"], "/var/lang/bin/python", "-I", "-B", "-c", script,
                                 capture_output=True).stdout
                extract_evidence(payload, manual_path / "partial-artifacts")
            except Exception as error:
                write_new(manual_path / "partial-export-unavailable.json", dict(
                    error_type=type(error).__name__, infrastructure=infrastructure_error(error),
                    input_staging_preserved=True, application_experiment=False))
        results.append(cleanup_owned(owner, manual_path, manual=True))
    for path in sorted((run_directory / "results").glob("*/owned-container.json")):
        if path.is_symlink() or not path.resolve().is_relative_to(run_directory.resolve()):
            raise ValueError("Linked owner file")
        if (path.parent / "container-cleanup.json").exists() or (path.parent / "manual-container-cleanup.json").exists():
            continue
        owner = json.loads(path.read_bytes())
        if not owner["name"].startswith("xoc-l6-" + marker["run_id"] + "-"):
            raise ValueError("Container owner belongs to another RunId")
        state = container_controls(owner)
        snapshot = path.parent / "manual-stop-snapshot"
        if snapshot.exists():
            raise FileExistsError(snapshot)
        if state["state"]["Running"]:
            export_results(owner, snapshot)
        else:
            write_new(snapshot / "export-unavailable.json", dict(
                reason="container_already_stopped_tmpfs_unavailable", prior_evidence_preserved=True))
        results.append(cleanup_owned(owner, path.parent, manual=True))
    return dict(lab_only=True, manual_stop=True, containers=results)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export"); exp.add_argument("--output", type=Path, required=True)
    make = sub.add_parser("build")
    make.add_argument("--staging", type=Path, required=True); make.add_argument("--output", type=Path, required=True)
    target = sub.add_parser("docker-build")
    target.add_argument("--staging", type=Path, required=True); target.add_argument("--output", type=Path, required=True)
    target.add_argument("--image", default=L4_IMAGE); target.add_argument("--name", required=True)
    equal = sub.add_parser("compare")
    equal.add_argument("--first", type=Path, required=True); equal.add_argument("--second", type=Path, required=True)
    ctx = sub.add_parser("context")
    ctx.add_argument("--artifacts", type=Path, required=True); ctx.add_argument("--output", type=Path, required=True)
    sub.add_parser("install")
    execute = sub.add_parser("run")
    execute.add_argument("--image", required=True); execute.add_argument("--name", required=True)
    execute.add_argument("--phase", choices=PHASES, required=True)
    execute.add_argument("--output", type=Path, required=True)
    cleanup = sub.add_parser("stop"); cleanup.add_argument("--run-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export": result = export(args.output)
    elif args.command == "build": result = build(args.staging, args.output)
    elif args.command == "docker-build": result = docker_build(args.staging, args.output, args.image, args.name)
    elif args.command == "compare": result = compare(args.first, args.second)
    elif args.command == "context": result = context(args.artifacts, args.output)
    elif args.command == "run": result = run(args.image, args.name, args.phase, args.output)
    elif args.command == "stop": result = stop(args.run_directory)
    else: install(); return
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
