"""Evaluate Serverless JS locally; no AWS resolution or deployment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AgentCoreInfraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = {}
        node = shutil.which('node')
        if not node: raise unittest.SkipTest('Node not installed in Python-only Linux test image')
        for stage in ('dev', 'staging', 'prod'):
            cls.configs[stage] = json.loads(subprocess.check_output([node, '-e',
                "console.log(JSON.stringify({agentcore:require('./serverless.agentcore'),chat:require('./serverless.chat')}))"],
                cwd=ROOT, env={**os.environ, 'STAGE': stage, 'SOPHIA_AGENTCORE_PROVISIONED': 'true'}, text=True))

    def test_gateway_requires_iam_and_private_lambda(self):
        for configs in self.configs.values():
            cfg = configs['agentcore']; resources = cfg['resources']['Resources']
            self.assertEqual('AWS_IAM', resources['SophiaGateway']['Properties']['AuthorizerType'])
            self.assertEqual(['2026-07-28'], resources['SophiaGateway']['Properties']['ProtocolConfiguration']['Mcp']['SupportedVersions'])
            self.assertNotIn('ExceptionLevel', resources['SophiaGateway']['Properties'])
            function = cfg['functions']['agentcoreReadTarget']
            self.assertNotIn('events', function); self.assertNotIn('url', function)
            self.assertEqual(5, function['timeout']); self.assertNotIn('iam', cfg['provider'])
            self.assertNotIn('DATABASE_SECRET_ARN', cfg['provider']['environment'])

    def test_roles_and_exact_resource_permissions(self):
        for stage, configs in self.configs.items():
            resources = configs['agentcore']['resources']['Resources']
            statements = resources['ReadTargetRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
            actions = {action for row in statements for action in row['Action']}
            self.assertEqual({'kms:VerifyMac', 'dynamodb:GetItem', 'dynamodb:Query', 'logs:CreateLogStream', 'logs:PutLogEvents'}, actions)
            self.assertNotIn('Resource": "*"', json.dumps(statements))
            gateway = resources['GatewayRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
            self.assertEqual(['lambda:InvokeFunction'], gateway[0]['Action'])
            self.assertIn(f'xoc-api-agentcore-{stage}-read', json.dumps(gateway))
            caller = configs['chat']['resources']['Resources']['SophiaAgentCoreCallerPolicy']['Properties']['PolicyDocument']['Statement']
            self.assertEqual(['bedrock-agentcore:InvokeGateway', 'kms:GenerateMac'], [row['Action'][0] for row in caller])
            self.assertNotIn('*', json.dumps(caller))

    def test_four_tools_and_separate_kms_key(self):
        from src.shared.agentcore_context import READ_TOOLS
        for configs in self.configs.values():
            resources = configs['agentcore']['resources']['Resources']
            tools = resources['SophiaReadTarget']['Properties']['TargetConfiguration']['Mcp']['Lambda']['ToolSchema']['InlinePayload']
            self.assertEqual(set(READ_TOOLS.values()), {t['Name'] for t in tools})
            for tool in tools: self.assertEqual(['assertion', 'arguments'], tool['InputSchema']['Required'])
            self.assertEqual('HMAC_256', resources['ContextKey']['Properties']['KeySpec'])
            self.assertEqual('Retain', resources['ContextKey']['DeletionPolicy'])

    def test_activation_is_separate_and_no_cloud_fallback(self):
        for configs in self.configs.values():
            chat = configs['chat']['functions']['chatAgentsApi']
            self.assertEqual(20, chat['timeout'])
            self.assertEqual("${env:SOPHIA_TOOL_EXECUTION, 'disabled'}", chat['environment']['SOPHIA_TOOL_EXECUTION'])

    def test_no_plaintext_credentials_or_laboratory_dependencies(self):
        for configs in self.configs.values():
            cfg = configs['agentcore']
            self.assertNotIn('JWT_SECRET', json.dumps(cfg))
            self.assertNotIn('xoc_mcp_lab', json.dumps(cfg))
            self.assertEqual('!**', cfg['package']['patterns'][0])


if __name__ == '__main__': unittest.main()
