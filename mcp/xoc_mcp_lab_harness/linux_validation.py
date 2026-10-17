"""Offline installed-package validation from /tmp, with no checkout or host mounts."""
import argparse
import asyncio
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import time


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def environment():
    from xoc_mcp_lab_harness.resources import snapshot
    os_info = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    assert os_info["ID"].strip('"') == "amzn" and os_info["VERSION_ID"].strip('"') == "2"
    assert sys.version_info[:2] == (3, 11) and platform.machine() == "x86_64"
    routes = Path("/proc/net/route").read_text().splitlines()
    assert len(routes) == 1, "LAB_NETWORK_NOT_DISABLED"
    mounts = Path("/proc/self/mountinfo").read_text()
    assert all(s not in mounts for s in ("XOC-API-AWS", "sources-01", "backend y pruebas", ".aws", ".azure", ".ssh"))
    assert Path.cwd() == Path("/tmp")
    # No editable/direct-url installations or .pth paths to a source tree.
    for site in {Path(d.locate_file("")).resolve() for d in importlib.metadata.distributions()}:
        for pth in site.glob("*.pth"):
            text = pth.read_text()
            assert not any(s in text for s in ("XOC", "__editable__", "/sources", "/workspace"))
    return dict(python=sys.version, os=os_info, machine=platform.machine(), libc=platform.libc_ver(),
        protocol="2025-11-25", sdk=importlib.metadata.version("mcp"), cwd=str(Path.cwd()),
        network_routes=routes, uid=os.getuid(), backend_checkout_mounted=False,
        executable=sys.executable, prefix=sys.prefix, sys_path=sys.path, resources=snapshot())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("tests", "smoke", "provenance", "benchmark"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    env = environment()
    write_new(args.output / "environment.json", env)
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    with Isolation():
        configure_logs()
        from xoc_mcp_lab.installed import require_installed, import_evidence
        root = require_installed()
        if args.phase == "smoke":
            from xoc_mcp_lab_harness.smoke_l1 import smoke
            start = time.monotonic()
            result = asyncio.run(smoke(None))
            result["total_seconds"] = time.monotonic() - start
            write_new(args.output / "smoke.json", result)
            print(json.dumps(result))
        elif args.phase == "provenance":
            from xoc_mcp_lab_harness.equivalence import cases
            from xoc_mcp_lab.wiring import Wiring
            actual = cases()
            expected = json.loads(Path("/offline/reference.json").read_text(encoding="utf-8"))
            assert actual == expected, "LAB_REFERENCE_DIFFERENCE"
            with Wiring():
                from xoc_mcp_lab.adapter import Adapter
                origins = import_evidence()
            write_new(args.output / "origins.json", origins)
            write_new(args.output / "equivalence.json", actual)
            print(json.dumps(dict(status="passed", cases=len(actual["cases"]), canonical_modules=len(origins))))
        elif args.phase == "tests":
            import pytest
            from xoc_mcp_lab import testing
            testing.BACKEND = root
            class Results:
                def __init__(self): self.rows = []
                def pytest_runtest_logreport(self, report):
                    if report.when == "call" or report.failed or report.skipped:
                        self.rows.append(dict(nodeid=report.nodeid, when=report.when, outcome=report.outcome,
                            duration=report.duration, reason=str(report.longrepr)[:4000] if report.failed or report.skipped else None))
            results, log = Results(), io.StringIO()
            started = time.monotonic()
            with redirect_stdout(log), redirect_stderr(log):
                code = pytest.main(["-q", "-p", "no:cacheprovider", str(Path(__file__).parent / "tests")], plugins=[results])
            safe = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[FIXTURE_JWS_REDACTED]", log.getvalue())
            (args.output / "pytest.txt").write_text(safe, encoding="utf-8")
            summary = dict(exit_code=int(code), total_seconds=time.monotonic()-started,
                passed=sum(r["outcome"] == "passed" for r in results.rows),
                failed=sum(r["outcome"] == "failed" and r["when"] == "call" for r in results.rows),
                errors=sum(r["outcome"] == "failed" and r["when"] != "call" for r in results.rows),
                skipped=sum(r["outcome"] == "skipped" for r in results.rows))
            write_new(args.output / "tests.json", dict(summary=summary, tests=results.rows))
            print(json.dumps(summary))
            if code: return int(code)
        else:
            from xoc_mcp_lab_harness.benchmark import benchmark
            result = asyncio.run(benchmark())
            write_new(args.output / "benchmark.json", result)
            print(json.dumps(result["summary"]))
    # Verify before Python/container exits. No remaining children in this PID namespace.
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip()
    assert not children, "LAB_CHILDREN_REMAIN"
    from xoc_mcp_lab_harness.resources import snapshot
    write_new(args.output / "final-process-check.json", dict(parent_pid=os.getpid(), children=children, checked_before_exit=True,
        resources=snapshot()))
    return 0


if __name__ == "__main__": raise SystemExit(main())
