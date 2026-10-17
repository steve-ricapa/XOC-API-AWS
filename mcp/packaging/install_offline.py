"""Only run inside the disposable image; verify wheels then install without network."""
import hashlib
import json
from pathlib import Path
import subprocess
import venv

root = Path("/offline")
manifest = json.loads((root / "artifacts/artifact-manifest.json").read_text())
assert manifest["lab_only"] is True
for entry in manifest["artifacts"]:
    path = root / "artifacts" / entry["filename"]
    assert path.name == entry["filename"] and path.suffix == ".whl"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
for group in ("runtime", "test"):
    assert hashlib.sha256((root / f"requirements-linux-{group}.lock.txt").read_bytes()).hexdigest() == manifest[f"{group}_lock_sha256"]
venv.EnvBuilder(with_pip=True).create("/opt/xoc-l2")
python = "/opt/xoc-l2/bin/python"
subprocess.run([python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--no-index",
    "--no-cache-dir", "--no-compile", "--require-hashes", "--only-binary=:all:", "--find-links", str(root / "wheelhouse"),
    "-r", str(root / "requirements-linux-runtime.lock.txt"), "-r", str(root / "requirements-linux-test.lock.txt")], check=True)
subprocess.run([python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--no-index",
    "--no-cache-dir", "--no-compile", "--no-deps", *[str(root / "artifacts" / e["filename"]) for e in manifest["artifacts"]]], check=True)
subprocess.run([python, "-I", "-B", "-m", "pip", "--isolated", "check"], check=True)
