"""Offline backend regression with a retained, bounded test-outcome report."""
import argparse
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import unittest
import traceback

PATTERNS = ("test_sophia_phase_d.py", "test_sophia_phase_d_infra.py",
    "test_chat_access_hardening.py", "test_chat_tool_gateway_integration.py",
    "test_chat_ticket_confirmations.py", "test_tool_gateway_policy.py",
    "test_tool_gateway_executor.py", "test_sophia_thread_operations.py",
    "test_sophia_mcp_execution.py")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("focal", "all"), default="focal")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    target = args.output.resolve()
    if target.exists() or not target.is_relative_to(root / "tests/ic1/.build"):
        raise ValueError("FT_NEW_EVIDENCE_REQUIRED")
    dependencies = args.dependencies.resolve(strict=True)
    sys.path.insert(0, str(args.mcp.resolve(strict=True)))
    from xoc_mcp_lab.isolation import Isolation
    node = shutil.which("node")
    rows = []
    class Result(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            rows.append(dict(test=test.id(), outcome="passed"))
        def addFailure(self, test, err):
            super().addFailure(test, err)
            frames = traceback.extract_tb(err[2])
            frame = next((f for f in reversed(frames) if Path(f.filename).name.startswith("test_")), frames[-1])
            rows.append(dict(test=test.id(), outcome="failed", category=err[0].__name__,
                             location=dict(file=Path(frame.filename).name, line=frame.lineno)))
        def addError(self, test, err):
            super().addError(test, err)
            rows.append(dict(test=test.id(), outcome="error", category=err[0].__name__))
    with Isolation() as guard:
        if node:
            os.environ["PATH"] = str(Path(node).parent)
        os.environ.update(JWT_SECRET_KEY="offline-testing-secret-32-characters",
            DATABASE_URL="sqlite:///:memory:", AZURE_AI_PROJECT_API_KEY="offline-testing")
        logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()], force=True)
        sys.path[:0] = [str(dependencies), str(root)]
        os.chdir(root)
        patterns = PATTERNS if args.suite == "focal" else ("test*.py",)
        suite = unittest.TestSuite(unittest.defaultTestLoader.discover("tests", pattern=p) for p in patterns)
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(suite)
        report = dict(passed=result.wasSuccessful(), total=result.testsRun, suite=args.suite,
                      python=sys.version, cases=rows,
                      network_attempts=guard.network_attempts, secret_file_attempts=guard.secret_file_attempts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}))
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
