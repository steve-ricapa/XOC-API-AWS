"""Inspect a real Serverless ZIP and import it in an isolated Lambda Linux image.

No AWS credentials, deployments or provider calls. Only the ZIP and this script
are mounted, read-only; package dependencies override the image's runtime SDK.
"""
import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import zipfile


def linux_import():
    root = Path('/tmp/ac2-package')
    with zipfile.ZipFile('/artifact.zip') as archive:
        for row in archive.infolist():
            path = root / row.filename
            if not path.resolve().is_relative_to(root):
                raise ValueError('unsafe ZIP path')
        archive.extractall(root)
    sys.path.insert(0, str(root))
    os.environ.update(AWS_EC2_METADATA_DISABLED='true', AWS_DEFAULT_REGION='us-east-1',
                      AWS_REGION='us-east-1')
    assert (root/'sqlalchemy/__init__.py').is_file(), 'SQLAlchemy missing from Linux ZIP paths'
    from src.handlers.agentcore_target import handler
    assert callable(handler)
    distributions = {d.metadata['Name']: d.version
                     for d in importlib.metadata.distributions(path=[str(root)])}
    import boto3
    import botocore
    import requests
    print(json.dumps({'importable': True, 'python': sys.version.split()[0],
                     'packaged_dependencies': distributions,
                     'effective': {'boto3': boto3.__version__, 'botocore': botocore.__version__,
                                   'requests': requests.__version__},
                     'sdk_origin': 'packaged' if (root/'boto3').exists() else 'Lambda image runtime'}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--image', default='public.ecr.aws/lambda/python:3.11')
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('new evidence required')
    template_path = args.directory/'cloudformation-template-update-stack.json'
    template = json.loads(template_path.read_bytes())
    resources = template['Resources']
    allowed = {'AWS::KMS::Key', 'AWS::IAM::Role', 'AWS::BedrockAgentCore::Gateway',
               'AWS::BedrockAgentCore::GatewayTarget', 'AWS::Lambda::Function',
               'AWS::Lambda::Version', 'AWS::Logs::LogGroup',
               'AWS::S3::Bucket', 'AWS::S3::BucketPolicy'}
    assert all(row['Type'] in allowed for row in resources.values())
    statements = resources['ReadTargetRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    assert {a for row in statements for a in row['Action']} == {
        'kms:VerifyMac', 'dynamodb:GetItem', 'dynamodb:Query',
        'logs:CreateLogStream', 'logs:PutLogEvents'}
    assert all(row['Resource'] != '*' for row in statements)
    def refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'Ref' and item in resources:
                    yield item
                elif key == 'Fn::GetAtt':
                    yield item[0]
                else:
                    yield from refs(item)
        elif isinstance(value, list):
            for item in value:
                yield from refs(item)
    graph = {name: set(refs(row)) for name, row in resources.items()}
    def visit(name, seen):
        assert name not in seen, 'CFN dependency cycle'
        for dependency in graph[name]:
            visit(dependency, seen | {name})
    for name in graph:
        visit(name, set())
    target = resources['AgentcoreReadTargetLambdaFunction']['Properties']
    assert target['Runtime'] == 'python3.11' and target['Architectures'] == ['x86_64']
    assert target['Timeout'] == 5 and 'VpcConfig' not in target
    assert target['Role'] == {'Fn::GetAtt': ['ReadTargetRole', 'Arn']}
    assert resources['ContextKey']['DeletionPolicy'] == 'Retain'
    assert resources['SophiaGateway']['Properties']['AuthorizerType'] == 'AWS_IAM'
    tools = resources['SophiaReadTarget']['Properties']['TargetConfiguration']['Mcp']['Lambda']['ToolSchema']['InlinePayload']
    assert {t['Name'] for t in tools} == {'tickets-list', 'tickets-get', 'documents-list', 'notifications-unread-count'}
    zips = list(args.directory.glob('*.zip'))
    assert len(zips) == 1
    artifact = zips[0].resolve()
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        assert not any('\\' in row.orig_filename for row in archive.infolist()), 'Non-POSIX ZIP paths'
        forbidden = [n for n in names if n != 'certifi/cacert.pem' and (n.startswith(('tests/', 'docs/', '.git/', 'node_modules/'))
                     or any(part in ('.env', '.aws', '.ssh', 'AGENTS.md', 'xoc_mcp_lab', '.build')
                            for part in Path(n).parts)
                     or n.endswith(('.pem', '.p8', '.key', '.ips')))]
        assert not forbidden, forbidden
        assert 'src/handlers/agentcore_target.py' in names
        assert 'requests/__init__.py' in names
        secret_pattern = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:AKIA|ASIA)[A-Z0-9]{16}')
        secret_matches = [n for n in names if n.startswith('src/') and secret_pattern.search(archive.read(n))]
        assert not secret_matches, secret_matches
        uncompressed = sum(row.file_size for row in archive.infolist())
        assert uncompressed < 250 * 1024 * 1024
    digest = hashlib.sha256(artifact.read_bytes()).digest()
    for resource in resources.values():
        if resource['Type'] == 'AWS::Lambda::Version':
            assert resource['Properties']['CodeSha256'] == base64.b64encode(digest).decode('ascii')
    public_block = resources['ServerlessDeploymentBucket']['Properties']['PublicAccessBlockConfiguration']
    assert all(public_block.get(k) is True for k in (
        'BlockPublicAcls', 'BlockPublicPolicy', 'IgnorePublicAcls', 'RestrictPublicBuckets'))
    image = subprocess.check_output(['docker', 'image', 'inspect', args.image,
                                    '--format', '{{.Id}}'], text=True).strip()
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only',
               '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=300m', '--memory', '768m',
               '--cpus', '2', '--pids-limit', '128', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--platform', 'linux/amd64',
               '--mount', f'type=bind,source={artifact},target=/artifact.zip,readonly',
               '--mount', f'type=bind,source={Path(__file__).resolve()},target=/artifact_check.py,readonly',
               '--entrypoint', '/var/lang/bin/python3', image, '-B', '/artifact_check.py', '--linux-import']
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr)
    report = {'passed': True, 'zip_bytes': artifact.stat().st_size,
              'uncompressed_bytes': uncompressed, 'files': len(names),
              'zip_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
              'template_sha256': hashlib.sha256(template_path.read_bytes()).hexdigest(),
              'resources': {k: v['Type'] for k, v in resources.items()},
              'linux_image': image, 'linux': json.loads(result.stdout),
              'static_iam_pass': True, 'dependency_graph_acyclic': True,
              'secret_pattern_matches': len(secret_matches),
              'note': 'Local deployable-artifact inspection; NOT evidence of cloud deployment.'}
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    if '--linux-import' in sys.argv:
        linux_import()
    else:
        main()
