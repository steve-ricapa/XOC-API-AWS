"""Installed-only, fail-closed integrity for the additive LAB_ONLY L6 wheel."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

L4_IMAGE = "sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9"
L4_VERSION = "0.4.0+lab.27a62d14058a"
L4_SHA256 = "764352dc77a7c467f6c8c3162a4f93e4fcca0e21b7933db0e2a4557669dcfb5d"
CORE_SHA256 = "df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41"
SDK_VERSION = "1.30.0"
PROTOCOL = "2025-11-25"


def _check_files(root, entries):
    root = root.resolve()
    for relative, expected in entries.items():
        path = root / relative
        if (not path.resolve().is_relative_to(root) or path.is_symlink()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError("LAB_L6_INSTALLED_FILE_MISMATCH")


def require_l6_installed():
    """Verify byte provenance, not user authority; never cache authorization.

    Whole wheel hashes are checked at image construction. Installed RECORD is
    excluded because pip rewrites it; all original source/metadata is checked.
    This guard is not a signature or a sandbox against a hostile image owner.
    """
    marker_path = Path(__file__).parent / "_artifact.json"
    if not marker_path.is_file():
        raise ValueError("LAB_L6_INSTALLED_ARTIFACT_REQUIRED")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (marker.get("schema") != "xoc-l6-artifact-v1"
            or marker.get("lab_only") is not True or marker.get("l4_image") != L4_IMAGE
            or marker.get("l4_sha256") != L4_SHA256
            or marker.get("core_sha256") != CORE_SHA256
            or marker.get("sdk") != SDK_VERSION or marker.get("protocol") != PROTOCOL):
        raise ValueError("LAB_L6_ARTIFACT_COMPATIBILITY_MISMATCH")
    dist = importlib.metadata.distribution("xoc-mcp-lab-l6")
    root = Path(dist.locate_file("")).resolve()
    if (dist.version != marker["version"]
            or not root.is_relative_to(Path(sys.prefix).resolve())
            or Path(__file__).resolve().parent.parent != root):
        raise ValueError("LAB_L6_ISOLATED_INSTALL_REQUIRED")
    prior = importlib.metadata.distribution("xoc-mcp-lab-l4")
    if prior.version != L4_VERSION or Path(prior.locate_file("")).resolve() != root:
        raise ValueError("LAB_L6_L4_DISTRIBUTION_MISMATCH")
    if importlib.metadata.version("mcp") != SDK_VERSION:
        raise ValueError("LAB_L6_SDK_MISMATCH")
    _check_files(root, marker["files"])
    _check_files(root, marker["preserved_files"])
    from xoc_mcp_lab_l4.integrity import require_l4_installed
    if require_l4_installed() != root:
        raise ValueError("LAB_L6_CANONICAL_ROOT_MISMATCH")
    return root


def evidence():
    root = require_l6_installed()
    marker = json.loads((root / "xoc_mcp_lab_l6/_artifact.json").read_bytes())
    return dict(lab_only=True, installed_root=str(root), l4_image=L4_IMAGE,
                l4_sha256=L4_SHA256, core_sha256=CORE_SHA256,
                version=marker["version"], sdk=SDK_VERSION, protocol=PROTOCOL,
                source_manifest_sha256=marker["source_manifest_sha256"],
                l6_files_verified=len(marker["files"]),
                preserved_files_verified=len(marker["preserved_files"]),
                c1_unchanged=True, l4_unchanged=True)
