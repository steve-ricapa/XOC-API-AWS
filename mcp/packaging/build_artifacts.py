"""Deterministic LAB_ONLY pure-Python wheels; no backend source transformations.

export: read exact Git blobs into a NEW, dedicated staging. build: no Git/checkout.
The tiny stdlib builder implements wheel 1.0 (METADATA/WHEEL/RECORD); no setup.py,
build hooks, editable installs, timestamps, pyc or host absolute paths in wheels.
"""
import argparse
import ast
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

LAB = Path(__file__).resolve().parents[1]
REVISION = "ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def internal_imports(path, data):
    found = set()
    for node in ast.walk(ast.parse(data.decode("utf-8-sig"), filename=path)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name == "src" or a.name.startswith("src."))
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
            found.add(node.module)
    return found


def export(backend, destination):
    selection = json.loads((LAB / "packaging/gateway-files.json").read_text())
    if selection["backend_commit"] != REVISION:
        raise ValueError("Canonical revision changed: explicit review required")
    if git(backend, "rev-parse", "HEAD").decode().strip() != REVISION:
        raise ValueError("Backend HEAD drift; do not silently use a new revision")
    destination.mkdir(parents=True, exist_ok=False)
    files, entries = {}, []
    for path, reason in sorted(selection["files"].items()):
        if not path.startswith("src/") or not path.endswith(".py") or ".." in Path(path).parts:
            raise ValueError("Invalid selection path")
        blob = git(backend, "rev-parse", f"{REVISION}:{path}").decode().strip()
        data = git(backend, "cat-file", "blob", blob)
        files[path] = data
        entries.append(dict(original_path=path, artifact_path=path, git_blob=blob,
            sha256=digest(data), reason=reason, imports=sorted(internal_imports(path, data))))
    for entry in entries:
        for module in entry["imports"]:
            relative = module.replace(".", "/")
            if relative + ".py" not in files and relative + "/__init__.py" not in files:
                raise ValueError(f"Missing canonical transitive import: {module}")
    for path, data in files.items():
        target = destination / "core" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = dict(lab_only=True, backend_commit=REVISION, files=entries,
        source_selection_sha256=digest((LAB / "packaging/gateway-files.json").read_bytes()))
    (destination / "gateway-manifest.json").write_bytes(json_bytes(manifest))
    # Explicit source groups: no .venv, caches, reports, env, Windows binaries.
    adapter = {p.relative_to(LAB).as_posix(): p.read_bytes() for p in sorted((LAB / "xoc_mcp_lab").glob("*.py"))}
    for name in ("server.py", "compatibility.json", "requirements.lock.txt"):
        adapter["xoc_mcp_lab/_lab/" + name] = (LAB / name).read_bytes()
    harness = {p.relative_to(LAB).as_posix(): p.read_bytes() for p in sorted((LAB / "xoc_mcp_lab_harness").glob("*.py"))}
    for p in sorted((LAB / "tests").glob("*.py")):
        harness["xoc_mcp_lab_harness/tests/" + p.name] = p.read_bytes()
    for name in ("smoke_l1.py", "run_tests.py"):
        harness["xoc_mcp_lab_harness/" + name] = (LAB / name).read_bytes()
    # Build source copied only into harness for packaging tests, not server runtime.
    for name in ("build_artifacts.py", "gateway-files.json"):
        harness["xoc_mcp_lab_harness/packaging/" + name] = (LAB / "packaging" / name).read_bytes()
    for group, contents in (("adapter", adapter), ("harness", harness)):
        for path, data in contents.items():
            target = destination / group / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    source_manifest = dict(lab_only=True, groups={group: {path: digest(data) for path, data in sorted(contents.items())}
        for group, contents in (("core", files), ("adapter", adapter), ("harness", harness))})
    (destination / "source-manifest.json").write_bytes(json_bytes(source_manifest))
    return source_manifest


def wheel(name, version, files, requirements, destination):
    metadata = f"{name}-{version}.dist-info"
    files = dict(files)
    files[metadata + "/METADATA"] = (f"Metadata-Version: 2.1\nName: {name.replace('_', '-')}\nVersion: {version}\n"
        "Summary: LAB_ONLY OFFLINE FIXTURES - never publish or deploy\nRequires-Python: >=3.11\n"
        + "".join(f"Requires-Dist: {r}\n" for r in requirements) + "\n").encode()
    files[metadata + "/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: xoc-l2-stdlib-1\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for path, data in sorted(files.items()):
        writer.writerow((path, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="), len(data)))
    writer.writerow((metadata + "/RECORD", "", ""))
    files[metadata + "/RECORD"] = record.getvalue().encode()
    target = destination / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_STORED) as archive:
        for path, data in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return dict(filename=target.name, sha256=digest(target.read_bytes()), bytes=target.stat().st_size)


def build(staging, destination, runtime_lock, test_lock):
    manifest_bytes = (staging / "gateway-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    source_bytes = (staging / "source-manifest.json").read_bytes()
    source = json.loads(source_bytes)
    if manifest["backend_commit"] != REVISION or manifest["lab_only"] is not True:
        raise ValueError("Wrong canonical manifest")
    groups = {}
    for group, entries in source["groups"].items():
        groups[group] = {}
        for path, expected in entries.items():
            file = staging / group / path
            if file.is_symlink() or not file.resolve().is_relative_to((staging / group).resolve()):
                raise ValueError("Unexpected staging link")
            data = file.read_bytes()
            if digest(data) != expected: raise ValueError("Source staging hash mismatch: " + path)
            groups[group][path] = data
    for entry in manifest["files"]:
        if digest(groups["core"][entry["original_path"]]) != entry["sha256"]:
            raise ValueError("Canonical manifest mismatch")
    runtime = runtime_lock.read_bytes()
    test = test_lock.read_bytes()
    source_hash = digest(source_bytes)
    core_version = "0.0.0+lab." + REVISION[:12]
    lab_version = "0.2.0+lab." + source_hash[:12]
    groups["core"]["xoc_tool_gateway_core/__init__.py"] = b'"""LAB_ONLY provenance metadata; business code remains src.*."""\n'
    groups["core"]["xoc_tool_gateway_core/manifest.json"] = manifest_bytes
    groups["adapter"]["xoc_mcp_lab/_artifact.json"] = json_bytes(dict(lab_only=True,
        backend_commit=REVISION, core_manifest_sha256=digest(manifest_bytes), source_manifest_sha256=source_hash,
        adapter_files={p: digest(d) for p, d in groups["adapter"].items()},
        runtime_lock_sha256=digest(runtime), test_lock_sha256=digest(test)))
    groups["adapter"]["xoc_mcp_lab/_lab/runtime.lock.txt"] = runtime
    groups["harness"]["xoc_mcp_lab_harness/source-manifest.json"] = source_bytes
    groups["harness"]["xoc_mcp_lab_harness/gateway-manifest.json"] = manifest_bytes
    requirements = [line.split(" --hash=", 1)[0] for line in runtime.decode().splitlines() if line and not line.startswith("#")]
    test_requirements = [line.split(" --hash=", 1)[0] for line in test.decode().splitlines() if line and not line.startswith("#")]
    destination.mkdir(parents=True, exist_ok=False)
    wheels = [wheel("xoc_tool_gateway_core", core_version, groups["core"], [], destination),
        wheel("xoc_mcp_lab", lab_version, groups["adapter"], [f"xoc-tool-gateway-core=={core_version}", *requirements], destination),
        wheel("xoc_mcp_lab_harness", lab_version, groups["harness"], [f"xoc-mcp-lab=={lab_version}", *test_requirements], destination)]
    evidence = dict(lab_only=True, fixture_only=True, backend_commit=REVISION,
        source_manifest_sha256=source_hash, runtime_lock_sha256=digest(runtime), test_lock_sha256=digest(test),
        builder="CPython stdlib zipfile; ZIP_STORED; fixed epoch/modes/order", artifacts=wheels)
    (destination / "artifact-manifest.json").write_bytes(json_bytes(evidence))
    (destination / "gateway-manifest.json").write_bytes(manifest_bytes)
    print(json.dumps(evidence, indent=2))
    return evidence


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--backend", type=Path, required=True)
    exp.add_argument("--output", type=Path, required=True)
    make = sub.add_parser("build")
    make.add_argument("--staging", type=Path, required=True)
    make.add_argument("--output", type=Path, required=True)
    make.add_argument("--runtime-lock", type=Path, required=True)
    make.add_argument("--test-lock", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export": export(args.backend, args.output)
    else: build(args.staging, args.output, args.runtime_lock, args.test_lock)


if __name__ == "__main__": main()
