"""Use existing offline dependency directory, NOT the MCP virtualenv."""
import argparse
import logging
import os
from pathlib import Path
import sys
import unittest
import shutil
from xoc_mcp_lab.isolation import Isolation, backend_path

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--backend",required=True)
    p.add_argument("--dependencies",required=True)
    p.add_argument("--suite",choices=("gateway","focal","all"),required=True)
    a=p.parse_args()
    backend=backend_path(a.backend)
    dependencies=Path(a.dependencies).resolve(strict=True)
    if dependencies.name!=".tmp-ticket-sync-tests": raise ValueError("Use the existing offline test dependencies")
    node=shutil.which("node")
    with Isolation():
        # The existing infrastructure tests use Node to inspect local templates.
        if node: os.environ["PATH"]=str(Path(node).parent)
        os.environ.update(JWT_SECRET_KEY="offline-testing-secret-32-characters",
            DATABASE_URL="sqlite:///:memory:",AZURE_AI_PROJECT_API_KEY="offline-testing")
        logging.basicConfig(level=logging.CRITICAL,handlers=[logging.NullHandler()],force=True)
        sys.path[:0]=[str(dependencies),str(backend)]
        os.chdir(backend)
        patterns={
            "gateway":["test_tool_gateway_policy.py","test_tool_gateway_executor.py"],
            "focal":["test_sophia_phase_d.py","test_sophia_phase_d_infra.py","test_chat_access_hardening.py",
                "test_chat_tool_gateway_integration.py","test_chat_ticket_confirmations.py",
                "test_tool_gateway_policy.py","test_tool_gateway_executor.py","test_sophia_thread_operations.py"],
            "all":["test*.py"]}[a.suite]
        suite=unittest.TestSuite(unittest.defaultTestLoader.discover("tests",pattern=pattern) for pattern in patterns)
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() else 1
if __name__=="__main__":
    raise SystemExit(main())
