"""Offline Chat template checks; Node evaluates local JS, never Serverless/AWS."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
STAGES = ('dev', 'staging', 'prod')
ARN_PREFIX = 'arn:aws:dynamodb:${aws:region}:${aws:accountId}:table/'
SIDS = ('SophiaReadDocumentJobs', 'SophiaCountUnreadNotifications')


class PhaseDInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node.js is required to validate the Serverless templates')
        script = r"""
const common = require('./serverless/services/lib/common');
const stage = process.env.STAGE;
const chat = require('./serverless.chat');
const reports = require('./serverless/services/reports-resources')(stage);
const ops = require('./serverless/services/ops-resources')(stage);
process.stdout.write(JSON.stringify({
  chat,
  baseIam: common.iamStatements(stage, {
    database: true, vpc: true, jwt: true, dynamo: true, events: true,
  }),
  baseEnvironment: common.commonEnvironment(stage),
  reports: reports.Resources.ReportsTable.Properties,
  inbox: ops.Resources.UserNotificationInboxTable.Properties,
}));
"""
        cls.snapshots = {}
        for stage in STAGES:
            result = subprocess.run(
                [node, '-e', script], cwd=ROOT,
                env={**os.environ, 'STAGE': stage},
                capture_output=True, text=True, encoding='utf-8',
                check=True, timeout=20,
            )
            cls.snapshots[stage] = json.loads(result.stdout)

    def test_new_grants_are_only_query_on_exact_stage_resources(self):
        for stage, snapshot in self.snapshots.items():
            with self.subTest(stage=stage):
                statements = snapshot['chat']['provider']['iam']['role']['statements']
                additions = statements[len(snapshot['baseIam']):]
                expected = []
                for sid, table, index in (
                    (SIDS[0], f'xoc-api-reports-{stage}-reports', 'StatusIndex'),
                    (SIDS[1], f'xoc-api-ops-{stage}-user-notification-inbox', 'UserStatusCreatedAtIndex'),
                ):
                    expected.append({
                        'Sid': sid, 'Effect': 'Allow', 'Action': ['dynamodb:Query'],
                        'Resource': [ARN_PREFIX + table, ARN_PREFIX + table + '/index/' + index],
                    })
                self.assertEqual(expected, additions)
                # Do not confuse existing dev-wide permissions with these new grants.
                self.assertNotIn('*', json.dumps(additions))

    def test_resources_and_environment_match_existing_table_definitions(self):
        for stage, snapshot in self.snapshots.items():
            with self.subTest(stage=stage):
                chat = snapshot['chat']
                env = {**chat['provider']['environment'],
                       **chat['functions']['chatAgentsApi']['environment']}
                for sid, resource, variable, index in (
                    (SIDS[0], snapshot['reports'], 'REPORTS_TABLE_NAME', 'StatusIndex'),
                    (SIDS[1], snapshot['inbox'], 'USER_NOTIFICATION_INBOX_TABLE_NAME', 'UserStatusCreatedAtIndex'),
                ):
                    self.assertEqual(resource['TableName'], env[variable])
                    self.assertIn(index, {gsi['IndexName'] for gsi in resource['GlobalSecondaryIndexes']})
                    statement = next(item for item in chat['provider']['iam']['role']['statements']
                                     if item.get('Sid') == sid)
                    self.assertEqual([
                        ARN_PREFIX + resource['TableName'],
                        ARN_PREFIX + resource['TableName'] + '/index/' + index,
                    ], statement['Resource'])

    def test_existing_common_iam_and_environment_are_unchanged(self):
        for stage, snapshot in self.snapshots.items():
            with self.subTest(stage=stage):
                provider = snapshot['chat']['provider']
                self.assertEqual(snapshot['baseIam'], provider['iam']['role']['statements'][:-2])
                self.assertEqual(snapshot['baseEnvironment'], provider['environment'])
                self.assertNotIn('USER_NOTIFICATION_INBOX_TABLE_NAME', provider['environment'])

    def test_chat_stays_opt_in_with_no_new_functions_resources_or_timeout(self):
        for stage, snapshot in self.snapshots.items():
            with self.subTest(stage=stage):
                config = snapshot['chat']
                self.assertEqual({'chatAgentsApi'}, set(config['functions']))
                self.assertFalse(config.get('resources'))
                function = config['functions']['chatAgentsApi']
                self.assertEqual(20, function['timeout'])
                self.assertEqual({
                    'SOPHIA_TOOL_GATEWAY_ENABLED': "${env:SOPHIA_TOOL_GATEWAY_ENABLED, 'false'}",
                    'SOPHIA_THREAD_CAPABILITIES_ENABLED': "${env:SOPHIA_THREAD_CAPABILITIES_ENABLED, 'false'}",
                    'USER_NOTIFICATION_INBOX_TABLE_NAME': f'xoc-api-ops-{stage}-user-notification-inbox',
                }, function['environment'])


if __name__ == '__main__':
    unittest.main()
