"""Fail closed unless L4 and the unchanged C1 wheels are installed together."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

C1_IMAGE = "sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40"
CORE_SHA256 = "df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41"
C1_VERSIONS = {
    "xoc-tool-gateway-core": "0.0.0+lab.ad4ac1a764de",
    "xoc-mcp-lab": "0.2.0+lab.cdce81cbc014",
    "xoc-mcp-lab-harness": "0.2.0+lab.cdce81cbc014",
}


def _check_files(root, entries):
    for relative, expected in entries.items():
        path = root / relative
        if (not path.resolve().is_relative_to(root) or path.is_symlink()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError("LAB_L4_INSTALLED_FILE_MISMATCH")


def require_l4_installed():
    """Verify installed bytes, dependencies and provenance before readiness.

    This is an integrity/provenance guard, not a signature or hostile-code sandbox.
    The image build separately verifies the input wheel's whole-file SHA256.
    """
    marker_path = Path(__file__).parent / "_artifact.json"
    if not marker_path.is_file():
        raise ValueError("LAB_L4_INSTALLED_ARTIFACT_REQUIRED")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (marker.get("lab_only") is not True or marker.get("schema") != "xoc-l4-artifact-v1"
            or marker.get("c1_image") != C1_IMAGE
            or marker.get("core_sha256") != CORE_SHA256):
        raise ValueError("LAB_L4_ARTIFACT_COMPATIBILITY_MISMATCH")
    dist = importlib.metadata.distribution("xoc-mcp-lab-l4")
    root = Path(dist.locate_file("")).resolve()
    if (dist.version != marker["version"] or not root.is_relative_to(Path(sys.prefix).resolve())
            or Path(__file__).resolve().parent.parent != root):
        raise ValueError("LAB_L4_ISOLATED_INSTALL_REQUIRED")
    for name, version in C1_VERSIONS.items():
        prior = importlib.metadata.distribution(name)
        if prior.version != version or Path(prior.locate_file("")).resolve() != root:
            raise ValueError("LAB_L4_C1_DISTRIBUTION_MISMATCH")
    _check_files(root, marker["files"])
    _check_files(root, marker["c1_files"])
    from xoc_mcp_lab.installed import require_installed
    if require_installed() != root:
        raise ValueError("LAB_L4_CANONICAL_ROOT_MISMATCH")
    return root


def evidence():
    root = require_l4_installed()
    marker = json.loads((root / "xoc_mcp_lab_l4/_artifact.json").read_text())
    return dict(lab_only=True, installed_root=str(root), c1_image=C1_IMAGE,
                core_sha256=CORE_SHA256, version=marker["version"],
                source_manifest_sha256=marker["source_manifest_sha256"],
                l4_files_verified=len(marker["files"]),
                c1_files_verified=len(marker["c1_files"]), c1_unchanged=True)
