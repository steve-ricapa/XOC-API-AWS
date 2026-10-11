"""D.1 partial: existing AgentSession on ephemeral SQLite; HTTP/key boundaries mocked."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.handlers.routes import chat
from src.persistence.models import AgentSession
from src.shared import sophia_thread_operations as t
from src.shared.errors import AppError, ForbiddenError, ValidationError

SECRET = 'offline-testing-secret-32-characters'


class ThreadOperationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite:///:memory:')
        AgentSession.__table__.create(engine)
        self.db = Session(engine)
        self.addCleanup(engine.dispose)
        self.addCleanup(self.db.close)
        self.actor = SimpleNamespace(id=12, tenant_id=7, effective_tenant_id=7, role='ADMIN',
                                     delegation_active=False, tenant=SimpleNamespace(plan_status='ACTIVE'))
        for sid, tenant, user, thread in [(1, 7, 12, 'thread_owned'), (2, 7, 13, 'thread_other_user'),
                                          (3, 8, 12, 'thread_other_tenant'), (4, 7, 12, None)]:
            self.db.add(AgentSession(id=sid, tenant_id=tenant, user_id=user, external_thread_id=thread, purpose='sophia_chat'))
        self.db.commit()
        self.start(patch.dict(os.environ, {'SOPHIA_THREAD_CAPABILITIES_ENABLED': 'true'}))
        self.key = self.start(patch.object(t, 'get_jwt_secret_key', return_value=SECRET))
        self.legacy = self.start(patch.object(chat, '_build_agent_invoke_token', return_value='legacy-test'))
        self.start(patch.object(chat, '_resolve_agent_routes', return_value={
            'function_base_url': 'https://provider.invalid', 'function_route_sophia_history': '/history',
            'function_route_sophia_delete': '/threads',
        }))
        self.get = self.start(patch.object(chat.requests, 'get', return_value=self.response({
            'thread_id': 'thread_owned', 'messages': [{'id': 'msg_1', 'role': 'assistant', 'text': 'Hola', 'created_at': 1}],
        })))
        self.delete = self.start(patch.object(chat.requests, 'delete', return_value=self.response({
            'thread_id': 'thread_owned', 'deleted': True,
        })))

    def start(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    @staticmethod
    def response(payload, status=200):
        response = MagicMock(status_code=status, text='private provider detail')
        response.json.return_value = payload
        return response

    def history(self, **kw):
        return chat.chat_history(self.actor, self.db, **kw)

    def claims(self, method):
        headers = method.call_args.kwargs['headers']
        claims = jwt.decode(headers['Authorization'].split(' ', 1)[1], SECRET, algorithms=['HS256'], audience=t.AUDIENCE)
        self.assertEqual(claims['request_id'], headers[t.REQUEST_HEADER])
        self.assertFalse(method.call_args.kwargs['allow_redirects'])
        self.assertEqual(30, method.call_args.kwargs['timeout'])
        self.assertNotIn('scopes', claims)
        self.assertEqual(t.CAPABILITY_TYPE, claims['type'])
        self.assertEqual(60, claims['exp'] - claims['iat'])
        self.assertEqual((12, 7), (claims['actor_user_id'], claims['effective_tenant_id']))
        self.legacy.assert_not_called()
        return claims

    def test_history_capability_only_after_owned_lookup_and_not_in_response(self):
        result = self.history(session_id='1')
        claims = self.claims(self.get)
        self.assertEqual('history', claims['operation'])
        self.assertEqual('sophia:thread:history', claims['scope'])
        self.assertEqual('thread_owned', claims['thread_id'])
        self.assertEqual({'thread_id', 'messages', 'count', 'session_id'}, set(result))
        self.assertEqual(1, result['count'])
        self.assertNotIn(self.get.call_args.kwargs['headers']['Authorization'][7:], str(result))

    def test_history_binds_effective_parameters(self):
        self.history(thread_id='thread_owned', limit=500, order='asc')
        claims = self.claims(self.get)
        from src.shared.sophia_tool_chat import body_hash
        self.assertEqual(body_hash({'operation': 'history', 'thread_id': 'thread_owned', 'limit': 100, 'order': 'asc'}), claims['parameters_sha256'])

    def test_other_user_tenant_and_unknown_never_get_capability_or_http(self):
        for params in ({'session_id': '2'}, {'session_id': '3'}, {'session_id': '999'},
                       {'thread_id': 'thread_other_user'}, {'thread_id': 'thread_other_tenant'},
                       {'thread_id': 'thread_unknown'}, {'session_id': '1', 'thread_id': 'thread_other_user'}):
            with self.subTest(params=params), self.assertRaises(ValidationError):
                self.history(**params)
        for sid in (2, 3, 999):
            with self.assertRaises(ValidationError):
                chat.delete_chat_session(sid, self.actor, self.db)
        self.key.assert_not_called()
        self.legacy.assert_not_called()
        self.get.assert_not_called()
        self.delete.assert_not_called()

    def test_signer_also_requires_owned_row_and_no_actor_injection(self):
        for sid in (2, 3):
            with self.assertRaises(ForbiddenError):
                t.headers_for_owned_session(self.actor, self.db.get(AgentSession, sid), 'delete')
        self.key.assert_not_called()

    def test_delegated_operator_uses_effective_tenant_only(self):
        self.actor.tenant_id = None
        self.actor.role = 'ADMIN_XOC'
        self.actor.delegation_active = True
        self.history(session_id='1')
        self.claims(self.get)
        self.key.reset_mock()
        self.actor.delegation_active = False
        with self.assertRaises(ForbiddenError):
            self.history(session_id='1')
        self.key.assert_not_called()

    def test_existing_thread_owner_roles_not_broadened(self):
        for role in ('USER', 'ADMIN', 'ADMIN_XOC', 'SUPERADMIN'):
            self.actor.role = role
            self.actor.delegation_active = role in ('ADMIN_XOC', 'SUPERADMIN')
            self.history(session_id='1')
        # History does not execute AI tools; preserve its existing role policy.
        self.actor.role = 'UNKNOWN'
        with self.assertRaises(ForbiddenError):
            self.history(session_id='1')

    def test_delete_binds_operation_and_removes_only_owned_row(self):
        result = chat.delete_chat_session(1, self.actor, self.db)
        claims = self.claims(self.delete)
        self.assertEqual('delete', claims['operation'])
        self.assertEqual('sophia:thread:delete', claims['scope'])
        self.assertEqual('thread_owned', claims['thread_id'])
        self.assertTrue(result['remote_deleted'])
        self.assertIsNone(self.db.get(AgentSession, 1))
        self.assertIsNotNone(self.db.get(AgentSession, 2))
        self.assertNotIn('capability', str(result))

    def test_empty_local_session_deletes_without_signing_or_remote_request(self):
        result = chat.delete_chat_session(4, self.actor, self.db)
        self.assertFalse(result['remote_deleted'])
        self.assertIsNone(self.db.get(AgentSession, 4))
        self.key.assert_not_called()
        self.delete.assert_not_called()
        self.legacy.assert_not_called()

    def test_delete_errors_preserve_binding_and_never_fall_back_to_legacy(self):
        for response in (self.response({}, 401), self.response({}, 403), self.response({}, 500),
                         self.response({}, 302), self.response({'thread_id': 'thread_other', 'deleted': True}),
                         self.response({'thread_id': 'thread_owned', 'deleted': False})):
            self.delete.return_value = response
            with self.assertRaises(AppError) as caught:
                chat.delete_chat_session(1, self.actor, self.db)
            self.assertNotIn('private', caught.exception.message)
            self.assertIsNotNone(self.db.get(AgentSession, 1))
        self.delete.side_effect = requests.Timeout('private token stack trace')
        with self.assertRaises(AppError) as caught:
            chat.delete_chat_session(1, self.actor, self.db)
        self.assertNotIn('private', caught.exception.message)
        self.assertIsNotNone(self.db.get(AgentSession, 1))
        self.legacy.assert_not_called()

    def test_history_errors_do_not_leak_or_fall_back(self):
        for response in (self.response({}, 401), self.response({}, 403), self.response({}, 500),
                         self.response({}, 302), self.response({'thread_id': 'thread_foreign', 'messages': []}),
                         self.response({'thread_id': 'thread_owned', 'messages': 'private'})):
            self.get.return_value = response
            with self.assertRaises(AppError) as caught:
                self.history(session_id='1')
            self.assertEqual(502, caught.exception.status_code)
            self.assertNotIn('private', caught.exception.message)
        self.get.side_effect = requests.ConnectionError('private Authorization token')
        with self.assertRaises(AppError) as caught:
            self.history(session_id='1')
        self.assertNotIn('private', caught.exception.message)
        self.legacy.assert_not_called()

    def test_only_public_history_fields_returned_and_tokens_redacted(self):
        token = jwt.encode({'test': 'private'}, SECRET, algorithm='HS256')
        self.get.return_value = self.response({'thread_id': 'thread_owned', 'capability': token, 'audit': {'secret': 'private'},
            'messages': [{'id': 'msg_1', 'role': 'assistant', 'text': 'Token ' + token, 'authorization': token}]})
        result = self.history(session_id='1')
        self.assertNotIn(token, str(result))
        self.assertNotIn('authorization', str(result))
        self.assertNotIn('audit', result)

    def test_key_failure_is_safe_and_does_not_send_request(self):
        self.key.side_effect = RuntimeError('private secret')
        with self.assertRaises(AppError) as caught:
            self.history(session_id='1')
        self.assertNotIn('private', caught.exception.message)
        self.get.assert_not_called()
        self.legacy.assert_not_called()

    def test_flag_off_preserves_existing_transport_and_best_effort_delete(self):
        with patch.dict(os.environ, {'SOPHIA_THREAD_CAPABILITIES_ENABLED': 'false'}):
            self.history(session_id='1')
            self.assertEqual('Bearer legacy-test', self.get.call_args.kwargs['headers']['Authorization'])
            self.assertNotIn(t.REQUEST_HEADER, self.get.call_args.kwargs['headers'])
            self.assertNotIn('allow_redirects', self.get.call_args.kwargs)
            self.delete.return_value = self.response({}, 500)
            result = chat.delete_chat_session(1, self.actor, self.db)
            self.assertFalse(result['remote_deleted'])
            self.assertIsNone(self.db.get(AgentSession, 1))
        self.key.assert_not_called()

    def test_flag_defaults_off_and_bad_value_never_silently_downgrades(self):
        with patch.dict(os.environ):
            os.environ.pop('SOPHIA_THREAD_CAPABILITIES_ENABLED', None)
            self.assertFalse(t.enabled())
            os.environ['SOPHIA_THREAD_CAPABILITIES_ENABLED'] = 'typo'
            with self.assertRaises(AppError):
                self.history(session_id='1')
            self.get.assert_not_called()
            self.legacy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
