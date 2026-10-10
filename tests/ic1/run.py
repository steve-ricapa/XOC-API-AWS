"""Installed IC-1 runner: real supervised L6, real backend handler, fixtures only."""
import asyncio
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import unittest


async def main():
    sys.path.insert(0, "/ic1/harness")
    from no_stdio import install, evidence
    install("/tmp/ic1-no-stdio")
    from xoc_mcp_lab.isolation import Isolation
    # This guard predates backend import, rejects credentials/files/external sockets.
    with Isolation() as isolation:
        sys.path[:0] = ["/ic1/dependencies", "/ic1/backend", "/ic1/harness"]
        os.environ["JWT_SECRET_KEY"] = "IC1-PUBLIC-FICTIONAL-SIGNING-KEY-ONLY"
        logging.disable(logging.CRITICAL)
        from xoc_mcp_lab_l6.harness import SupervisedProcess, child_identity
        import integration
        server = SupervisedProcess()
        try:
            await server.wait_ready()
            identity = child_identity(server.server_pid)
            assert identity and identity["ppid"] == server.proc.pid
            integration.SERVER = server
            output = io.StringIO()
            suite = unittest.defaultTestLoader.loadTestsFromModule(integration)
            result = await asyncio.to_thread(unittest.TextTestRunner(stream=output, verbosity=2).run, suite)
            await server.wait_idle()
            events = server.events()
            report = dict(lab_only=True, server_ready=True, supervised=True,
                generation=server.generation, cases=integration.EVIDENCE,
                tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                details=output.getvalue(), network_attempts=isolation.network_attempts,
                secret_file_attempts=isolation.secret_file_attempts,
                source_manifest_sha256=hashlib.sha256(Path("/ic1/source-manifest.json").read_bytes()).hexdigest())
            # Test failures contain only fixture IDs/asserted counters, not auth headers.
            report["events"] = [{k: row[k] for k in ("event", "request_id", "handler_calls") if k in row}
                                for row in events if row.get("request_id", "").startswith("lab-ic1-")]
            report["passed"] = result.wasSuccessful() and not isolation.network_attempts and not isolation.secret_file_attempts
        except Exception as error:
            report = dict(passed=False, error=type(error).__name__, control=server.messages)
        finally:
            await server.close()
        report["cleanup"] = server.close_evidence
        report["no_stdio"] = evidence("/tmp/ic1-no-stdio")
        report["http_tool_calls"] = sum(row["mcp_tools_call"] for row in integration.EVIDENCE)
        report["passed"] = bool(report["passed"] and server.close_evidence
            and report["no_stdio"]["passed"] and report["http_tool_calls"] > 0
            and server.close_evidence.get("supervisor_reaped")
            and server.close_evidence.get("supervisor_absent")
            and not server.close_evidence.get("own_server_residual")
            and not server.close_evidence.get("harness_forced"))
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
