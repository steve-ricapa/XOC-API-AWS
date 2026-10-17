import ast
import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import zipfile
import pytest
from xoc_mcp_lab.installed import is_installed, require_installed
from xoc_mcp_lab.isolation import backend_path


def builder():
    source = Path(__file__).resolve().parents[1] / "packaging/build_artifacts.py"
    spec = importlib.util.spec_from_file_location("lab_wheel_builder", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_sources_cover_internal_imports_without_routes(backend):
    selection = json.loads((Path(__file__).resolve().parents[1] / "packaging/gateway-files.json").read_text())
    paths = set(selection["files"])
    assert len(paths) == 20
    assert not any("/handlers/" in p or p.endswith("db.py") for p in paths)
    build = builder()
    for path in paths:
        for module in build.internal_imports(path, (backend / path).read_bytes()):
            candidate = module.replace(".", "/")
            assert candidate + ".py" in paths or candidate + "/__init__.py" in paths


def test_wheel_double_build_record_metadata_bytes_and_no_pyc():
    build = builder()
    with tempfile.TemporaryDirectory(prefix="xoc-l2-wheel-") as tmp:
        first, second = Path(tmp) / "one", Path(tmp) / "two"
        first.mkdir(); second.mkdir()
        source = {"src/__init__.py": b"", "src/fixture.py": b"# bytes preserved\r\nVALUE=1\r\n"}
        a = build.wheel("xoc_test_lab", "0.0.0", source, [], first)
        b = build.wheel("xoc_test_lab", "0.0.0", dict(reversed(list(source.items()))), [], second)
        assert a == b
        with zipfile.ZipFile(first / a["filename"]) as archive:
            assert archive.read("src/fixture.py") == source["src/fixture.py"]
            records = list(csv.reader(io.StringIO(archive.read("xoc_test_lab-0.0.0.dist-info/RECORD").decode())))
            for name, expected, size in records:
                if not expected: continue
                data = archive.read(name)
                assert expected == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
                assert len(data) == int(size)
            assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in archive.infolist())
            assert all(not n.endswith((".pyc", ".pth", ".pyd")) for n in archive.namelist())
        with pytest.raises(FileExistsError): build.wheel("xoc_test_lab", "0.0.0", source, [], first)


def test_installed_mode_never_accepts_checkout_or_falls_back():
    if is_installed():
        root = require_installed()
        assert backend_path(None) == root
        with pytest.raises(ValueError): backend_path("../XOC-API-AWS")
    else:
        with pytest.raises(ValueError): require_installed()
        with pytest.raises(ValueError): backend_path(None)


def test_independent_smoke_reuses_existing_fixture_signer():
    from l1_fixtures import envelope
    from xoc_mcp_lab_harness.signing import envelope as original
    assert envelope is original
