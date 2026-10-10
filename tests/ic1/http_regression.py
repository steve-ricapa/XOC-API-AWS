"""Fresh FT-2 HTTP-only regression on the immutable installed L6 baseline."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys


def main():
    sys.path.insert(0, "/ic1/harness")
    from no_stdio import install, evidence
    mode = sys.argv[1]
    if mode != "control":
        install("/tmp/ft-http-guard")
    if mode == "guard":
        from guard_selftest import check
        result = check()
        print(json.dumps(result))
        return int(not result["passed"])
    from xoc_mcp_lab_l6.integrity import require_l6_installed
    require_l6_installed()
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    with Isolation() as isolation:
        configure_logs()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if mode == "tests":
                import pytest
                import xoc_mcp_lab_l6
                rows = []
                class Reporter:
                    def pytest_runtest_logreport(self, report):
                        if report.when == "call" or report.failed or report.skipped:
                            rows.append(dict(test=report.nodeid, phase=report.when, outcome=report.outcome))
                code = int(pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider",
                    str(Path(xoc_mcp_lab_l6.__file__).parent / "tests")], plugins=[Reporter()]))
                result = dict(passed=code == 0, tests=rows)
            elif mode in ("recovery", "smoke", "control"):
                from xoc_mcp_lab_l6.acceptance import run_cases
                result = asyncio.run(run_cases(["C04"] if mode in ("smoke", "control") else None))
                result["passed"] = result["all_passed"]
            elif mode == "provenance":
                from xoc_mcp_lab_l6.harness import provenance
                result = asyncio.run(provenance())
                result["passed"] = result["all_installed"]
            else:
                raise ValueError("FT_UNKNOWN_MODE")
        result["no_stdio"] = evidence("/tmp/ft-http-guard") if mode != "control" else {"passed": True, "control_only": True}
        if not result["passed"]:
            result["test_output"] = output.getvalue()
        result["external_network_attempts"] = isolation.network_attempts
        result["secret_file_attempts"] = isolation.secret_file_attempts
        result["passed"] = bool(result["passed"] and result["no_stdio"]["passed"]
                                and not isolation.network_attempts and not isolation.secret_file_attempts)
        result["mode"] = mode
        print(json.dumps(result))
        return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
