"""Guard the provenance of an MCP checkout vendored beneath the backend."""
import importlib.util
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ic1_prepare", ROOT / "tests/ic1/prepare.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def test_vendored_mcp_does_not_claim_backend_head():
    mcp = ROOT / "mcp"
    manifest = (mcp / "HANDOFF_MANIFEST.json").read_bytes()
    result = prepare.mcp_provenance(mcp, manifest)
    assert result["head"] is None
    assert len(result["handoff_manifest_sha256"]) == 64


def test_independent_mcp_checkout_reports_its_own_head(tmp_path):
    repo = tmp_path / "standalone-mcp"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "test"], check=True)
    expected = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert prepare.mcp_provenance(repo, b"test")["head"] == expected
