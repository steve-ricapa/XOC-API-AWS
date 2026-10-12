import unittest
from unittest.mock import MagicMock, patch
import time

from src.shared import sophia_tool_chat as chat
from src.shared import sophia_agentcore_client as client
from src.tool_gateway.schemas import ToolContext, ToolRequest, ToolResult, ToolDecision

CTX = ToolContext(7, 7, 12, 'ADMIN', False, 'request-test', 'sophia_chat')


class FailureSemanticsTests(unittest.TestCase):
    def test_transport_failure_is_not_permission_denial(self):
        result = chat.model_result(ToolResult(ToolDecision.DENIED, code='agentcore_unavailable'), 'xoc.tickets.list', CTX)
        self.assertEqual(result['code'], 'unavailable')
        self.assertIsNone(result['data'])

    def test_policy_denial_remains_denied(self):
        result = chat.model_result(ToolResult(ToolDecision.DENIED, code='role_not_allowed'), 'xoc.tickets.list', CTX)
        self.assertEqual(result['code'], 'denied')

    def test_http_uses_remaining_total_not_half_budget(self):
        session = MagicMock()
        response = session.return_value.__enter__.return_value.post.return_value.__enter__.return_value
        response.status_code = 503
        with patch.object(client, 'settings', return_value=('arn', 'https://example.invalid/mcp', 'us-east-1', 'key')), \
             patch.object(client.trusted, 'validate_context'), patch.object(client.trusted, 'issue', return_value={}), \
             patch.object(client.boto3, 'Session'), patch.object(client, 'SigV4Auth'), \
             patch.object(client.requests, 'Session', session):
            result = client.AgentCoreExecution().execute(CTX, ToolRequest('xoc.tickets.list', {}), deadline=time.monotonic()+18)
        timeout = session.return_value.__enter__.return_value.post.call_args.kwargs['timeout']
        self.assertGreater(timeout.total, 4)
        self.assertLessEqual(timeout.total, 5)
        self.assertLessEqual(timeout.connect_timeout, .5)
        self.assertEqual(result.code, 'agentcore_unavailable')
        session.return_value.__enter__.return_value.post.assert_called_once()
