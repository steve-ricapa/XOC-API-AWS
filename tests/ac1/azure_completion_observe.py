"""Read-only Azure Function error categories, no raw requests or credentials."""
import json
import subprocess
import requests

token = json.loads(subprocess.check_output([
    'az.cmd', 'account', 'get-access-token', '--subscription',
    '884feb5e-8492-4d88-8777-6391393372f5', '--resource',
    'https://api.applicationinsights.io', '-o', 'json']))['accessToken']
queries = [
    "traces | where timestamp > ago(3h) | where message startswith 'sophia_gateway_error' or message startswith 'sophia_capability_rejected' | extend code=extract('(?:code|reason)=([a-z_]+)',1,message) | project timestamp,operation_Id,code",
    "requests | where timestamp > ago(3h) | where name == 'sophia_run' | project timestamp,operation_Id,resultCode,duration | order by timestamp desc | take 30",
    "traces | where timestamp > ago(12h) | where message startswith 'sophia_gateway_error' or message startswith 'sophia_capability_rejected' | extend code=extract('(?:code|reason)=([a-z_]+)',1,message) | summarize n=count() by code",
    "requests | where timestamp > ago(12h) | where name == 'sophia_run' | summarize n=count(), median_ms=percentile(duration,50), max_ms=max(duration) by resultCode",
]
for query in queries:
    response = requests.get('https://api.applicationinsights.io/v1/apps/c854497d-ab9d-4dd7-be21-8ecbb68cb789/query',
        headers={'Authorization': 'Bearer ' + token}, params={'query': query}, timeout=30)
    print('http_status', response.status_code)
    if response.ok:
        print(json.dumps(response.json().get('tables', [])))
