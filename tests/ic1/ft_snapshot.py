"""Read-only repository/artifact inventory; write only a new local evidence file."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[2]
    target = args.output.resolve()
    if target.exists() or not target.is_relative_to(backend / "tests/ic1/.build"):
        raise ValueError("FT_NEW_EVIDENCE_REQUIRED")
    mcp = args.mcp.resolve(strict=True)
    report = dict(repos={}, handoff={}, history={})
    for name, repo in (("backend", backend), ("mcp", mcp)):
        entry = {}
        for label, command in (("status", ["status", "--short"]),
                ("branch", ["branch", "--show-current"]), ("head", ["rev-parse", "HEAD"]),
                ("diff", ["diff"]), ("staged", ["diff", "--cached"]), ("check", ["diff", "--check"])):
            run = subprocess.run(["git", "-C", str(repo), *command], capture_output=True, check=True)
            entry[label] = run.stdout.decode("utf-8", errors="replace")
        report["repos"][name] = entry
    manifest = json.loads((mcp / "HANDOFF_MANIFEST.json").read_bytes())
    rows = [dict(path=r["path"], matches=sha(mcp / r["path"]) == r["sha256"]) for r in manifest["files"]]
    report["handoff"] = dict(files=rows, count=len(rows), passed=all(r["matches"] for r in rows))
    checksums = []
    for line in (mcp / "HANDOFF_SHA256SUMS.txt").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        path = (mcp / relative).resolve(strict=True)
        if not path.is_relative_to(mcp):
            raise ValueError("FT_INVALID_MANIFEST_PATH")
        checksums.append(dict(path=relative, matches=sha(path) == expected))
    report["checksums"] = dict(count=len(checksums), passed=all(r["matches"] for r in checksums), files=checksums)
    for relative in ("docs/sophia-mcp-ic1-local-integration.md",
            "tests/ic1/.build/evidence/final.json", "tests/ic1/.build/evidence/baseline-focused.json",
            "tests/ic1/.build/ic1r5-20261008/analysis-final.json",
            "docs/cases-tenant-hotfix-and-d1c-identity-gate.md",
            "docs/sophia-d1c-proxy-source-verified.md", "docs/sophia-phase-d1b-inventory-gate.md"):
        report["history"][relative] = sha(backend / relative)
    report["new_evidence"] = {p.name: sha(p) for p in sorted(target.parent.glob("*.json"))}
    report["harness"] = {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    with target.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(dict(handoff=report["handoff"]["passed"], count=len(rows),
                         checksums=report["checksums"]["passed"], checksum_count=len(checksums),
                         output=str(target), sha256=sha(target))))


if __name__ == "__main__":
    main()
