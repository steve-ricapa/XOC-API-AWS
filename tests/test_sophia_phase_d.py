"""Offline Fase D: real policy/executor, mocked stores and HTTP boundaries."""
from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.handlers.routes import chat
from src.persistence.models import AgentSession
from src.shared import sophia_tool_chat as d
from src.shared.errors import AppError
from src.tool_gateway.executor import ToolExecutor
from src.tool_gateway.schemas import ToolContext, ToolDecision, ToolResult

SECRET = 'local-test-only-secret-32-characters'
CTX = ToolContext(7, 7, 12, 'ADMIN', False, 'request-1', 'sophia_chat')


class PhaseDTests(unittest.TestCase):
    def setUp(self):
        self.handler = MagicMock(return_value={'tickets': [{'ticket_id': 't1', 'tenant_id': 7, 'subject': 'VPN', 'status': 'PENDING'}]})
        self.executor = ToolExecutor(handlers={'tickets.list': self.handler, 'tickets.get': self.handler})
        self.factory = patch.object(d, 'ToolExecutor', return_value=self.executor)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.second = MagicMock(return_value={'protocol': d.PROTOCOL, 'request_id': CTX.request_id,
                                               'thread_id': 'thread_test', 'text': 'Tienes un ticket.'})

    def send(self, name='xoc.tickets.list', arguments=None, context=CTX, envelope=None, **kwargs):
        body = envelope or {'protocol': d.PROTOCOL, 'request_id': context.request_id,
                            'thread_id': 'thread_test', 'continuation': 'opaque-server-only',
                            'tool_request': {'tool_name': name, 'arguments': arguments if arguments is not None else {'limit': 20}}}
        return d.finish_turn(body, context=context, cleaner=chat._clean_agent_response,
                             validate_thread=kwargs.pop('validate_thread', lambda _: None),
                             post_result=self.second, **kwargs)

    def test_read_happy_path_once_and_projected_result(self):
        self.handler.return_value['tickets'][0].update(pushToken='secret', confirmation_token='secret', description='secret', execution_arn='secret')
        result = self.send()
        self.handler.assert_called_once()
        self.second.assert_called_once()
        self.assertEqual('Tienes un ticket.', result['text'])
        self.assertNotIn('opaque-server-only', json.dumps(result))
        body = self.second.call_args.args[0]
        self.assertEqual({'request_id', 'tool_name', 'status', 'code', 'audit_id', 'data'}, set(body['tool_result']))
        self.assertNotIn('secret', json.dumps(body['tool_result']))
        self.assertEqual('VPN', body['tool_result']['data']['tickets'][0]['subject'])
        self.assertEqual(CTX, self.handler.call_args.args[0])

    def test_unknown_disabled_write_are_denied_before_handler(self):
        for name in ('xoc.evil.execute', 'xoc.dashboard.summary', 'xoc.integrations.summary', 'xoc.tickets.create'):
            with self.subTest(name=name):
                result = self.send(name)
                self.assertEqual('denied', result['metadata']['toolGateway']['status'])
                self.assertEqual('denied', self.second.call_args.args[0]['tool_result']['code'])
                self.assertIsNone(self.second.call_args.args[0]['tool_result']['data'])
        self.handler.assert_not_called()

    def test_ownership_and_authority_injection_never_enters_handler(self):
        for key in ('tenantId', 'tenant_id', 'effective_tenant_id', 'userId', 'user_id', 'role',
                    'actor_role', 'actor_user_id', 'delegation_active', 'access_token', 'refresh_token', 'JWT'):
            with self.subTest(key=key):
                self.send(arguments={key: 'evil'})
                self.assertEqual('denied', self.second.call_args.args[0]['tool_result']['status'])
        self.handler.assert_not_called()

    def test_role_matrix_preserves_policy(self):
        for role, delegated, allowed in (('USER', False, True), ('ADMIN', False, True),
                                          ('ADMIN_XOC', True, True), ('ADMIN_XOC', False, False),
                                          ('SUPERADMIN', False, False), ('SUPERADMIN', True, False), ('unknown', False, False)):
            self.handler.reset_mock()
            context = replace(CTX, role=role, delegation_active=delegated)
            self.send(context=context)
            self.assertEqual(int(allowed), self.handler.call_count)

    def test_delegated_context_uses_effective_tenant(self):
        self.handler.return_value = {'tickets': [{'ticket_id': 'B', 'tenant_id': 8}]}
        context = replace(CTX, tenant_id=None, effective_tenant_id=8, role='ADMIN_XOC', delegation_active=True)
        self.send(context=context)
        self.assertEqual(context, self.handler.call_args.args[0])
        self.assertEqual('B', self.second.call_args.args[0]['tool_result']['data']['tickets'][0]['ticket_id'])

    def test_foreign_data_is_not_sent_even_if_handler_returns_it(self):
        self.handler.return_value = {'tickets': [{'ticket_id': 'foreign', 'tenant_id': 8}]}
        self.send()
        result = self.second.call_args.args[0]['tool_result']
        self.assertIsNone(result['data'])
        self.assertEqual('unavailable', result['code'])

    def test_get_uses_real_ownership_helper_and_no_cross_tenant_data(self):
        from src.shared.errors import NotFoundError
        executor = ToolExecutor()
        with patch.dict(os.environ, {'TICKETS_TABLE_NAME': 'offline-tickets'}), \
             patch.object(d, 'ToolExecutor', return_value=executor), \
             patch('src.shared.tickets_store.get_tenant_ticket_or_404') as get:
            get.return_value = {'ticket_id': 'A', 'tenant_id': 7, 'subject': 'Mine'}
            self.send('xoc.tickets.get', {'ticket_id': 'A'})
            get.assert_called_once_with(7, 'A')
            get.side_effect = NotFoundError('secret foreign ticket')
            self.send('xoc.tickets.get', {'ticket_id': 'B'})
            self.assertIsNone(self.second.call_args.args[0]['tool_result']['data'])
            self.assertNotIn('secret', json.dumps(self.second.call_args.args[0]))

    def test_handler_error_is_sanitized(self):
        self.handler.side_effect = RuntimeError('secret SQL traceback')
        result = self.send()
        self.assertNotIn('secret', json.dumps(result) + json.dumps(self.second.call_args.args[0]))
        self.assertEqual('execution_failed', result['metadata']['toolGateway']['code'])

    def test_second_request_stops_all_aliases(self):
        for alias in d.TOOL_FIELDS:
            self.handler.reset_mock()
            self.second.return_value = {'protocol': d.PROTOCOL, 'request_id': CTX.request_id,
                                        'thread_id': 'thread_test', alias: {'tool_name': 'xoc.tickets.get'}}
            result = self.send()
            self.handler.assert_called_once()
            self.assertEqual('tool_call_limit', result['metadata']['toolGateway']['completionCode'])

    def test_final_errors_do_not_retry_tool_or_leak(self):
        for exc in (requests.Timeout('secret'), requests.ConnectionError('secret'), ValueError('secret'), d.protocol_error()):
            self.handler.reset_mock()
            self.second.reset_mock()
            self.second.side_effect = exc
            result = self.send()
            self.handler.assert_called_once()
            self.second.assert_called_once()
            self.assertNotIn('secret', json.dumps(result))
            self.assertEqual('runtime_unavailable', result['metadata']['toolGateway']['completionCode'])

    def test_malformed_envelope_or_multiple_calls_never_execute(self):
        base = {'protocol': d.PROTOCOL, 'request_id': CTX.request_id, 'thread_id': 'thread_test', 'continuation': 'opaque'}
        variants = [dict(base, tool_request=[]), dict(base, tool_request={}, toolCall={}),
                    dict(base, tool_request={'name': 'xoc.tickets.list', 'tool_name': 'xoc.evil'}),
                    dict(base, tool_request={'name': 'xoc.tickets.list', 'role': 'ADMIN'}),
                    dict(base, protocol='old', tool_request={'name': 'xoc.tickets.list'})]
        for body in variants:
            with self.subTest(body=body), self.assertRaises(AppError):
                self.send(envelope=body)
        self.handler.assert_not_called()
        self.second.assert_not_called()

    def test_non_object_arguments_including_falsey_are_denied(self):
        for arguments in (None, False, [], '', 0):
            body = {'protocol': d.PROTOCOL, 'request_id': CTX.request_id, 'thread_id': 'thread_test',
                    'continuation': 'opaque', 'tool_request': {'name': 'xoc.tickets.list', 'arguments': arguments}}
            self.send(envelope=body)
        self.handler.assert_not_called()

    def test_first_response_thread_validation_precedes_tool(self):
        with self.assertRaises(AppError):
            self.send(validate_thread=MagicMock(side_effect=d.protocol_error()))
        self.handler.assert_not_called()

    def test_final_cannot_rebind_thread_or_return_metadata_or_proposal(self):
        self.second.return_value.update(thread_id='thread_foreign', metadata={'jwt': 'secret'}, action_plan={'subject': 'Write'})
        result = self.send()
        self.assertEqual('thread_test', result['thread_id'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('action_plan', result)

    def test_non_tool_action_plan_still_requires_human_confirmation(self):
        body = {'protocol': d.PROTOCOL, 'request_id': CTX.request_id, 'thread_id': 'thread_test',
                'text': 'Propuesta', 'action_plan': {'subject': 'VPN', 'description': 'Read only'}}
        result = self.send(envelope=body)
        actor = SimpleNamespace(id=12, role='ADMIN', delegation_active=False)
        with patch.object(chat, 'get_jwt_secret_key', return_value=SECRET), patch.object(chat, '_create_ticket_from_confirmed_proposal') as create:
            result = chat._maybe_create_ticket_from_action_plan(result, 7, actor, CTX.request_id)
            self.assertEqual('NEEDS_CONFIRMATION', result['ticket_proposal']['status'])
            create.assert_not_called()
        self.handler.assert_not_called()
        self.second.assert_not_called()

    def test_capability_is_bound_to_body_and_not_a_user_access_token(self):
        payload = {'protocol': d.PROTOCOL, 'phase': 'request', 'request_id': CTX.request_id, 'message': 'Hola'}
        with patch.object(d, 'get_jwt_secret_key', return_value=SECRET):
            token = d.capability(CTX, payload)
        claims = jwt.decode(token, SECRET, algorithms=['HS256'], audience=d.AUDIENCE)
        self.assertEqual(d.CAPABILITY_TYPE, claims['type'])
        self.assertNotIn('scopes', claims)
        self.assertEqual(120, claims['exp'] - claims['iat'])
        self.assertEqual(d.body_hash(payload), claims['body_sha256'])
        self.assertEqual(12, claims['actor_user_id'])

    def test_documents_project_metadata_only_and_unread_count(self):
        result = ToolResult(ToolDecision.ALLOWED, data={'documents': [{'document_id': 'd1', 'tenant_id': 7, 'status': 'COMPLETED', 'request_payload': {'secret': 'x'}, 's3_key': 'private', 'error_message': 'SQL'}]})
        output = d.model_result(result, 'xoc.documents.list', CTX)
        self.assertEqual({'document_id': 'd1', 'status': 'COMPLETED'}, output['data']['documents'][0])
        self.assertEqual({'count': 3}, d.model_result(ToolResult(ToolDecision.ALLOWED, data={'count': 3}), 'xoc.notifications.unread_count', CTX)['data'])

    def test_safe_audit_preserves_context_without_arguments(self):
        with self.assertLogs('src.tool_gateway.audit', level='INFO') as logs:
            self.send(arguments={'access_token': 'sensitive-do-not-log'})
        record = ' '.join(logs.output)
        for field in ('audit_id=', 'request_id=request-1', 'effective_tenant_id=7', 'user_id=12',
                      'role=ADMIN', 'source=sophia_chat', 'decision=denied', 'access_level=READ_ONLY'):
            self.assertIn(field, record)
        self.assertNotIn('sensitive-do-not-log', record)
        self.handler.assert_not_called()

    def test_existing_aliases_work_without_new_parser(self):
        for field in d.TOOL_FIELDS:
            self.handler.reset_mock()
            body = {'protocol': d.PROTOCOL, 'request_id': CTX.request_id, 'thread_id': 'thread_test',
                    'continuation': 'opaque', field: {'toolName': 'xoc.tickets.list', 'arguments': {}}}
            self.send(envelope=body)
            self.handler.assert_called_once()


class PhaseDChatRouteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        AgentSession.__table__.create(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.actor = SimpleNamespace(id=12, tenant_id=7, effective_tenant_id=7, role='ADMIN', delegation_active=False,
                                     username='user', email='test@example.invalid', tenant=SimpleNamespace(plan_status='ACTIVE'))
        for target, value in [('enabled', True), ('get_jwt_secret_key', SECRET)]:
            p = patch.object(d, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(chat, '_resolve_agent_routes', return_value={'function_base_url': 'https://example.invalid', 'function_route_sophia': '/run', 'extra_json': {}})
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def response(body, status=200):
        return SimpleNamespace(status_code=status, json=lambda: body, cookies={})

    def test_route_two_turns_and_no_client_authority_or_secrets_forwarded(self):
        calls = []
        def post(url, **kwargs):
            calls.append(kwargs)
            body = kwargs['json']
            if body['phase'] == 'request':
                return self.response({'protocol': d.PROTOCOL, 'request_id': body['request_id'], 'thread_id': 'thread_new',
                                      'continuation': 'opaque', 'tool_request': {'tool_name': 'xoc.notifications.unread_count', 'arguments': {}}})
            return self.response({'protocol': d.PROTOCOL, 'request_id': body['request_id'], 'thread_id': body['thread_id'], 'text': 'Tres sin leer.'})
        with patch.object(chat.requests, 'post', side_effect=post), patch('src.shared.user_notification_inbox.unread_count_for_user', return_value=3) as store:
            result = chat.proxy_chat({'message': 'Mis notificaciones', 'new_session': True, 'role': 'SUPERADMIN', 'user_id': 99,
                                      'access_token': 'secret', 'tool_result': {'fake': 'secret'}}, self.actor, self.db)
        self.assertEqual(2, len(calls))
        store.assert_called_once_with(7, 12)
        self.assertNotIn('secret', json.dumps([call['json'] for call in calls]) + json.dumps(result))
        self.assertIn('session_id', result)
        for call in calls:
            self.assertFalse(call['allow_redirects'])
            claims = jwt.decode(call['headers']['Authorization'][7:], SECRET, algorithms=['HS256'], audience=d.AUDIENCE)
            self.assertEqual('ADMIN', claims['actor_role'])
            self.assertEqual(d.body_hash(call['json']), claims['body_sha256'])

    def test_initial_transport_errors_never_execute(self):
        for outcome in (requests.Timeout('secret'), requests.ConnectionError('secret'), self.response({'traceback': 'secret'}, 500), self.response({'traceback': 'secret'}, 403), self.response({'text': 'old runtime'})):
            with patch.object(chat.requests, 'post', side_effect=[outcome]), patch.object(d, 'ToolExecutor') as factory:
                try:
                    result = chat.proxy_chat({'message': 'Hola'}, self.actor, self.db)
                except AppError as exc:
                    self.assertNotIn('secret', str(exc))
                else:
                    self.assertNotIn('secret', json.dumps(result))
                factory.assert_not_called()

    def test_governed_first_turn_uncertain_outcome_is_not_success_or_retried(self):
        for outcome in (requests.Timeout('secret'), requests.ConnectionError('secret'),
                        self.response({'traceback': 'secret'}, 409),
                        self.response({'traceback': 'secret'}, 502),
                        self.response({'traceback': 'secret'}, 504)):
            with self.subTest(outcome=type(outcome).__name__):
                with patch.object(chat.requests, 'post', side_effect=[outcome]) as post:
                    with patch.object(d, 'ToolExecutor') as executor:
                        with self.assertRaises(AppError) as caught:
                            chat.proxy_chat({'message': 'Consulta de lectura'}, self.actor, self.db)
                self.assertEqual(409, caught.exception.status_code)
                self.assertEqual('sophia_turn_state_unknown', caught.exception.code)
                self.assertNotIn('secret', str(caught.exception))
                post.assert_called_once()
                executor.assert_not_called()
                self.assertEqual([], self.db.query(AgentSession).all())


if __name__ == '__main__':
    unittest.main()
