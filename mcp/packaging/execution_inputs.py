"""Assemble ONLY wheels, locks and public harness inputs for a clean offline image."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = Path(__file__).parent
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.artifacts / "artifact-manifest.json").read_text())
    (args.output / "artifacts").mkdir()
    for entry in manifest["artifacts"]:
        file = args.artifacts / entry["filename"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry["sha256"]
        shutil.copyfile(file, args.output / "artifacts" / file.name)
    shutil.copyfile(args.artifacts / "artifact-manifest.json", args.output / "artifacts/artifact-manifest.json")
    (args.output / "wheelhouse").mkdir()
    for file in sorted((args.preparation / "wheelhouse").glob("*.whl")):
        assert "win_" not in file.name and "win32" not in file.name
        shutil.copyfile(file, args.output / "wheelhouse" / file.name)
    for group in ("runtime", "test"):
        name = f"requirements-linux-{group}.lock.txt"
        shutil.copyfile(config / name, args.output / name)
    for name in ("Dockerfile.linux-test", "install_offline.py"):
        shutil.copyfile(config / name, args.output / name)
    shutil.copyfile(args.reference, args.output / "reference.json")
    print(json.dumps(dict(lab_only=True, backend_checkout_included=False,
        files=[p.relative_to(args.output).as_posix() for p in sorted(args.output.rglob("*")) if p.is_file()])))

if __name__ == "__main__": main()
