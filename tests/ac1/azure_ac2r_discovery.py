"""Lecturas Azure AC-2R sanitizadas. Nunca emite settings secretos ni tokens."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

SUB='884feb5e-8492-4d88-8777-6391393372f5'
RG='rg-sophia-poc'
APP='prod-a-xoc-cons'
OUT=Path(__file__).resolve().parents[1]/'ic1/.build/ac2r'

def az(args):
    result=subprocess.run(['az.cmd',*args,'--subscription',SUB,'-o','json'],capture_output=True,text=True,timeout=45)
    if result.returncode:
        code=next((x for x in ('AuthorizationFailed','AuthorizationPermissionMismatch','Forbidden','NotFound') if x in result.stderr),'AZURE_READ_FAILED')
        raise RuntimeError(code)
    return json.loads(result.stdout)

def main():
    safe={'FUNCTIONS_WORKER_RUNTIME','FUNCTIONS_EXTENSION_VERSION','SCM_DO_BUILD_DURING_DEPLOYMENT',
          'ENABLE_ORYX_BUILD','SOPHIA_GATEWAY_REQUIRED','SOPHIA_AGENT_ID','PROJECT_ENDPOINT','AZURE_AI_PROJECT_ENDPOINT',
          'AZURE_AI_PROJECT_API_VERSION','BACKEND_BASE_URL','BACKEND_TIMEOUT_SECONDS','JWT_ALGORITHM'}
    settings=az(['functionapp','config','appsettings','list','-g',RG,'-n',APP])
    report={'settings':{}}
    for entry in settings:
        name,value=entry['name'],entry['value'] or ''
        item={'present':bool(value),'key_vault_reference':value.startswith('@Microsoft.KeyVault(')}
        if name in safe: item['value']=value
        if name in ('WEBSITE_RUN_FROM_PACKAGE','SCM_RUN_FROM_PACKAGE'):
            if value.startswith('https://'):
                url=urlsplit(value)
                item.update(host=url.hostname,path=url.path,query_present=bool(url.query))
            else: item['mode']=value if value in ('','0','1') else 'CONFIGURED_REDACTED'
        if name=='AzureWebJobsStorage':
            parts=dict(p.split('=',1) for p in value.split(';') if '=' in p)
            item['account_name']=parts.get('AccountName')
        report['settings'][name]=item
    site=f'/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.Web/sites/{APP}'
    source=az(['functionapp','deployment','source','show','-g',RG,'-n',APP])
    report['deployment_source']={k:source.get(k) for k in ('branch','isManualIntegration','scmType')}
    report['deployment_source']['repo_configured']=bool(source.get('repoUrl'))
    deployments=az(['rest','--method','get','--url','https://management.azure.com'+site+'/deployments?api-version=2024-04-01'])
    report['deployments']=[{'id':d.get('name'),'status':d.get('properties',{}).get('status')} for d in deployments.get('value',[])]
    report['vaults']=az(['keyvault','list','-g',RG,'--query','[].{name:name,id:id}'])
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'azure-preflight.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__': main()
