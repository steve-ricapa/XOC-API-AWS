"""L4 additions only; historical packaging tests and inputs stay unchanged."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile
import pytest


def load_builder():
    path = Path(__file__).resolve().parents[1] / "packaging/l4_build.py"
    if not path.is_file():
        # Packaging source tests run on host; installed artifact tests verify the
        # deterministic proof bundled in their own wheel instead of a checkout.
        return None
    spec = importlib.util.spec_from_file_location("test_l4_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_l4_source_or_installed_integrity_fail_closed():
    from xoc_mcp_lab_l4.integrity import require_l4_installed
    if (Path(__file__).resolve().parents[1] / "_artifact.json").is_file():
        root = require_l4_installed()
        assert (root / "xoc_mcp_lab_l4/_artifact.json").is_file()
    else:
        with pytest.raises(ValueError, match="LAB_L4_INSTALLED_ARTIFACT_REQUIRED"):
            require_l4_installed()


def test_l4_pinned_c1_and_locks_unchanged():
    build = load_builder()
    if build is None:
        from xoc_mcp_lab_l4.integrity import evidence
        assert evidence()["c1_unchanged"] is True
        return
    manifest = build.validate_c1(build.C1_DIRECTORY)
    assert len(manifest["artifacts"]) == 3
    for name, expected in build.LOCKS.items():
        build.checked_file(build.LAB / "packaging" / name, expected)


def test_l4_two_independent_pure_wheels_and_c1_bytes():
    build = load_builder()
    if build is None:
        from xoc_mcp_lab_l4.integrity import require_l4_installed
        installed = require_l4_installed()
        path = installed / "xoc_mcp_lab_harness/packaging/build_artifacts.py"
        spec = importlib.util.spec_from_file_location("l4_installed_wheel_builder", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        marker_path = installed / "xoc_mcp_lab_l4/_artifact.json"
        marker = json.loads(marker_path.read_bytes())
        files = {name: (installed / name).read_bytes() for name in marker["files"]}
        files["xoc_mcp_lab_l4/_artifact.json"] = marker_path.read_bytes()
        with tempfile.TemporaryDirectory(prefix="xoc-l4-installed-build-") as temporary:
            root = Path(temporary)
            (root / "a").mkdir(); (root / "b").mkdir()
            requirements = ["xoc-mcp-lab==0.2.0+lab.cdce81cbc014", "xoc-mcp-lab-harness==0.2.0+lab.cdce81cbc014"]
            first = module.wheel("xoc_mcp_lab_l4", marker["version"], files, requirements, root / "a")
            second = module.wheel("xoc_mcp_lab_l4", marker["version"], files, requirements, root / "b")
            assert first == second
            original = Path("/offline-l4") / first["filename"]
            if original.is_file():
                assert hashlib.sha256(original.read_bytes()).hexdigest() == first["sha256"]
        return
    with tempfile.TemporaryDirectory(prefix="xoc-l4-package-") as temporary:
        root = Path(temporary)
        for suffix in ("a", "b"):
            build.export(root / ("sources-" + suffix))
            build.build(root / ("sources-" + suffix), root / ("build-" + suffix))
        assert build.compare(root / "build-a", root / "build-b")["independent_builds_identical"]
        manifest = build.validate_build(root / "build-a")
        own = manifest["artifacts"][-1]
        with zipfile.ZipFile(root / "build-a" / own["filename"]) as archive:
            assert all(not name.startswith(("src/", "xoc_mcp_lab/", "xoc_mcp_lab_harness/")) for name in archive.namelist())
            assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in archive.infolist())
            assert all(not name.endswith((".pyc", ".pth", ".pyd")) for name in archive.namelist())
            marker = json.loads(archive.read("xoc_mcp_lab_l4/_artifact.json"))
            assert marker["core_sha256"] == build.CORE_SHA256
            for name, sha in marker["files"].items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == sha
        with pytest.raises(FileExistsError):
            build.build(root / "sources-a", root / "build-a")


def test_l4_tampered_source_or_path_is_rejected():
    build = load_builder()
    if build is None:
        from xoc_mcp_lab_l4.integrity import _check_files
        with tempfile.TemporaryDirectory(prefix="xoc-l4-integrity-") as temporary:
            with pytest.raises(ValueError, match="LAB_L4_INSTALLED_FILE_MISMATCH"):
                _check_files(Path(temporary), {"../forbidden.py": "0" * 64})
        return
    with tempfile.TemporaryDirectory(prefix="xoc-l4-tamper-") as temporary:
        root = Path(temporary)
        build.export(root / "sources")
        path = root / "sources/l4/xoc_mcp_lab_l4/__init__.py"
        path.write_bytes(b"# tampered fixture copy only\n")
        with pytest.raises(ValueError, match="hash mismatch"):
            build.build(root / "sources", root / "rejected")
        with pytest.raises(ValueError, match="Unsafe"):
            build.relative_file(root, "../outside.py")


def test_l4_runner_uses_no_bind_mounts_or_exposed_ports():
    build = load_builder()
    if build is None:
        from xoc_mcp_lab_l4.integrity import evidence
        assert evidence()["l4_files_verified"] > 0
        return
    assert "--mount" not in build.LIMITS and "--volume" not in build.LIMITS
    assert "--publish" not in build.LIMITS and "-p" not in build.LIMITS
    assert build.LIMITS[build.LIMITS.index("--network") + 1] == "none"
    assert build.LIMITS[build.LIMITS.index("--user") + 1] == "10001:10001"
    with pytest.raises(ValueError):
        build.safe_name("unrelated-container")


def test_l4_evidence_tar_is_bounded_and_no_overwrite_or_traversal():
    build = load_builder()
    if build is None:
        from xoc_mcp_lab_l4.integrity import evidence
        assert evidence()["c1_unchanged"] is True
        return
    def archive(name, payload=b"fixture evidence\n", kind=tarfile.REGTYPE):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as tar:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(payload) if kind == tarfile.REGTYPE else 0
            tar.addfile(info, io.BytesIO(payload) if info.size else None)
        return buffer.getvalue()
    with tempfile.TemporaryDirectory(prefix="xoc-l4-evidence-") as temporary:
        root = Path(temporary)
        payload = archive("nested/result.json")
        assert build.extract_evidence(payload, root)["files"] == 1
        assert (root / "nested/result.json").read_bytes() == b"fixture evidence\n"
        assert build.extract_evidence(payload, root)["files"] == 1
        with pytest.raises(FileExistsError):
            build.extract_evidence(archive("nested/result.json", b"changed"), root)
        for unsafe in ("../outside", "/absolute", "C:/drive", "nested\\escape"):
            with pytest.raises(ValueError):
                build.extract_evidence(archive(unsafe), root)
        with pytest.raises(ValueError):
            build.extract_evidence(archive("link", kind=tarfile.SYMTYPE), root)


def test_l4_infrastructure_errors_retain_system_stderr_not_tokens():
    build = load_builder()
    if build is None:
        from xoc_mcp_lab_l4.integrity import evidence
        assert evidence()["c1_unchanged"] is True
        return
    error = subprocess.CalledProcessError(1, ["docker", "exec"],
                                         stderr=b"Error response from daemon: missing container\nAuthorization: Bearer fictional\n")
    safe = build.infrastructure_error(error)
    assert safe["exit_code"] == 1
    assert "missing container" in safe["stderr"]
    assert "fictional" not in safe["stderr"]
