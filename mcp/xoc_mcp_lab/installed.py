"""Select and verify installed LAB_ONLY artifacts; NEVER fall back to a checkout."""
import hashlib
import importlib.metadata
import importlib.machinery
import importlib.abc
import json
from pathlib import Path
import sys

MARKER = Path(__file__).parent / "_artifact.json"


def is_installed():
    return MARKER.is_file()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CanonicalFinder(importlib.abc.MetaPathFinder):
    """Reject missing/foreign src modules rather than falling through to host paths."""
    def __init__(self, root, expected):
        self.root, self.expected = root, expected

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "src" and not fullname.startswith("src."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or not spec.origin:
            raise ImportError("LAB_CANONICAL_MODULE_MISSING")
        origin = Path(spec.origin).resolve()
        relative = origin.relative_to(self.root).as_posix() if origin.is_relative_to(self.root) else ""
        if relative not in self.expected or _sha(origin) != self.expected[relative]:
            raise ImportError("LAB_CANONICAL_ORIGIN_MISMATCH")
        return spec


def require_installed():
    if not is_installed():
        raise ValueError("LAB_INSTALLED_ARTIFACT_REQUIRED")
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    if marker.get("lab_only") is not True or importlib.metadata.version("mcp") != "1.30.0":
        raise ValueError("LAB_ARTIFACT_COMPATIBILITY_MISMATCH")
    root = Path(importlib.metadata.distribution("xoc-tool-gateway-core").locate_file("")).resolve()
    adapter_root = Path(importlib.metadata.distribution("xoc-mcp-lab").locate_file("")).resolve()
    if root != adapter_root or not root.is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError("LAB_ISOLATED_INSTALL_REQUIRED")
    manifest_path = root / "xoc_tool_gateway_core/manifest.json"
    if _sha(manifest_path) != marker["core_manifest_sha256"]:
        raise ValueError("LAB_CORE_MANIFEST_MISMATCH")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["lab_only"] is not True or manifest["backend_commit"] != marker["backend_commit"]:
        raise ValueError("LAB_CORE_REVISION_MISMATCH")
    expected = {e["artifact_path"]: e["sha256"] for e in manifest["files"]}
    for relative, expected_hash in dict(expected, **marker["adapter_files"]).items():
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink() or _sha(path) != expected_hash:
            raise ValueError("LAB_ARTIFACT_FILE_MISMATCH")
    runtime_lock = root / "xoc_mcp_lab/_lab/runtime.lock.txt"
    if _sha(runtime_lock) != marker["runtime_lock_sha256"]:
        raise ValueError("LAB_RUNTIME_LOCK_MISMATCH")
    for line in runtime_lock.read_text().splitlines():
        if not line or line.startswith("#"): continue
        name, version = line.split(" --hash=", 1)[0].split("==", 1)
        if importlib.metadata.version(name) != version:
            raise ValueError("LAB_DEPENDENCY_VERSION_MISMATCH")
    for name, module in list(sys.modules.items()):
        if name == "src" or name.startswith("src."):
            origin = Path(getattr(module, "__file__", "")).resolve()
            relative = origin.relative_to(root).as_posix() if origin.is_relative_to(root) else ""
            if relative not in expected or _sha(origin) != expected[relative]:
                raise ValueError("LAB_FOREIGN_CANONICAL_IMPORT")
    if not any(isinstance(f, CanonicalFinder) and f.root == root for f in sys.meta_path):
        sys.meta_path.insert(0, CanonicalFinder(root, expected))
    return root


def import_evidence():
    root = require_installed()
    return [{"module": name, "file": str(Path(module.__file__).resolve()),
             "sha256": _sha(Path(module.__file__))}
        for name, module in sorted(sys.modules.items()) if name == "src" or name.startswith("src.")]
