"""Check the local Serverless-produced ZIP and compiled dependency graph."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory
    template = json.loads((directory/'cloudformation-template-update-stack.json').read_bytes())
    resources = template['Resources']
    assert resources['SophiaGateway']['Properties']['AuthorizerType'] == 'AWS_IAM'
    assert resources['SophiaReadTarget']['Properties']['TargetConfiguration']['Mcp']['Lambda']['LambdaArn'] == {
        'Fn::GetAtt': ['AgentcoreReadTargetLambdaFunction', 'Arn']}
    target = resources['AgentcoreReadTargetLambdaFunction']['Properties']
    assert target['Role'] == {'Fn::GetAtt': ['ReadTargetRole', 'Arn']}
    assert target['Timeout'] == 5
    assert not any(row['Type'] in ('AWS::Lambda::Url', 'AWS::ApiGateway::Method') for row in resources.values())
    def refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'Ref' and item in resources: yield item
                elif key == 'Fn::GetAtt': yield item[0]
                else: yield from refs(item)
        elif isinstance(value, list):
            for item in value: yield from refs(item)
    graph = {name: set(refs(row)) for name, row in resources.items()}
    def visit(name, seen):
        assert name not in seen, ('CFN cycle', name)
        for dependency in graph[name]: visit(dependency, seen | {name})
    for name in graph: visit(name, set())
    zips = list(directory.glob('*.zip')); assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as archive:
        files = set(archive.namelist())
        for name in ('src/handlers/agentcore_target.py', 'src/shared/agentcore_context.py',
                     'src/tool_gateway/executor.py', 'src/tool_gateway/agentcore_providers.py'):
            assert name in files, name
        assert not any('.env' in name or 'AGENTS.md' in name or 'xoc_mcp_lab' in name or name.startswith('tests/') for name in files)
    print(json.dumps({'passed': True, 'resources': len(resources), 'files': len(files),
        'zip_sha256': hashlib.sha256(zips[0].read_bytes()).hexdigest(),
        'template_sha256': hashlib.sha256((directory/'cloudformation-template-update-stack.json').read_bytes()).hexdigest(),
        'dependency_graph_acyclic': True, 'note': 'source-only offline package, not deployable artifact'}))


if __name__ == '__main__': main()
