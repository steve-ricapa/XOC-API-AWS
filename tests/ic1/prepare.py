"""Prepare an explicit IC-1 Docker context; no credentials, git writes or LAB paths."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mcp", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    mcp = args.mcp.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "tests/ic1/.build"):
        raise ValueError("Use a new directory within backend/tests/ic1/.build")
    manifest = json.loads((mcp / "HANDOFF_MANIFEST.json").read_bytes())
    # README repair has been explicitly authorized; all original hashes must pass.
    for row in manifest["files"]:
        if hashlib.sha256((mcp / row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("MCP handoff drift: " + row["path"])
    output.mkdir(parents=True)
    records = []

    def copy(source, target):
        data = source.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append(dict(path=target.relative_to(output).as_posix(),
                            sha256=hashlib.sha256(data).hexdigest()))

    # Only Python source, not .env / archives / build outputs / operational docs.
    for source in sorted((ROOT / "src").rglob("*.py")):
        if source.is_symlink():
            raise ValueError("Linked source")
        copy(source, output / "backend" / source.relative_to(ROOT))
    for source in sorted(Path(__file__).parent.glob("*.py")):
        if source.name != "prepare.py":
            copy(source, output / "harness" / source.name)
    for name in ("Dockerfile", "requirements-linux.lock.txt"):
        copy(Path(__file__).parent / name, output / name)
    for wheel in sorted((mcp / "build/ic1-extra").glob("*.whl")):
        copy(wheel, output / "extra" / wheel.name)
    # Explicit source revisions; uncommitted source hashes above are the tested truth.
    heads = {name: subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
             for name, repo in (("backend", ROOT), ("mcp", mcp))}
    (output / "source-manifest.json").write_text(json.dumps(dict(heads=heads, files=records), indent=2))
    print(json.dumps(dict(context=str(output), files=len(records), heads=heads)))


if __name__ == "__main__":
    main()
