"""Metadatos AC-2R y detección agregada de secretos: nunca imprimir logs crudos."""
import json
from pathlib import Path
import re
import subprocess
import time

import requests
from run_xoc_session import cli_json

OUT=Path(__file__).resolve().parents[1]/'ic1/.build/ac2r'
SUB='884feb5e-8492-4d88-8777-6391393372f5'
SENSITIVE=re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|AWS4-HMAC-SHA256 Credential=|[?&]sig=[A-Za-z0-9%+/]+|AccountKey=',re.I)


def azure_logs():
    token=subprocess.run(['az.cmd','account','get-access-token','--subscription',SUB,
        '--resource','https://api.applicationinsights.io','-o','json'],capture_output=True,text=True,timeout=40)
    if token.returncode: return {'status':'TOKEN_UNAVAILABLE'}
    query='traces | where timestamp > ago(2h) | project message'
    response=requests.post('https://api.applicationinsights.io/v1/apps/c854497d-ab9d-4dd7-be21-8ecbb68cb789/query',
        headers={'Authorization':'Bearer '+json.loads(token.stdout)['accessToken']},
        json={'query':query},timeout=30,allow_redirects=False)
    if response.status_code!=200: return {'status':'QUERY_UNAVAILABLE','http_status':response.status_code}
    rows=[str(row[0]) for table in response.json().get('tables',[]) for row in table.get('rows',[])]
    return {'status':'REVIEWED' if rows else 'NO_TRACES_YET','trace_count':len(rows),
            'credential_pattern_count':sum(bool(SENSITIVE.search(row)) for row in rows),
            'payload_marker_count':sum(any(marker in row for marker in ('body_sha256','tool_result','Responde solamente Hola','Muestrame mis tickets')) for row in rows)}


def main():
    assert cli_json(['sts','get-caller-identity'])['Account']=='811776156524'
    config=cli_json(['lambda','get-function-configuration','--function-name','xoc-api-chat-prod-chatAgentsApi'])
    env=config['Environment']['Variables']
    report={'chat':{key:config.get(key) for key in ('FunctionName','CodeSha256','State','LastUpdateStatus','Timeout','Version')},
        'flags':{key:value for key,value in env.items() if key in (
            'SOPHIA_TOOL_EXECUTION','SOPHIA_TOOL_GATEWAY_ENABLED','SOPHIA_THREAD_CAPABILITIES_ENABLED','SOPHIA_GOVERNED_BASE_URL')},
        'aws_logs':{}}
    for group in ('/aws/lambda/xoc-api-chat-prod-chatAgentsApi','/aws/lambda/xoc-api-agentcore-prod-read'):
        events=cli_json(['logs','filter-log-events','--log-group-name',group,'--start-time',str(int((time.time()-1800)*1000)),
                         '--limit','1000','--no-paginate']).get('events',[])
        messages=[row.get('message','') for row in events]
        report['aws_logs'][group]={'records_sampled':len(messages),
            'credential_pattern_count':sum(bool(SENSITIVE.search(row)) for row in messages),
            'bounded_sample':True}
    report['azure_logs']=azure_logs()
    (OUT/('cloud-evidence-'+str(time.time_ns())+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
