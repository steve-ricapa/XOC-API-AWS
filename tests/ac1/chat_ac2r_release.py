"""Release aislado Chat: cuenta/perfil fijos, sin secretos en salida o manifiesto."""
import argparse
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

from run_xoc_session import cli_json, session_environment

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT.parent / 'XOC-API-AWS-CHAT-AC2R'
OUT = ROOT / 'tests/ic1/.build/ac2r'
BASE = 'fe3b25f13833fb300ad1049b0e7974b37be8e706'
BASE_SHA = 'H/uO4Z5cNLVyRGLy/CIBibwN/Gen8BXzvrvW+6niP2E='
OVERLAY = ('src/handlers/routes/chat.py', 'src/shared/sophia_tool_chat.py',
    'src/shared/sophia_thread_operations.py', 'src/shared/sophia_tool_execution.py',
    'src/shared/sophia_agentcore_client.py', 'src/shared/agentcore_context.py',
    'src/tool_gateway/policy.py')


def prepare():
    assert cli_json(['sts','get-caller-identity'])['Account'] == '811776156524'
    config = cli_json(['lambda','get-function','--function-name','xoc-api-chat-prod-chatAgentsApi'])
    assert config['Configuration']['CodeSha256'] == BASE_SHA, 'Live Chat changed: re-accredit before overlay'
    with urllib.request.urlopen(config['Code']['Location'], timeout=45) as response:
        raw = response.read(150_000_001)
    assert len(raw) < 150_000_001
    assert base64.b64encode(hashlib.sha256(raw).digest()).decode() == BASE_SHA
    original = zipfile.ZipFile(io.BytesIO(raw))
    assert len(original.namelist()) == len(set(original.namelist()))
    source_count = 0
    for name in original.namelist():
        normalized = name.replace('\\', '/')
        assert not normalized.startswith('/') and '..' not in normalized.split('/')
        if normalized.startswith('src/') and normalized.endswith('.py'):
            source_count += 1
            expected = subprocess.check_output(['git','show', BASE+':'+normalized], cwd=ROOT)
            assert original.read(name) == expected, normalized
    assert source_count == 120
    OUT.mkdir(parents=True, exist_ok=True)
    artifact = OUT/'chat-isolated.zip'
    if artifact.exists():
        raise ValueError('Fresh artifact path required')
    manifest = {'base': BASE, 'base_code_sha256': BASE_SHA,
                'base_source_matches': source_count, 'overlay': {}, 'unchanged_entries': 0}
    seen = set()
    with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as archive:
        for info in original.infolist():
            normalized = info.filename.replace('\\', '/')
            data = original.read(info.filename)
            if normalized in OVERLAY:
                data = (RELEASE/normalized).read_bytes()
                seen.add(normalized)
            else:
                manifest['unchanged_entries'] += 1
            info.filename = normalized
            archive.writestr(info, data)
        for name in set(OVERLAY)-seen:
            archive.writestr(name, (RELEASE/name).read_bytes())
    for name in OVERLAY:
        manifest['overlay'][name] = hashlib.sha256((RELEASE/name).read_bytes()).hexdigest()
    manifest['artifact_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (OUT/'chat-artifact-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (OUT/'chat-base.zip').write_bytes(raw)
    print(json.dumps(manifest,indent=2))


def package(mode):
    env = session_environment()
    check = subprocess.run(['node', '-e',
        "const AWS=require('aws-sdk');new AWS.STS({region:'us-east-1',maxRetries:0,httpOptions:{connectTimeout:3000,timeout:5000}}).getCallerIdentity({},(e,d)=>{console.log(e?e.code:d.Account);process.exit(e||d.Account!=='811776156524'?1:0);});"],
        env=env,cwd=RELEASE,capture_output=True,text=True,timeout=20)
    if check.returncode: raise RuntimeError('SDK_IDENTITY_FAILED')
    print('SDK_ACCOUNT=811776156524',flush=True)
    env.update(SOPHIA_AGENTCORE_PROVISIONED='true', SOPHIA_TOOL_EXECUTION=mode,
        SOPHIA_TOOL_GATEWAY_ENABLED='true', SOPHIA_THREAD_CAPABILITIES_ENABLED='true',
        SOPHIA_GOVERNED_BASE_URL='https://prod-a-xoc-sophia.azurewebsites.net',
        SOPHIA_CHAT_RELEASE_ARTIFACT=str(OUT/'chat-isolated.zip'))
    command = ['cmd.exe','/d','/c','npm.cmd','run','package:chat:prod','--','--package',str(OUT/('chat-'+mode))]
    return subprocess.run(command,cwd=RELEASE,env=env).returncode


def inspect(mode):
    directory = OUT/('chat-'+mode)
    template = json.loads((directory/'cloudformation-template-update-stack.json').read_bytes())
    live = cli_json(['cloudformation','get-template','--stack-name','xoc-api-chat-prod'])['TemplateBody']
    if isinstance(live,str): live=json.loads(live)
    resources, old = template['Resources'], live['Resources']
    function = 'ChatAgentsApiLambdaFunction'
    changes = {}
    for key in set(resources)|set(old):
        if resources.get(key) != old.get(key):
            changes[key] = {'type': resources.get(key,old.get(key))['Type'],
                            'change': 'add' if key not in old else 'remove' if key not in resources else 'modify'}
            assert (key in (function,'SophiaAgentCoreCallerPolicy') or
                    resources.get(key,old.get(key))['Type']=='AWS::Lambda::Version'), key
    before, after = old[function]['Properties'], resources[function]['Properties']
    property_diff = [key for key in set(before)|set(after) if before.get(key)!=after.get(key)]
    assert set(property_diff) <= {'Code','Environment'}, property_diff
    assert after['Timeout']==20
    e0, e1=before['Environment']['Variables'], after['Environment']['Variables']
    env_diff = [key for key in set(e0)|set(e1) if e0.get(key)!=e1.get(key)]
    allowed_env = {'SOPHIA_TOOL_GATEWAY_ENABLED','SOPHIA_THREAD_CAPABILITIES_ENABLED',
        'SOPHIA_GOVERNED_BASE_URL','SOPHIA_TOOL_EXECUTION','SOPHIA_AGENTCORE_GATEWAY_ARN',
        'SOPHIA_AGENTCORE_GATEWAY_URL','SOPHIA_AGENTCORE_CONTEXT_KEY_ARN'}
    assert set(env_diff) <= allowed_env, env_diff
    assert e1['SOPHIA_TOOL_EXECUTION']==mode
    assert e1['SOPHIA_TOOL_GATEWAY_ENABLED']==e1['SOPHIA_THREAD_CAPABILITIES_ENABLED']=='true'
    manifest=json.loads((OUT/'chat-artifact-manifest.json').read_bytes())
    assert hashlib.sha256((OUT/'chat-isolated.zip').read_bytes()).hexdigest()==manifest['artifact_sha256']
    with zipfile.ZipFile(OUT/'chat-base.zip') as original, zipfile.ZipFile(OUT/'chat-isolated.zip') as final:
        for name in original.namelist():
            normalized=name.replace('\\','/')
            if normalized not in OVERLAY: assert original.read(name)==final.read(normalized), normalized
    policy=resources['SophiaAgentCoreCallerPolicy']['Properties']['PolicyDocument']
    assert {a for row in policy['Statement'] for a in row['Action']}=={'bedrock-agentcore:InvokeGateway','kms:GenerateMac'}
    assert all(row['Resource']!='*' for row in policy['Statement'])
    report={'status':'PASS','resources':changes,'lambda_property_diff':property_diff,
            'environment_diff':sorted(env_diff),'IAM_added':policy,
            'dependency_bytes_preserved':True,'voice_bytes_and_config_preserved':True,
            'victor_bytes_and_config_preserved':True,'template_sha256':hashlib.sha256(
                (directory/'cloudformation-template-update-stack.json').read_bytes()).hexdigest()}
    (OUT/('chat-'+mode+'-inspection.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


def deploy(mode):
    inspect(mode)
    live=cli_json(['lambda','get-function-configuration','--function-name','xoc-api-chat-prod-chatAgentsApi'])
    planned_sha=base64.b64encode(hashlib.sha256((OUT/'chat-isolated.zip').read_bytes()).digest()).decode()
    assert live['CodeSha256'] in (BASE_SHA,planned_sha), 'Chat drift: stop for source accreditation'
    if mode=='agentcore':
        gate=json.loads((OUT/'authenticated-disabled-smoke.json').read_bytes())
        assert gate['normal_chat']=='PASS' and gate['thread_continuation']=='PASS' and gate['c3_proposal']=='PASS'
        assert gate['human_session']=='LEGITIMATE' and gate['tools_executed']==0
    # Verify compatibility in memory. Neither key, digest, token nor raw settings
    # is persisted; the new Azure app already resolved the vault reference.
    value=cli_json(['secretsmanager','get-secret-value','--secret-id',live['Environment']['Variables']['JWT_SECRET_ARN']])['SecretString']
    try: aws_signing=json.loads(value).get('secret') or value
    except json.JSONDecodeError: aws_signing=value
    azure=subprocess.run(['az.cmd','functionapp','config','appsettings','list',
        '--subscription','884feb5e-8492-4d88-8777-6391393372f5','-g','rg-sophia-poc','-n','prod-a-xoc-cons','-o','json'],
        capture_output=True,text=True,timeout=45)
    assert azure.returncode==0
    azure_signing=next(row['value'] for row in json.loads(azure.stdout) if row['name']=='AGENT_JWT_SECRET')
    assert hmac.compare_digest(aws_signing,azure_signing), 'Service capability material differs'
    print('CAPABILITY_SIGNING_COMPATIBILITY=PASS',flush=True)
    env=session_environment()
    # Serverless consumes the reviewed state/template/artifact; no source repackaging.
    command=['cmd.exe','/d','/c','npm.cmd','run','deploy:chat:prod','--','--package',str(OUT/('chat-'+mode))]
    return subprocess.run(command,cwd=RELEASE,env=env).returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','package','inspect','deploy'])
    parser.add_argument('--mode',choices=['disabled','agentcore'],default='disabled')
    args=parser.parse_args()
    try:
        if args.action=='prepare': prepare()
        elif args.action=='package': raise SystemExit(package(args.mode))
        elif args.action=='inspect': inspect(args.mode)
        else: raise SystemExit(deploy(args.mode))
    except Exception as exc:
        print('AC2R_RELEASE_FAILED='+type(exc).__name__)
        raise SystemExit(1)
