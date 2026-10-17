"""Additive installed/offline integrity; never changes historical assertions."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import zipfile

import pytest


def load_builder():
    base = Path(__file__).resolve().parents[1]
    path = base / "packaging/l6_build.py"
    if not path.is_file():
        path = base / "_build/l6_build.py"
    spec = importlib.util.spec_from_file_location("test_l6_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_l6_installed_only_integrity():
    from xoc_mcp_lab_l6.integrity import require_l6_installed
    if (Path(__file__).resolve().parents[1] / "_artifact.json").is_file():
        root = require_l6_installed()
        assert (root / "xoc_mcp_lab_l4/_artifact.json").is_file()
    else:
        with pytest.raises(ValueError, match="LAB_L6_INSTALLED_ARTIFACT_REQUIRED"):
            require_l6_installed()


def test_l6_four_pinned_wheels_and_immutable_builder():
    build = load_builder()
    assert len(build.PRESERVED_WHEELS) == 4
    assert build.PRESERVED_WHEELS["xoc_mcp_lab_l4-" + build.L4_VERSION + "-py3-none-any.whl"] == build.L4_SHA256
    for filename, sha in build.BUILDER_INPUTS.items():
        build.checked_file(Path(build.__file__).with_name(filename), sha)
    if build.L4_DIRECTORY.is_dir():
        assert len(build.validate_l4(build.L4_DIRECTORY)["artifacts"]) == 4
    else:
        from xoc_mcp_lab_l6.integrity import evidence
        assert evidence()["l4_unchanged"]


def test_l6_reproducible_fifth_wheel_and_preserved_namespaces():
    build = load_builder()
    marker_path = Path(__file__).resolve().parents[1] / "_artifact.json"
    with tempfile.TemporaryDirectory(prefix="xoc-l6-packaging-") as temporary:
        root = Path(temporary)
        if marker_path.is_file():
            from xoc_mcp_lab_l6.integrity import require_l6_installed
            site = require_l6_installed()
            marker = json.loads(marker_path.read_bytes())
            files = {name: (site / name).read_bytes() for name in marker["files"]}
            files["xoc_mcp_lab_l6/_artifact.json"] = marker_path.read_bytes()
            for suffix in ("a", "b"):
                (root / suffix).mkdir()
                build.OLD.builder().wheel("xoc_mcp_lab_l6", marker["version"], files,
                                          ["xoc-mcp-lab-l4==" + build.L4_VERSION], root / suffix)
            first = next((root / "a").glob("*.whl"))
            second = next((root / "b").glob("*.whl"))
            assert first.read_bytes() == second.read_bytes()
            assert first.read_bytes() == (Path("/offline-l6") / first.name).read_bytes()
        else:
            for suffix in ("a", "b"):
                build.export(root / ("sources-" + suffix))
                build.build(root / ("sources-" + suffix), root / suffix)
            assert build.compare(root / "a", root / "b")["independent_builds_identical"]
            manifest = build.validate_build(root / "a")
            assert len(manifest["artifacts"]) == 5
            first = root / "a" / manifest["artifacts"][-1]["filename"]
            with pytest.raises(FileExistsError):
                build.export(root / "sources-a")
        with zipfile.ZipFile(first) as archive:
            assert all(name.startswith(("xoc_mcp_lab_l6/", "xoc_mcp_lab_l6-")) for name in archive.namelist())
            assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
            assert all(not name.endswith((".pth", ".pyc", ".pyd")) for name in archive.namelist())
            marker = json.loads(archive.read("xoc_mcp_lab_l6/_artifact.json"))
            for name, sha in marker["files"].items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == sha


def test_l6_exact_limits_and_no_old_runner_monkeypatch():
    build = load_builder()
    assert build.LIMITS == tuple(build.OLD.LIMITS)
    assert "--mount" not in build.LIMITS and "--volume" not in build.LIMITS
    assert "--publish" not in build.LIMITS and "-p" not in build.LIMITS
    assert build.LIMITS[build.LIMITS.index("--network") + 1] == "none"
    assert build.LIMITS[build.LIMITS.index("--pids-limit") + 1] == "128"
    assert build.OLD.run.__module__ == "xoc_l6_immutable_l4_helpers"
    with pytest.raises(ValueError):
        build.safe_name("xoc-l4-foreign")


def test_l6_owner_requires_exact_id_name_and_labels():
    build = load_builder()
    owner = dict(id="a" * 64, name="xoc-l6-own-sample")
    raw = dict(Id=owner["id"], Name="/" + owner["name"],
               Config=dict(Labels={"io.xoc.lab": "l6", "io.xoc.l6.owner": owner["name"]}))
    build.validate_owner(raw, owner)
    for changed in (dict(raw, Id="b" * 64), dict(raw, Name="/unrelated"), dict(raw, Config=dict(Labels={}))):
        with pytest.raises(ValueError, match="unowned"):
            build.validate_owner(changed, owner)


def test_l6_evidence_rejects_traversal_and_overwrite():
    build = load_builder()
    def payload(name, contents):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive:
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
        return buffer.getvalue()
    with tempfile.TemporaryDirectory(prefix="xoc-l6-evidence-") as temporary:
        root = Path(temporary)
        assert build.extract_evidence(payload("test.json", b"{}"), root)["files"] == 1
        with pytest.raises(FileExistsError):
            build.extract_evidence(payload("test.json", b"changed"), root)
        with pytest.raises(ValueError):
            build.extract_evidence(payload("../escape.json", b"{}"), root)


def test_l6_file_integrity_fails_closed_on_missing_or_escape():
    from xoc_mcp_lab_l6.integrity import _check_files
    with tempfile.TemporaryDirectory(prefix="xoc-l6-integrity-") as temporary:
        for name in ("absent.py", "../escape.py"):
            with pytest.raises(ValueError, match="LAB_L6_INSTALLED_FILE_MISMATCH"):
                _check_files(Path(temporary), {name: "0" * 64})
