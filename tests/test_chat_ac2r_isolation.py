import os
import time
import unittest
from unittest.mock import patch

from src.handlers.routes import chat
from src.shared.sophia_tool_execution import selected_execution
from src.tool_gateway.schemas import ToolContext, ToolRequest


class IsolatedChatTests(unittest.TestCase):
    def test_override_preserves_existing_tenant_metadata_and_routes(self):
        old = {'function_base_url': 'https://shared.example', 'extra_json': {'intent': 'keep'},
               'function_route_victor': '/unchanged', 'function_route_sophia': '/run'}
        env = {'SOPHIA_GOVERNED_BASE_URL': 'https://prod-a-xoc-sophia.azurewebsites.net/',
               'SOPHIA_TOOL_GATEWAY_ENABLED': 'true', 'SOPHIA_THREAD_CAPABILITIES_ENABLED': 'true'}
        with patch.dict(os.environ, env), patch.object(chat, '_resolve_existing_agent_routes', return_value=dict(old)):
            result = chat._resolve_agent_routes(None, 7)
        self.assertEqual(result, {**old, 'function_base_url': env['SOPHIA_GOVERNED_BASE_URL'].rstrip('/')})

    def test_bad_destination_and_non_governed_fail_closed(self):
        for value in ('http://example.com', 'https://user:pass@example.com', 'https://example.com/path',
                      'https://example.com?a=b', 'https://example.com:bad'):
            with self.subTest(value=value), patch.dict(os.environ, {'SOPHIA_GOVERNED_BASE_URL': value}), \
                 patch.object(chat, '_resolve_existing_agent_routes', return_value={}):
                with self.assertRaises(chat.AppError):
                    chat._resolve_agent_routes(None, 7)

    def test_disabled_and_unavailable_never_fall_back(self):
        context = ToolContext(7, 7, 32, 'ADMIN', False, 'ac2r-test', 'sophia_chat')
        request = ToolRequest('xoc.tickets.list', {}, context.request_id)
        for mode in ('disabled', 'agentcore', 'invalid'):
            with self.subTest(mode=mode), patch.dict(os.environ, {'SOPHIA_TOOL_EXECUTION': mode}), \
                 patch('src.shared.sophia_agentcore_client.settings', side_effect=ValueError('unavailable')), \
                 patch('src.tool_gateway.executor.ToolExecutor.execute') as local:
                result = selected_execution().execute(context, request, deadline=time.monotonic()+10)
                self.assertEqual(result.status.value, 'denied')
                local.assert_not_called()
