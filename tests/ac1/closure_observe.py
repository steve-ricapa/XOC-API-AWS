"""Read-only closure evidence. Never prints raw CloudWatch messages or settings."""
import datetime
import json
import re
import statistics
from run_xoc_session import cli_json


def main():
    assert cli_json(['sts', 'get-caller-identity'])['Account'] == '811776156524'
    now = datetime.datetime.now(datetime.timezone.utc)
    start = int((now - datetime.timedelta(hours=24)).timestamp() * 1000)
    output = {'observed_utc': now.isoformat(), 'window_hours': 24, 'functions': {}}
    for function in ('xoc-api-chat-prod-chatAgentsApi', 'xoc-api-agentcore-prod-read'):
        cfg = cli_json(['lambda', 'get-function-configuration', '--function-name', function])
        rows, token = [], None
        while True:
            args = ['logs', 'filter-log-events', '--log-group-name', '/aws/lambda/' + function,
                    '--start-time', str(start), '--no-paginate']
            if token:
                args += ['--next-token', token]
            page = cli_json(args)
            rows.extend(page.get('events', []))
            next_token = page.get('nextToken')
            if not next_token:
                break
            if next_token == token:
                raise RuntimeError('Pagination did not progress')
            token = next_token
        metrics = {}
        patterns = {
            'lambda_duration_ms': r'REPORT RequestId:.*?\bDuration: ([\d.]+) ms',
            'init_duration_ms': r'Init Duration: ([\d.]+) ms',
            'first_turn_ms': r'sophia_chat_stage stage=first_turn .*?duration_ms=(\d+)',
            'second_turn_ms': r'sophia_chat_stage stage=second_turn .*?duration_ms=(\d+)',
            'agentcore_ms': r'sophia_agentcore .*?duration_ms=(\d+)',
        }
        for name, pattern in patterns.items():
            values = [float(m.group(1)) for row in rows if (m := re.search(pattern, row['message']))]
            if values:
                metrics[name] = {'n': len(values), 'min': min(values), 'median': statistics.median(values), 'max': max(values)}
        output['functions'][function] = {
            'state': cfg['State'], 'update': cfg['LastUpdateStatus'], 'sha': cfg['CodeSha256'],
            'timeout': cfg['Timeout'], 'mode': cfg.get('Environment', {}).get('Variables', {}).get('SOPHIA_TOOL_EXECUTION'),
            'events_scanned': len(rows), 'metrics': metrics,
            'timeout_messages': sum('Task timed out after' in row['message'] for row in rows),
        }
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
