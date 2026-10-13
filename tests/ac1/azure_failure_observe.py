"""Read-only, narrowly scoped AC-2R failure telemetry; no payloads or secrets."""
import json
import subprocess
import requests


def main():
    token = json.loads(subprocess.check_output([
        'az.cmd', 'account', 'get-access-token', '--subscription',
        '884feb5e-8492-4d88-8777-6391393372f5', '--resource',
        'https://api.applicationinsights.io', '-o', 'json']))['accessToken']
    scope = "where operation_Id == '45995be46cf737b9a9831c5c0fe18901'"
    queries = [
        f"dependencies | {scope} | project timestamp,type,resultCode,success,duration | order by timestamp asc",
        f"exceptions | {scope} | project timestamp,type,problemId",
        f"traces | {scope} | summarize n=count() by severityLevel",
        "traces | where timestamp > ago(20m) | where message startswith 'sophia_provider_stage' "
        "| extend phase=extract('phase=([a-z_]+)',1,message), stage=extract('stage=([a-z_]+)',1,message), "
        "outcome=extract('outcome=([a-z_]+)',1,message), http_status=extract('http_status=([0-9]+)',1,message), "
        "duration_ms=extract('duration_ms=([0-9]+)',1,message) "
        "| project timestamp,operation_Id,phase,stage,outcome,http_status,duration_ms",
        "traces | where timestamp > ago(20m) | where message startswith 'sophia_provider_terminal' "
        "| extend phase=extract('phase=([a-z_]+)',1,message), status=extract('status=([a-z_]+)',1,message) "
        "| project timestamp,operation_Id,phase,status",
    ]
    for query in queries:
        response = requests.get(
            'https://api.applicationinsights.io/v1/apps/c854497d-ab9d-4dd7-be21-8ecbb68cb789/query',
            headers={'Authorization': 'Bearer ' + token}, params={'query': query}, timeout=30)
        print('http_status', response.status_code)
        if response.ok:
            print(json.dumps(response.json().get('tables', [])))


if __name__ == '__main__':
    main()
