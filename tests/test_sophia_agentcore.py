"""AC-1 contracts, real policy/executor/readers, mocked KMS/HTTP/DynamoDB only."""
from dataclasses import replace
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from botocore.credentials import Credentials
from src.shared import agentcore_context as trusted
from src.shared import sophia_agentcore_client as client
from src.shared import sophia_tool_chat as chat
from src.shared.sophia_tool_execution import selected_execution, local_execution, requires_governed_execution
from src.handlers import agentcore_target as target
from src.tool_gateway.agentcore_providers import ReadOnlyProviders
from src.tool_gateway.schemas import ToolContext, ToolRequest, ToolDecision

ARN = 'arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/xoc-api-agentcore-dev-abcdefghij'
KEY = 'arn:aws:kms:us-east-1:111122223333:key/11111111-1111-1111-1111-111111111111'
URL = 'https://xoc-api-agentcore-dev-abcdefghij.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp'
ENV = {'SOPHIA_AGENTCORE_GATEWAY_ARN': ARN, 'SOPHIA_AGENTCORE_GATEWAY_URL': URL,
       'SOPHIA_AGENTCORE_CONTEXT_KEY_ARN': KEY, 'SOPHIA_TOOL_EXECUTION': 'agentcore',
       'TICKETS_TABLE_NAME': 'offline-fixture-tickets'}
CTX = ToolContext(7, 7, 32, 'ADMIN', False, 'ac1-request', 'sophia_chat')
REQ = ToolRequest('xoc.tickets.list', {}, CTX.request_id)


class FakeKMS:
    """Fictional test key, never used in deployment or exported."""
    def generate_mac(self, *, KeyId, Message, MacAlgorithm):
        assert KeyId == KEY and MacAlgorithm == 'HMAC_SHA_256'
        return {'Mac': hmac.digest(b'ac1-offline-fixture-only', Message, 'sha256')}

    def verify_mac(self, *, Mac, **kwargs):
        return {'MacValid': hmac.compare_digest(Mac, self.generate_mac(**kwargs)['Mac'])}


def assertion(context=CTX, request=REQ):
    return trusted.issue(FakeKMS(), KEY, ARN, context, request, time.monotonic()+4)


def metadata(tool=REQ.tool_name):
    return SimpleNamespace(client_context=SimpleNamespace(custom={
        'bedrockAgentCoreGatewayId': ARN.rsplit('/', 1)[1], 'bedrockAgentCoreMessageVersion': '1.0',
        'bedrockAgentCoreToolName': 'xoc-read___'+trusted.READ_TOOLS[tool]}),
        get_remaining_time_in_millis=lambda: 5000)


class ContextTests(unittest.TestCase):
    def test_roundtrip_exact_authority(self):
        context, request, end = trusted.verify(FakeKMS(), KEY, ARN, assertion(), REQ.tool_name, {})
        self.assertEqual((CTX, REQ), (context, request))
        self.assertLessEqual(end-time.time()*1000, 5000)

    def test_delegation_and_roles(self):
        trusted.validate_context(replace(CTX, role='ADMIN_XOC', tenant_id=None, delegation_active=True))
        trusted.validate_context(replace(CTX, role='USER'))
        for change in ({'role': 'SUPERADMIN'}, {'role': 'ADMIN_XOC'}, {'tenant_id': 8},
                       {'user_id': True}, {'effective_tenant_id': 0}, {'delegation_active': 1},
                       {'delegation_active': True}, {'source': 'voice'}, {'request_id': 'bad\nlog'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                trusted.validate_context(replace(CTX, **change))

    def test_expiry_future_and_excessive_lifetime(self):
        for start, end in ((0, 1000), (2001, 2100), (1999, 8000)):
            with self.assertRaises(ValueError):
                trusted.check_time({'iat_ms': start, 'deadline_ms': end}, 2000)

    def test_tamper_and_binding(self):
        token = assertion()
        raw, mac = token.split('.')
        claims = json.loads(trusted._decode(raw))
        claims['context']['effective_tenant_id'] = 8
        corrupted = trusted._encode(trusted.canonical(claims))+'.'+mac
        for audience, tok, tool, args in ((ARN, corrupted, REQ.tool_name, {}),
                (ARN+'other', token, REQ.tool_name, {}), (ARN, token, 'xoc.documents.list', {}),
                (ARN, token, REQ.tool_name, {'limit': 1}), (ARN, 'x.y', REQ.tool_name, {})):
            with self.assertRaises((ValueError, KeyError)):
                trusted.verify(FakeKMS(), KEY, audience, tok, tool, args)

    def test_signature_cannot_extend_deadline(self):
        slow = MagicMock(wraps=FakeKMS())
        def sign(**kwargs):
            result = FakeKMS().generate_mac(**kwargs)
            with patch.object(trusted.time, 'time', return_value=time.time()+10):
                trusted.check_time(json.loads(kwargs['Message']))
            return result
        slow.generate_mac.side_effect = sign
        with self.assertRaises(ValueError):
            trusted.issue(slow, KEY, ARN, CTX, REQ, time.monotonic()+2)


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV); self.env.start(); self.addCleanup(self.env.stop)
        self.kms = patch.object(target.boto3, 'client', return_value=FakeKMS())
        self.kms.start(); self.addCleanup(self.kms.stop)

    def invoke(self, context=CTX, request=REQ):
        return target.handler({'assertion': assertion(context, request), 'arguments': request.arguments}, metadata(request.tool_name))

    def test_policy_executor_once_and_sanitized(self):
        read = MagicMock(return_value={'tickets': [{'ticket_id': 'a', 'tenant_id': 7, 'subject': 'Hi', 'pushToken': 'SECRET'}]})
        with patch.object(ReadOnlyProviders, 'handlers', return_value={'tickets.list': read}):
            output = self.invoke()
        read.assert_called_once_with(CTX, {})
        self.assertEqual(output['status'], 'allowed')
        self.assertNotIn('SECRET', json.dumps(output))

    def test_ownership_injection_never_reads(self):
        for field in ('tenantId', 'tenant_id', 'userId', 'user_id', 'role', 'effective_tenant_id'):
            req = replace(REQ, arguments={field: 9})
            with patch.object(ReadOnlyProviders, 'tickets_list') as read:
                self.assertEqual('denied', self.invoke(request=req)['status'])
                read.assert_not_called()

    def test_untrusted_envelopes_never_execute(self):
        event = {'assertion': assertion(), 'arguments': {}}
        cases = [(dict(event, tenant_id=9), metadata()), ({**event, 'assertion': 'bad'}, metadata()),
                 (event, SimpleNamespace()), (event, metadata())]
        cases[-1][1].client_context.custom['bedrockAgentCoreGatewayId'] = 'other'
        for payload, ctx in cases:
            with patch.object(target, 'ToolExecutor') as executor:
                self.assertEqual({'status': 'denied', 'code': 'unavailable'}, target.handler(payload, ctx))
                executor.assert_not_called()

    def test_disabled_and_unknown_wire_never_execute(self):
        for wire in ('xoc-read___dashboard-summary', 'other___tickets-list', 'xoc-read___tickets-create'):
            ctx = metadata(); ctx.client_context.custom['bedrockAgentCoreToolName'] = wire
            with patch.object(target, 'ToolExecutor') as executor:
                target.handler({'assertion': assertion(), 'arguments': {}}, ctx)
                executor.assert_not_called()

    def test_cross_tenant_output_is_dropped(self):
        with patch.object(ReadOnlyProviders, 'tickets_list', return_value={'tickets': [{'tenant_id': 8}]}):
            result = self.invoke()
        self.assertEqual(('denied', None), (result['status'], result['data']))

    def test_provider_error_never_leaks(self):
        with patch.object(ReadOnlyProviders, 'tickets_list', side_effect=RuntimeError('SECRET SQL JWT')):
            output = self.invoke()
        self.assertEqual('denied', output['status'])
        self.assertNotIn('SECRET', json.dumps(output))

    def test_two_tenants_and_delegation_do_not_share_identity(self):
        contexts = [CTX, replace(CTX, tenant_id=8, effective_tenant_id=8, user_id=33),
                    replace(CTX, role='ADMIN_XOC', tenant_id=None, effective_tenant_id=8, delegation_active=True)]
        seen = []
        def read(context, args):
            seen.append(context)
            return {'tickets': [{'tenant_id': context.effective_tenant_id}]}
        with patch.object(ReadOnlyProviders, 'handlers', return_value={'tickets.list': read}):
            for context in contexts:
                self.assertEqual('allowed', self.invoke(context)['status'])
        self.assertEqual(contexts, seen)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TICKETS_TABLE_NAME': 'offline-fixture-tickets'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.p = ReadOnlyProviders(int(time.time()*1000)+4500)
        self.table = MagicMock()
        self.patch = patch.object(self.p, 'table', return_value=self.table)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def test_get_uses_tenant_key_not_global_lookup(self):
        self.table.get_item.return_value = {'Item': {'pk': 'TICKET#7', 'ticket_id': 't1', 'tenant_id': 7}}
        self.p.tickets_get(CTX, {'ticket_id': 't1'})
        self.table.get_item.assert_called_once_with(Key={'pk': 'TICKET#7', 'sk': 'TICKET#t1'})
        self.table.query.assert_not_called()
        self.table.get_item.return_value = {'Item': {'pk': 'TICKET#8'}}
        with self.assertRaises(ValueError): self.p.tickets_get(CTX, {'ticket_id': 't1'})

    def test_list_keys_and_sort(self):
        self.table.query.return_value = {'Items': [{'tenant_id': 7, 'ticket_id': 'a', 'created_at': '2026-01-01T00:00:00+00:00'}]}
        self.assertEqual(1, self.p.tickets_list(CTX, {})['count'])
        expr = self.table.query.call_args.kwargs['KeyConditionExpression'].get_expression()
        self.assertEqual('TICKET#7', expr['values'][1])
        self.p.tickets_list(CTX, {'status': 'PENDING'})
        self.assertEqual('StatusIndex', self.table.query.call_args.kwargs['IndexName'])

    def test_documents_real_query_and_no_file_fallback(self):
        self.table.query.return_value = {'Items': [{'tenant_id': 7, 'document_id': 'doc'}]}
        self.assertEqual(1, self.p.documents_list(CTX, {})['count'])
        self.p.documents_list(CTX, {'status': 'COMPLETED'})
        self.assertEqual('StatusIndex', self.table.query.call_args.kwargs['IndexName'])
        p = ReadOnlyProviders(int(time.time()*1000)+4000)
        with patch.dict(os.environ, {'REPORTS_STORE_MODE': 'local', 'REPORTS_TABLE_NAME': 'fake'}), self.assertRaises(ValueError):
            p.table('REPORTS_TABLE_NAME')

    def test_unread_scopes_both_user_and_tenant_and_paginates(self):
        self.table.query.side_effect = [{'Count': 2, 'LastEvaluatedKey': {'PK': 'next'}}, {'Count': 3}]
        self.assertEqual({'count': 5}, self.p.unread_count(CTX, {}))
        calls = self.table.query.call_args_list
        expr = calls[0].kwargs['KeyConditionExpression'].get_expression()
        self.assertEqual('TENANT#7#USER#32#STATUS#UNREAD', expr['values'][1])
        self.assertEqual({'PK': 'next'}, calls[1].kwargs['ExclusiveStartKey'])

    def test_pagination_cap_fails_not_partial(self):
        self.table.query.return_value = {'Count': 1, 'LastEvaluatedKey': {'PK': 'repeat'}}
        with self.assertRaises(TimeoutError): self.p.unread_count(CTX, {})
        self.assertEqual(20, self.table.query.call_count)

    def test_deadline_before_io(self):
        self.p.deadline_ms = 0
        with self.assertRaises(TimeoutError): list(self.p.pages(self.table))
        self.table.query.assert_not_called()


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV); self.env.start(); self.addCleanup(self.env.stop)

    def execute(self, context=CTX, request=REQ, deadline=None):
        return client.AgentCoreExecution().execute(context, request, deadline=deadline or time.monotonic()+10)

    def test_no_iam_calls_for_denied_tools_and_roles(self):
        for ctx, req in ((CTX, replace(REQ, tool_name='xoc.evil.execute')),
                (CTX, replace(REQ, tool_name='xoc.dashboard.summary')), (replace(CTX, role='SUPERADMIN'), REQ),
                (replace(CTX, role='ADMIN_XOC'), REQ), (CTX, replace(REQ, arguments={'tenantId': 8}))):
            with patch.object(client.boto3, 'Session') as factory:
                self.assertEqual(ToolDecision.DENIED, self.execute(ctx, req).status)
                factory.assert_not_called()

    def test_bad_endpoint_key_and_budget_fail_closed(self):
        for change in ({'SOPHIA_AGENTCORE_GATEWAY_URL': 'https://evil.invalid/mcp'},
                       {'SOPHIA_AGENTCORE_GATEWAY_URL': URL+'?x=1'}, {'SOPHIA_AGENTCORE_CONTEXT_KEY_ARN': 'alias/jwt'}):
            with patch.dict(os.environ, change), patch.object(client.requests, 'Session') as http:
                self.assertEqual(ToolDecision.DENIED, self.execute().status); http.assert_not_called()
        with patch.object(client.boto3, 'Session') as factory:
            for end in (time.monotonic()-1, float('nan'), float('inf')):
                self.assertEqual(ToolDecision.DENIED, self.execute(deadline=end).status)
            factory.assert_not_called()

    def mock_transport(self, response_status=200, content_type='application/json', error=None):
        aws = MagicMock(); aws.client.return_value = FakeKMS()
        aws.get_credentials.return_value = Credentials('FICTIONAL', 'FICTIONAL')
        http = MagicMock(); http.__enter__.return_value = http
        response = MagicMock(); response.__enter__.return_value = response
        response.status_code = response_status; response.headers = {'Content-Type': content_type}
        output = {'request_id': CTX.request_id, 'tool_name': REQ.tool_name, 'status': 'allowed', 'code': 'allowed',
                  'data': {'tickets': []}, 'audit_id': 'a'*24}
        wire = {'jsonrpc': '2.0', 'id': CTX.request_id, 'result': {'content': [{'type': 'text', 'text': json.dumps(output)}]}}
        response.iter_content.return_value = [json.dumps(wire).encode()]
        http.post.return_value = response
        if error: http.post.side_effect = error
        return aws, http, response

    def test_one_signed_call_no_user_jwt_and_no_redirect(self):
        aws, http, _ = self.mock_transport()
        with patch.object(client.boto3, 'Session', return_value=aws), patch.object(client.requests, 'Session', return_value=http):
            self.assertEqual(ToolDecision.ALLOWED, self.execute().status)
        self.assertEqual(1, http.post.call_count)
        kwargs = http.post.call_args.kwargs
        self.assertFalse(kwargs['allow_redirects']); self.assertFalse(http.trust_env)
        self.assertIn('AWS4-HMAC-SHA256', kwargs['headers']['Authorization'])
        self.assertNotIn('Bearer', json.dumps(kwargs['headers']))
        payload = json.loads(kwargs['data'])
        self.assertEqual('xoc-read___tickets-list', payload['params']['name'])
        self.assertEqual({'assertion', 'arguments'}, set(payload['params']['arguments']))
        self.assertEqual(client.PROTOCOL, kwargs['headers']['MCP-Protocol-Version'])
        self.assertEqual(client.PROTOCOL, payload['params']['_meta']['io.modelcontextprotocol/protocolVersion'])
        self.assertEqual({}, payload['params']['_meta']['io.modelcontextprotocol/clientCapabilities'])
        self.assertEqual(payload['params']['name'], kwargs['headers']['Mcp-Name'])

    def test_chat_to_client_target_real_executor_and_second_turn(self):
        aws, http, response = self.mock_transport()
        table = MagicMock()
        table.query.return_value = {'Items': [{'pk': 'TICKET#7', 'tenant_id': 7,
            'ticket_id': 'real-boundary-test', 'created_at': '2026-01-01T00:00:00+00:00', 'subject': 'VPN'}]}
        def post(url, **kwargs):
            payload = json.loads(kwargs['data'])
            output = target.handler(payload['params']['arguments'], metadata())
            response.iter_content.return_value = [json.dumps({'jsonrpc': '2.0', 'id': payload['id'],
                'result': {'content': [{'type': 'text', 'text': json.dumps(output)}]}}).encode()]
            return response
        http.post.side_effect = post
        second = MagicMock(return_value={'protocol': chat.PROTOCOL, 'request_id': CTX.request_id,
                                         'thread_id': 'owned-thread', 'text': 'Tienes un ticket VPN.'})
        with patch.object(client.boto3, 'Session', return_value=aws), \
             patch.object(target.boto3, 'client', return_value=FakeKMS()), \
             patch.object(client.requests, 'Session', return_value=http), \
             patch.object(ReadOnlyProviders, 'table', return_value=table), \
             patch.object(chat, 'ToolExecutor') as direct:
            output = chat.finish_turn({'protocol': chat.PROTOCOL, 'request_id': CTX.request_id,
                'thread_id': 'owned-thread', 'continuation': 'opaque',
                'tool_request': {'tool_name': REQ.tool_name, 'arguments': {}}}, context=CTX,
                cleaner=lambda p: p, validate_thread=lambda _: None, post_result=second,
                deadline=time.monotonic()+15)
        self.assertEqual('Tienes un ticket VPN.', output['text'])
        self.assertEqual('allowed', output['metadata']['toolGateway']['status'])
        self.assertEqual(1, http.post.call_count); self.assertEqual(1, table.query.call_count)
        second.assert_called_once(); direct.assert_not_called()
        model_body = second.call_args.args[0]
        self.assertEqual('real-boundary-test', model_body['tool_result']['data']['tickets'][0]['ticket_id'])
        self.assertNotIn('assertion', json.dumps(model_body))
        self.assertNotIn('Authorization', json.dumps(model_body))

    def test_failures_never_retried(self):
        for status, content, error in ((403, 'application/json', None), (500, 'application/json', None),
                (302, 'application/json', None), (200, 'text/event-stream', None),
                (200, 'application/json', TimeoutError('SECRET'))):
            aws, http, _ = self.mock_transport(status, content, error)
            with patch.object(client.boto3, 'Session', return_value=aws), patch.object(client.requests, 'Session', return_value=http):
                self.assertEqual(ToolDecision.DENIED, self.execute().status)
            self.assertEqual(1, http.post.call_count)

    def test_oversize_and_mismatched_response(self):
        for body in (b'x'*131073, b'{}', b'{"jsonrpc":"2.0","id":"wrong","result":{}}'):
            aws, http, response = self.mock_transport(); response.iter_content.return_value = [body]
            with patch.object(client.boto3, 'Session', return_value=aws), patch.object(client.requests, 'Session', return_value=http):
                self.assertEqual(ToolDecision.DENIED, self.execute().status)

    def test_no_sampling_elicitation_or_multi_round_trip(self):
        for result in ({'resultType': 'input_required'}, {'requestState': 'opaque'},
                       {'inputRequests': {'1': {'method': 'sampling/createMessage'}}}):
            with self.assertRaises(ValueError):
                client.decode_result({'jsonrpc': '2.0', 'id': CTX.request_id, 'result': result}, CTX, REQ)

    def test_agentcore_precedes_local_and_invalid_mode_never_direct(self):
        with local_execution(object()): self.assertIsInstance(selected_execution(), client.AgentCoreExecution)
        for mode in ('disabled', 'typo', ''):
            with patch.dict(os.environ, {'SOPHIA_TOOL_EXECUTION': mode}):
                self.assertTrue(requires_governed_execution())
                self.assertEqual(ToolDecision.DENIED, selected_execution().execute(CTX, REQ, deadline=None).status)

    def test_chat_failure_no_direct_executor_second_tool_not_run(self):
        second = MagicMock(return_value={'protocol': chat.PROTOCOL, 'request_id': CTX.request_id,
                         'thread_id': 'thread', 'tool_request': {'tool_name': 'xoc.tickets.get'}})
        with patch.object(client, 'settings', side_effect=ValueError('SECRET')), patch.object(chat, 'ToolExecutor') as direct:
            output = chat.finish_turn({'protocol': chat.PROTOCOL, 'request_id': CTX.request_id, 'thread_id': 'thread',
                'continuation': 'opaque', 'tool_request': {'tool_name': REQ.tool_name}}, context=CTX,
                cleaner=lambda p: p, validate_thread=lambda _: None, post_result=second, deadline=time.monotonic()+10)
            direct.assert_not_called()
        self.assertEqual(1, second.call_count)
        self.assertEqual('tool_call_limit', output['metadata']['toolGateway']['completionCode'])
        self.assertNotIn('SECRET', json.dumps(output))


if __name__ == '__main__': unittest.main()
