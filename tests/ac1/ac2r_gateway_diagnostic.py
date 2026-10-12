"""Real READ_ONLY service fixture; not a human Chat/session end-to-end test.

Uses xoc-dev only. No stored credentials, raw data, JWT, configuration changes,
or local tool fallback. Optional aging diagnoses a local clock ahead of AWS;
it does NOT alter signed timestamps, TTL, server validation or production code.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

from run_xoc_session import cli_json, session_environment

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT.parent / 'XOC-API-AWS-CHAT-AC2R'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assertion-delay-seconds', type=int, choices=(0, 2), default=0)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'tests/ic1/.build/ac2r'):
        raise ValueError('Evidence must stay in ignored AC2R directory')
    if output.exists():
        raise ValueError('Do not overwrite earlier evidence')
    cfg = cli_json(['lambda', 'get-function-configuration', '--function-name', 'xoc-api-chat-prod-chatAgentsApi'])
    # Resolve profile first. Then isolate boto3 from unrelated/default profiles.
    env = session_environment()
    for key in list(os.environ):
        if key.startswith('AWS_'):
            del os.environ[key]
    os.environ.update(env)
    os.environ.pop('AWS_PROFILE', None)
    os.environ.pop('AWS_DEFAULT_PROFILE', None)
    sys.path.insert(0, str(RELEASE))
    import boto3
    from src.shared import sophia_agentcore_client as client
    from src.tool_gateway.schemas import ToolContext, ToolRequest
    assert boto3.client('sts').get_caller_identity()['Account'] == '811776156524'
    for key, value in cfg['Environment']['Variables'].items():
        if key.startswith('SOPHIA_AGENTCORE_'):
            os.environ[key] = value
    original = client.trusted.issue

    def aged(*positional, **named):
        assertion = original(*positional, **named)
        if args.assertion_delay_seconds:
            time.sleep(args.assertion_delay_seconds)
        return assertion

    client.trusted.issue = aged
    rows = []
    try:
        for tool, arguments in (
            ('xoc.tickets.list', {'limit': 1}),
            ('xoc.notifications.unread_count', {}),
            ('xoc.documents.list', {'limit': 1}),
        ):
            rid = 'repair-fixture-' + uuid.uuid4().hex
            context = ToolContext(2147483601, 2147483601, 2147483601, 'ADMIN', False, rid, 'sophia_chat')
            start = time.monotonic()
            result = client.AgentCoreExecution().execute(
                context, ToolRequest(tool, arguments, rid), deadline=start + 18)
            row = {'tool': tool, 'request_id': rid, 'status': result.status.value,
                   'code': result.code, 'has_audit': bool(result.audit_id),
                   'duration_ms': round(1000 * (time.monotonic() - start))}
            # Fixture tenant must be empty; never print or persist returned rows.
            if result.data and result.data.get('count', 0) != 0:
                raise RuntimeError('Fixture tenant unexpectedly contains data')
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        client.trusted.issue = original
    report = {'scope': 'direct_service_fixture_not_chat_e2e',
              'chat_mode_unchanged': cfg['Environment']['Variables']['SOPHIA_TOOL_EXECUTION'],
              'assertion_delay_seconds': args.assertion_delay_seconds, 'tests': rows,
              'status': 'PASS' if all(r['status'] == 'allowed' and r['has_audit'] for r in rows) else 'FAIL'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report['status'] != 'PASS'


if __name__ == '__main__':
    raise SystemExit(main())
