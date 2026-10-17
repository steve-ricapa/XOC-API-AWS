"""Additive, deterministic, offline L4 packaging and no-host-mount execution.

All output directories are new. No export reads the backend repository. C1's
three wheels, locks and installed files are immutable inputs, never rebuilt.
Docker operations are explicit CLI subcommands; importing this module is inert.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

LAB = Path(__file__).resolve().parents[1]
C1_IMAGE = "sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40"
BASE_IMAGE = "public.ecr.aws/lambda/python@sha256:cc940054e77d4a160c611a20c09cb8ab9325c09d72555a164329eb3e46957d80"
BASE_IMAGE_ID = "sha256:3dd4ffcf67d1302b4c9808bdc65fbc8dd952debfbaf373392bbd8fb7c9856420"
C1_MANIFEST_SHA256 = "eead716dd9170d7346380e7af647bceaf9211cacf864dedd2e58ed4bacfbd3eb"
GATEWAY_MANIFEST_SHA256 = "f2f7589c5b84cfc40ea81f4dca37d343da91513950bb2f877e01b2b99874a87c"
C1_DIRECTORY = LAB / "l3-runs/l3-c1-final-build/build-a"
C1_WHEELS = {
    "xoc_tool_gateway_core-0.0.0+lab.ad4ac1a764de-py3-none-any.whl": "df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41",
    "xoc_mcp_lab-0.2.0+lab.cdce81cbc014-py3-none-any.whl": "0f82b903e2b0bbbc5107a6371a2da95c83c0561c76f5b918a1cb1df1798da2ac",
    "xoc_mcp_lab_harness-0.2.0+lab.cdce81cbc014-py3-none-any.whl": "f41abe59883f1bc9311ed88f8582b1f31639bc2be65e1163412925c17bd2d736",
}
LOCKS = {
    "requirements-linux-runtime.lock.txt": "0b439be1007d484201613bae015e0ded27c8f38fa3b5338f8a7f41cc674e858b",
    "requirements-linux-test.lock.txt": "4336d71caffdd9aeeb4143a9b3b2bfc30b61409a89c205f5a9c2b50bd3cbaff5",
}
CORE_SHA256 = next(iter(C1_WHEELS.values()))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def new_directory(path):
    path.mkdir(parents=True, exist_ok=False)


def checked_file(path, expected):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or linked pinned input: " + str(path))
    data = path.read_bytes()
    if digest(data) != expected:
        raise ValueError("Pinned input hash mismatch: " + path.name)
    return data


def relative_file(root, relative):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe package path")
    return path


def validate_c1(directory):
    manifest = json.loads(checked_file(directory / "artifact-manifest.json", C1_MANIFEST_SHA256))
    checked_file(directory / "gateway-manifest.json", GATEWAY_MANIFEST_SHA256)
    actual = {e["filename"]: e["sha256"] for e in manifest["artifacts"]}
    if actual != C1_WHEELS or manifest.get("lab_only") is not True:
        raise ValueError("C1 manifest drift")
    for name, expected in C1_WHEELS.items():
        checked_file(directory / name, expected)
    return manifest


def copy_c1(source, destination):
    new_directory(destination)
    for name in (*C1_WHEELS, "artifact-manifest.json", "gateway-manifest.json"):
        shutil.copyfile(source / name, destination / name)


def export(destination):
    validate_c1(C1_DIRECTORY)
    package = LAB / "xoc_mcp_lab_l4"
    files = {p.relative_to(LAB).as_posix(): p.read_bytes()
             for p in sorted(package.glob("*.py")) if not p.is_symlink()}
    for path in sorted((LAB / "tests").glob("test_l4*.py")):
        if path.is_symlink():
            raise ValueError("Linked L4 test")
        files["xoc_mcp_lab_l4/tests/" + path.name] = path.read_bytes()
    if "xoc_mcp_lab_l4/integrity.py" not in files or not any("/tests/" in p for p in files):
        raise ValueError("Incomplete L4 sources")
    source = dict(schema="xoc-l4-source-v1", lab_only=True, c1_image=C1_IMAGE,
                  files={p: digest(data) for p, data in sorted(files.items())})
    new_directory(destination)
    for relative, data in files.items():
        path = relative_file(destination / "l4", relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (destination / "source-manifest.json").write_bytes(encoded(source))
    copy_c1(C1_DIRECTORY, destination / "c1")
    (destination / "locks").mkdir()
    for name, expected in LOCKS.items():
        (destination / "locks" / name).write_bytes(checked_file(LAB / "packaging" / name, expected))
    return source


def builder():
    path = Path(__file__).with_name("build_artifacts.py")
    spec = importlib.util.spec_from_file_location("xoc_l4_pinned_wheel_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def c1_files(directory):
    files = {}
    for name in C1_WHEELS:
        with zipfile.ZipFile(directory / name) as archive:
            for member in archive.namelist():
                # pip rewrites RECORD and adds INSTALLER/direct_url; all original
                # source and metadata bytes apart from RECORD remain unchanged.
                if member.endswith(".dist-info/RECORD"):
                    continue
                relative_file(Path("/installed"), member)
                value = digest(archive.read(member))
                if member in files and files[member] != value:
                    raise ValueError("C1 wheel file collision")
                files[member] = value
    return dict(sorted(files.items()))


def build(staging, destination):
    c1 = validate_c1(staging / "c1")
    for name, expected in LOCKS.items():
        checked_file(staging / "locks" / name, expected)
    raw = (staging / "source-manifest.json").read_bytes()
    source = json.loads(raw)
    if source.get("schema") != "xoc-l4-source-v1" or source.get("c1_image") != C1_IMAGE:
        raise ValueError("Wrong L4 source manifest")
    files = {name: checked_file(relative_file(staging / "l4", name), expected)
             for name, expected in source["files"].items()}
    if any(not name.startswith("xoc_mcp_lab_l4/") or not name.endswith(".py") for name in files):
        raise ValueError("L4 package must not replace C1 files")
    version = "0.4.0+lab." + digest(raw)[:12]
    marker = dict(schema="xoc-l4-artifact-v1", lab_only=True, version=version,
                  c1_image=C1_IMAGE, core_sha256=CORE_SHA256,
                  source_manifest_sha256=digest(raw), files=source["files"],
                  c1_files=c1_files(staging / "c1"), locks=LOCKS)
    files["xoc_mcp_lab_l4/_artifact.json"] = encoded(marker)
    new_directory(destination)
    for name, expected in C1_WHEELS.items():
        (destination / name).write_bytes(checked_file(staging / "c1" / name, expected))
    artifact = builder().wheel("xoc_mcp_lab_l4", version, files,
                              ["xoc-mcp-lab==0.2.0+lab.cdce81cbc014",
                               "xoc-mcp-lab-harness==0.2.0+lab.cdce81cbc014"], destination)
    manifest = dict(schema="xoc-l4-build-v1", lab_only=True, fixture_only=True,
                    c1_image=C1_IMAGE, base_image=BASE_IMAGE, core_sha256=CORE_SHA256,
                    source_manifest_sha256=digest(raw), locks=LOCKS,
                    builder="Unmodified L2 stdlib wheel(); ZIP_STORED; fixed epoch/modes/order",
                    artifacts=[*c1["artifacts"], artifact])
    (destination / "artifact-manifest.json").write_bytes(encoded(manifest))
    (destination / "source-manifest.json").write_bytes(raw)
    (destination / "gateway-manifest.json").write_bytes((staging / "c1/gateway-manifest.json").read_bytes())
    return manifest


def validate_build(directory):
    manifest = json.loads((directory / "artifact-manifest.json").read_bytes())
    if manifest.get("schema") != "xoc-l4-build-v1" or manifest.get("c1_image") != C1_IMAGE:
        raise ValueError("Wrong L4 build manifest")
    for entry in manifest["artifacts"]:
        checked_file(relative_file(directory, entry["filename"]), entry["sha256"])
    expected = {e["filename"]: e["sha256"] for e in manifest["artifacts"][:3]}
    if expected != C1_WHEELS or len(manifest["artifacts"]) != 4:
        raise ValueError("L4 did not preserve exact C1 wheels")
    return manifest


def compare(first, second):
    validate_build(first)
    validate_build(second)
    a = {p.name: digest(p.read_bytes()) for p in first.iterdir() if p.is_file()}
    b = {p.name: digest(p.read_bytes()) for p in second.iterdir() if p.is_file()}
    if a != b:
        raise ValueError("Independent L4 build hashes differ")
    return dict(lab_only=True, independent_builds_identical=True, files=a)


def require_linux_target():
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if (sys.version_info[:3] != (3, 11, 16) or platform.machine() != "x86_64"
            or release["ID"].strip('"') != "amzn" or release["VERSION_ID"].strip('"') != "2"):
        raise ValueError("L4 requires pinned AL2 / CPython 3.11.16 / x86_64")


def docker(*arguments, **kwargs):
    return subprocess.run(["docker", *map(str, arguments)], check=True, **kwargs)


def inspect_image(image):
    data = json.loads(docker("image", "inspect", image, capture_output=True, text=True).stdout)
    return data[0]


def safe_name(name):
    if not re.fullmatch(r"xoc-l4-[a-z0-9-]{3,80}", name):
        raise ValueError("L4 must own its distinct container name")


LIMITS = ["--platform", "linux/amd64", "--memory", "512m", "--cpus", "2",
          "--pids-limit", "128", "--user", "10001:10001", "--network", "none",
          "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
          "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777", "--workdir", "/tmp"]

BUILD_BOOTSTRAP = '''import io, pathlib, sys, tarfile
root=pathlib.Path('/tmp/l4-builder'); root.mkdir()
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()), mode='r:') as archive:
    for member in archive.getmembers():
        target=root/member.name
        if not member.isfile() or not target.resolve().is_relative_to(root): raise ValueError('unsafe build input')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(archive.extractfile(member).read())
sys.path.insert(0,str(root/'tools'))
import l4_build
l4_build.require_linux_target()
output=root/'output'
l4_build.build(root/'sources', output)
buffer=io.BytesIO()
with tarfile.open(fileobj=buffer,mode='w:') as archive:
    for path in sorted(output.iterdir()): archive.add(path,arcname=path.name,recursive=False)
sys.stdout.buffer.write(buffer.getvalue())
'''


def docker_build(staging, destination, image, name):
    safe_name(name)
    if destination.exists():
        raise FileExistsError(destination)
    actual = inspect_image(image)["Id"]
    if actual != C1_IMAGE and not (image == BASE_IMAGE and actual == BASE_IMAGE_ID):
        raise ValueError("Builder must use exact local C1 or pinned AL2 base")
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise ValueError("Linked staging input")
            if path.is_file():
                archive.add(path, arcname="sources/" + path.relative_to(staging).as_posix(), recursive=False)
        for filename in ("l4_build.py", "build_artifacts.py"):
            archive.add(Path(__file__).with_name(filename), arcname="tools/" + filename, recursive=False)
    result = docker("run", "--rm", "--name", name, *LIMITS, "-i", "--entrypoint",
                    "/var/lang/bin/python", actual, "-I", "-B", "-c", BUILD_BOOTSTRAP,
                    input=payload.getvalue(), capture_output=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        entries = {}
        for member in archive.getmembers():
            if not member.isfile() or Path(member.name).name != member.name:
                raise ValueError("Unsafe build output")
            entries[member.name] = archive.extractfile(member).read()
    new_directory(destination)
    for filename, data in entries.items():
        (destination / filename).write_bytes(data)
    return validate_build(destination)


def context(artifacts, preparation, destination):
    manifest = validate_build(artifacts)
    validate_c1(C1_DIRECTORY)
    new_directory(destination)
    offline, l4 = destination / "offline", destination / "l4"
    offline.mkdir(); l4.mkdir()
    copy_c1(C1_DIRECTORY, offline / "artifacts")
    for filename, expected in LOCKS.items():
        (offline / filename).write_bytes(checked_file(LAB / "packaging" / filename, expected))
    wheelhouse = offline / "wheelhouse"
    wheelhouse.mkdir()
    expected_wheels = {}
    for filename in LOCKS:
        for line in (offline / filename).read_text().splitlines():
            if line and not line.startswith("#"):
                requirement, sha = line.split(" --hash=sha256:")
                expected_wheels[sha] = requirement
    copied = set()
    for path in sorted((preparation / "wheelhouse").glob("*.whl")):
        sha = digest(path.read_bytes())
        if sha in expected_wheels:
            if "win_" in path.name or "win32" in path.name:
                raise ValueError("Windows wheel in Linux preparation")
            shutil.copyfile(path, wheelhouse / path.name)
            copied.add(sha)
    if copied != set(expected_wheels):
        raise ValueError("Incomplete pinned offline wheelhouse")
    shutil.copyfile(LAB / "packaging/install_offline.py", offline / "install_offline.py")
    shutil.copyfile(LAB / "reports/l2-reference-windows.json", offline / "reference.json")
    entry = manifest["artifacts"][-1]
    shutil.copyfile(artifacts / entry["filename"], l4 / entry["filename"])
    shutil.copyfile(artifacts / "artifact-manifest.json", l4 / "artifact-manifest.json")
    shutil.copyfile(Path(__file__), l4 / "l4_build.py")
    shutil.copyfile(LAB / "packaging/Dockerfile.l4", destination / "Dockerfile.l4")
    return dict(lab_only=True, files={p.relative_to(destination).as_posix(): digest(p.read_bytes())
                                   for p in sorted(destination.rglob("*")) if p.is_file()})


def install():
    """Image-build only. Add L4 without reinstalling or modifying C1/dependencies."""
    require_linux_target()
    offline = Path("/offline")
    validate_c1(offline / "artifacts")
    for filename, expected in LOCKS.items():
        checked_file(offline / filename, expected)
    python = Path("/opt/xoc-l2/bin/python")
    if not python.exists():
        subprocess.run([sys.executable, "-I", "-B", "/offline/install_offline.py"], check=True)
    pinned_files = c1_files(offline / "artifacts")
    site = Path("/opt/xoc-l2/lib/python3.11/site-packages")
    for relative, expected in pinned_files.items():
        checked_file(relative_file(site, relative), expected)
    manifest = json.loads(Path("/offline-l4/artifact-manifest.json").read_bytes())
    if manifest.get("c1_image") != C1_IMAGE or manifest.get("locks") != LOCKS:
        raise ValueError("Image L4 provenance mismatch")
    entry = manifest["artifacts"][-1]
    if (manifest.get("schema") != "xoc-l4-build-v1" or len(manifest["artifacts"]) != 4
            or {e["filename"]: e["sha256"] for e in manifest["artifacts"][:3]} != C1_WHEELS
            or not re.fullmatch(r"xoc_mcp_lab_l4-0\.4\.0\+lab\.[a-f0-9]{12}-py3-none-any\.whl", entry["filename"])):
        raise ValueError("Image L4 wheel selection mismatch")
    wheel = relative_file(Path("/offline-l4"), entry["filename"])
    checked_file(wheel, entry["sha256"])
    with zipfile.ZipFile(wheel) as archive:
        metadata_prefix = entry["filename"].removesuffix("-py3-none-any.whl") + ".dist-info/"
        for member in archive.namelist():
            relative_file(Path("/installed"), member)
            if (not member.startswith(("xoc_mcp_lab_l4/", metadata_prefix))
                    or member.endswith((".pth", ".pyc", ".pyd"))):
                raise ValueError("L4 wheel attempted to replace a foreign package")
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
                    "install", "--no-index", "--no-deps", "--no-cache-dir", "--no-compile", str(wheel)], check=True)
    for relative, expected in pinned_files.items():
        checked_file(relative_file(site, relative), expected)
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "--isolated", "check"], check=True)
    subprocess.run([str(python), "-I", "-B", "-c",
                    "from xoc_mcp_lab_l4.integrity import require_l4_installed; require_l4_installed()"], check=True)


KEEPALIVE = '''import json, pathlib, subprocess, sys, time
root=pathlib.Path('/tmp/results'); root.mkdir(exist_ok=False)
with (root/'runner.log').open('w',encoding='utf-8') as log:
    result=subprocess.run(sys.argv[1:],stdin=subprocess.DEVNULL,stdout=log,stderr=log)
(root/'container-process.json').write_text(json.dumps({'exit_code':result.returncode,'direct_child_reaped':True})+'\\n')
print('XOC_L4_EXPORT_READY',flush=True)
deadline=time.monotonic()+180
while not (root/'export-complete').exists() and time.monotonic()<deadline: time.sleep(.1)
sys.exit(result.returncode)
'''

EVIDENCE_LIMIT = 32 * 1024 * 1024
EXPORT_RESULTS = '''import pathlib, sys, tarfile
root=pathlib.Path('/tmp/results')
files=[]; total=0
for path in sorted(root.rglob('*')):
    if path.is_symlink() or not path.resolve().is_relative_to(root): raise ValueError('unsafe evidence path')
    if path.is_file():
        total+=path.stat().st_size
        if total>33554432: raise ValueError('evidence export exceeds 32 MiB')
        files.append(path)
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
    for path in files: archive.add(path,arcname=path.relative_to(root).as_posix(),recursive=False)
'''


def extract_evidence(payload, destination):
    """Receive a bounded regular-file tar; never use tar.extract or overwrite."""
    if len(payload) > EVIDENCE_LIMIT + 1024 * 1024:
        raise ValueError("Evidence archive exceeds limit")
    entries, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            relative = member.name
            if (not member.isfile() or relative in entries or not relative
                    or "\\" in relative or ":" in relative
                    or any(p in ("", ".", "..") for p in relative.split("/"))
                    or member.size < 0):
                raise ValueError("Unsafe evidence archive member")
            relative_file(destination, relative)
            total += member.size
            if total > EVIDENCE_LIMIT:
                raise ValueError("Evidence contents exceed limit")
            entries[relative] = archive.extractfile(member).read()
    # Validate the whole archive before writing even the first evidence file.
    for relative, data in entries.items():
        path = relative_file(destination, relative)
        if path.exists() and (not path.is_file() or path.read_bytes() != data):
            raise FileExistsError("Refusing to replace retained evidence")
    for relative, data in entries.items():
        path = relative_file(destination, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(data)
    return dict(files=len(entries), bytes=total,
                export_method="docker exec Python regular-file tar over binary stdout")


def export_results(container, destination):
    result = docker("exec", container, "/opt/xoc-l2/bin/python", "-I", "-B", "-c",
                    EXPORT_RESULTS, capture_output=True)
    return extract_evidence(result.stdout, destination)


def infrastructure_error(error):
    """Only bounded/redacted stderr of our infrastructure commands, not stdout."""
    if not isinstance(error, subprocess.CalledProcessError):
        return None
    stderr = error.stderr or b""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    stderr = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[JWS_REDACTED]", stderr)
    stderr = "\n".join("[ENVELOPE_OR_AUTH_DETAIL_REDACTED]" if any(
        token in line.lower() for token in ("x-xoc-lab-envelope", "authorization:", "bearer "))
        else line for line in stderr.splitlines())
    return dict(exit_code=error.returncode, stderr=stderr[:4000])


def container_controls(container):
    raw = json.loads(docker("inspect", container, capture_output=True, text=True).stdout)[0]
    host, config = raw["HostConfig"], raw["Config"]
    good = (host["NetworkMode"] == "none" and host["Memory"] == 536870912
            and host["NanoCpus"] == 2000000000 and host["PidsLimit"] == 128
            and host["ReadonlyRootfs"] is True and config["User"] == "10001:10001"
            and not host.get("Binds") and not host.get("PortBindings")
            and not any(m["Type"] == "bind" for m in raw.get("Mounts", []))
            and "ALL" in host.get("CapDrop", [])
            and any(s.startswith("no-new-privileges") for s in host.get("SecurityOpt", [])))
    if not good:
        raise ValueError("L4 container control mismatch")
    return dict(id=raw["Id"], name=raw["Name"], image=raw["Image"],
                state={key: raw["State"].get(key) for key in ("Status", "Running", "ExitCode", "OOMKilled", "Error")},
                controls=dict(network="none", memory=host["Memory"], nanocpus=host["NanoCpus"],
                              pids_limit=host["PidsLimit"], user=config["User"], readonly=True,
                              bind_mounts=[], port_bindings={}, cap_drop=host["CapDrop"],
                              security_opt=host["SecurityOpt"], tmpfs=host.get("Tmpfs", {})))


def run(image, name, phase, destination):
    """Copy tmpfs evidence while OUR container is alive; then release and remove it."""
    safe_name(name)
    if destination.exists():
        raise FileExistsError(destination)
    actual = inspect_image(image)["Id"]
    python = "/opt/xoc-l2/bin/python"
    if phase.startswith("c1"):
        if actual != C1_IMAGE:
            raise ValueError("C1 regressions require immutable original C1 image")
        action = phase[2:]
        module = "xoc_mcp_lab_harness.l3_benchmark" if action == "benchmark" else "xoc_mcp_lab_harness.linux_validation"
        command = [python, "-I", "-B", "-m", module, action, "--output", "/tmp/results/" + phase]
    else:
        command = [python, "-I", "-B", "-m", "xoc_mcp_lab_l4.harness", "--mode", phase,
                   "--output", "/tmp/results/" + phase + ".json"]
    created = docker("create", "--name", name, *LIMITS, "--entrypoint", python,
                     actual, "-I", "-B", "-c", KEEPALIVE, *command,
                     capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{64}", created):
        raise ValueError("Unrecognized owned container ID")
    try:
        before = container_controls(created)
        docker("start", created, capture_output=True, text=True)
        deadline = time.monotonic() + 1800
        while True:
            logs = docker("logs", "--tail", "3", created, capture_output=True, text=True)
            if "XOC_L4_EXPORT_READY" in logs.stdout:
                break
            state = container_controls(created)["state"]
            if not state["Running"]:
                raise RuntimeError("Owned L4 container stopped before evidence export")
            if time.monotonic() >= deadline:
                raise TimeoutError("L4 phase exceeded finite 1800 second supervisor limit")
            time.sleep(1)
        new_directory(destination)
        export = export_results(created, destination)
        (destination / "evidence-export.json").write_bytes(encoded(export))
        observed = container_controls(created)
        (destination / "container-inspect.json").write_bytes(encoded(dict(before=before, before_exit=observed)))
        docker("exec", created, python, "-I", "-B", "-c",
               "from pathlib import Path; Path('/tmp/results/export-complete').touch(exist_ok=False)",
               capture_output=True, text=True)
        exit_code = int(docker("wait", created, capture_output=True, text=True).stdout.strip())
        final = container_controls(created)
        (destination / "container-final.json").write_bytes(encoded(final))
        recorded = json.loads((destination / "container-process.json").read_bytes())["exit_code"]
        if exit_code != recorded or exit_code:
            raise RuntimeError("L4 phase failed; retained evidence and exit code " + str(exit_code))
        return dict(lab_only=True, phase=phase, exit_code=exit_code, output=str(destination),
                    copied_before_container_exit=True, image=actual)
    except Exception as error:
        # Preserve partial evidence if a phase fails before its normal handshake.
        # Error categories only: never echo exceptions containing a request/token.
        destination.mkdir(parents=True, exist_ok=True)
        export_error = None
        try:
            export_results(created, destination)
            copied = True
        except Exception as failure:
            copied = False
            export_error = dict(error_type=type(failure).__name__, infrastructure=infrastructure_error(failure))
        (destination / "runner-failure.json").write_bytes(encoded(dict(
            lab_only=True, error_type=type(error).__name__, infrastructure=infrastructure_error(error),
            partial_evidence_copied=copied, export_error=export_error)))
        raise
    finally:
        # Exact ID returned by this invocation; never names/globs/foreign containers.
        docker("rm", "--force", created, capture_output=True, text=True)
        if destination.exists():
            (destination / "container-cleanup.json").write_bytes(encoded(dict(
                lab_only=True, owned_container_id=created, removal_exit_code=0,
                foreign_containers_touched=False)))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export"); exp.add_argument("--output", type=Path, required=True)
    make = sub.add_parser("build")
    make.add_argument("--staging", type=Path, required=True); make.add_argument("--output", type=Path, required=True)
    make_docker = sub.add_parser("docker-build")
    make_docker.add_argument("--staging", type=Path, required=True); make_docker.add_argument("--output", type=Path, required=True)
    make_docker.add_argument("--image", default=C1_IMAGE); make_docker.add_argument("--name", required=True)
    equal = sub.add_parser("compare")
    equal.add_argument("--first", type=Path, required=True); equal.add_argument("--second", type=Path, required=True)
    ctx = sub.add_parser("context")
    ctx.add_argument("--artifacts", type=Path, required=True); ctx.add_argument("--preparation", type=Path, required=True)
    ctx.add_argument("--output", type=Path, required=True)
    sub.add_parser("install")
    execute = sub.add_parser("run")
    execute.add_argument("--image", required=True); execute.add_argument("--name", required=True)
    execute.add_argument("--phase", choices=("tests", "smoke", "benchmark", "provenance", "c1tests", "c1smoke", "c1provenance", "c1benchmark"), required=True)
    execute.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export": result = export(args.output)
    elif args.command == "build": result = build(args.staging, args.output)
    elif args.command == "docker-build": result = docker_build(args.staging, args.output, args.image, args.name)
    elif args.command == "compare": result = compare(args.first, args.second)
    elif args.command == "context": result = context(args.artifacts, args.preparation, args.output)
    elif args.command == "run": result = run(args.image, args.name, args.phase, args.output)
    else: install(); return
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
