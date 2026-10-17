"""Public wheel preparation on the target image. No account CLI or source import."""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import venv
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(exist_ok=False)
    config = Path(__file__).parent
    target = json.loads((config / "linux-target.json").read_text())
    os_info = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    assert sys.version_info[:2] == (3, 11) and platform.machine() == "x86_64"
    assert os_info["ID"].strip('"') == "amzn" and os_info["VERSION_ID"].strip('"') == "2"
    venv.EnvBuilder(with_pip=True).create(output / "resolver")
    python = output / "resolver/bin/python"
    wheelhouse = output / "wheelhouse"
    wheelhouse.mkdir()
    inventory = []
    for group in ("runtime", "test"):
        subprocess.run([str(python), "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "download", "--only-binary=:all:", "--no-cache-dir", "--index-url", "https://pypi.org/simple",
            "--dest", str(wheelhouse), "-r", str(config / f"requirements-linux-{group}.in")], check=True)
        # runtime snapshot is taken BEFORE adding test-only wheels.
        rows = []
        for path in sorted(wheelhouse.glob("*.whl")):
            with zipfile.ZipFile(path) as archive:
                meta_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
                metadata = BytesParser().parsebytes(archive.read(meta_name))
            row = dict(name=metadata["Name"], version=metadata["Version"], filename=path.name,
                sha256=sha(path), size=path.stat().st_size, requires_python=metadata["Requires-Python"],
                requires_dist=metadata.get_all("Requires-Dist", []))
            rows.append(row)
        if group == "runtime":
            runtime_names = {r["filename"] for r in rows}
        else:
            rows = [r for r in rows if r["filename"] not in runtime_names]
        lock = "# LAB_ONLY; resolved on pinned Amazon Linux 2 / CPython 3.11.\n"
        lock += "\n".join(f'{r["name"]}=={r["version"]} --hash=sha256:{r["sha256"]}' for r in sorted(rows, key=lambda r: r["name"].lower())) + "\n"
        (output / f"requirements-linux-{group}.lock.txt").write_text(lock, encoding="utf-8", newline="\n")
        inventory.extend(dict(r, group=group) for r in rows)
    # A LOCAL inventory, not a signed SBOM or vulnerability attestation.
    evidence = dict(lab_only=True, target=target, python=sys.version, platform=platform.platform(),
        libc=platform.libc_ver(), os=os_info, emulation=False, packages=inventory,
        pip=subprocess.check_output([str(python), "-m", "pip", "--version"], text=True).strip(),
        build_engine="CPython stdlib zipfile, wheel spec 1.0, ZIP_STORED")
    (output / "dependency-inventory.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(status="prepared", packages=len(inventory), python=sys.version)))


if __name__ == "__main__":
    main()
