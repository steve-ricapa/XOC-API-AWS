"""Offline AC-1 runner. No AWS/Azure credentials, external network or secret files."""
import argparse
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import unittest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mcp', type=Path, required=True)
    p.add_argument('--dependencies', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if output.exists(): raise ValueError('new evidence required')
    sys.path.insert(0, str(args.mcp.resolve()))
    from xoc_mcp_lab.isolation import Isolation
    node = shutil.which('node')
    with Isolation() as guard:
        if args.dependencies: sys.path.insert(0, str(args.dependencies.resolve()))
        sys.path.insert(0, str(root))
        if node: os.environ['PATH'] = str(Path(node).parent)
        os.environ.update(JWT_SECRET_KEY='ac1-offline-test-only', DATABASE_URL='sqlite:///:memory:')
        logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()], force=True)
        suite = unittest.defaultTestLoader.discover(str(root/'tests'), pattern='test_sophia_agentcore*.py')
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        report = {'passed': result.wasSuccessful(), 'total': result.testsRun,
                  'failures': len(result.failures), 'errors': len(result.errors),
                  'skipped': [(str(test), reason) for test, reason in result.skipped],
                  'network_attempts': guard.network_attempts, 'secret_file_attempts': guard.secret_file_attempts,
                  'output': stream.getvalue()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'output'}))
    if not report['passed']: print(report['output'])
    return int(not report['passed'])


if __name__ == '__main__': raise SystemExit(main())
