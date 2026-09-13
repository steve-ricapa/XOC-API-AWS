"""Ticket-case isolation: real JWT projection/dependencies/router/store, fake I/O."""
from __future__ import annotations

from decimal import Decimal
import os
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import jwt

with patch.dict(os.environ, {
    'AWS_EC2_METADATA_DISABLED': 'true', 'AWS_DEFAULT_REGION': 'us-east-1',
    'AWS_ACCESS_KEY_ID': 'testing', 'AWS_SECRET_ACCESS_KEY': 'testing',
    'CASES_TABLE_NAME': 'offline-cases',
}):
    from src.handlers.routes import cases
    from src.handlers.authorizers import jwt_authorizer
    from src.shared import cases_store, dependencies
    from src.shared.errors import AppError


SECRET = 'offline-case-isolation-secret-not-for-production'


class TicketCaseIsolationTests(unittest.TestCase):
    def setUp(self):
        self.store = MagicMock()
        store_patch = patch.object(cases_store, 'table', self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        key_patch = patch.object(jwt_authorizer, 'get_jwt_secret_key', return_value=SECRET)
        key_patch.start()
        self.addCleanup(key_patch.stop)
        self.claims = {}
        app = FastAPI()
        app.include_router(cases.router)
        app.dependency_overrides[dependencies.get_request_claims] = lambda: self.claims

        @app.exception_handler(AppError)
        async def handle_error(_request, exc):
            return JSONResponse({'error': exc.message, 'code': exc.code}, status_code=exc.status_code)

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def authenticate(self, *, agent=False, tenant=7, projected=True, **extra):
        claims = {'sub': 'agent-runtime-7-SOPHIA' if agent else '12',
                  'type': 'access', 'tenant_id': tenant,
                  'iat': int(time.time()), 'exp': int(time.time()) + 60}
        if agent:
            claims.update(agent_type='SOPHIA', scopes=['agent:invoke'])
        else:
            claims['role'] = 'ADMIN'
        claims.update(extra)
        token = jwt.encode(claims, SECRET, algorithm='HS256')
        # Call the real authorizer without the Lambda logging decorator.
        verified = jwt_authorizer.handler.__wrapped__({'headers': {'Authorization': 'Bearer ' + token}}, None)
        self.assertTrue(verified['isAuthorized'])
        self.claims = verified['context'] if projected else jwt.decode(token, SECRET, algorithms=['HS256'])

    @staticmethod
    def row(tenant=7):
        return {'tenant_id': Decimal(tenant), 'case_id': 'case-owned',
                'ticket_id': 'ticket-owned', 'subject': 'private-case-content',
                'status': 'RESUELTO', 'attempts_log': []}

    def get(self, row, *, path='/cases/ticket/ticket-owned', **kwargs):
        self.store.query.return_value = {'Items': [] if row is None else [row]}
        return self.client.request('GET', path, **kwargs)

    def assert_not_found(self, response):
        self.assertEqual(404, response.status_code)
        self.assertEqual({'error': 'No case found for this ticket', 'code': 'not_found'}, response.json())
        self.assertNotIn('private-case-content', response.text)
        self.assertNotIn('tenant_id', response.text)

    def test_human_same_tenant_preserves_response(self):
        self.authenticate()
        row = self.row()
        response = self.get(row)
        self.assertEqual(200, response.status_code)
        self.assertEqual(row, response.json())

    def test_agent_same_tenant_preserves_response(self):
        self.authenticate(agent=True)
        self.assertEqual(200, self.get(self.row()).status_code)

    def test_human_other_tenant_returns_same_not_found_as_absent(self):
        self.authenticate()
        foreign = self.get(self.row(8))
        absent = self.get(None)
        self.assert_not_found(foreign)
        self.assert_not_found(absent)
        self.assertEqual((absent.content, absent.headers), (foreign.content, foreign.headers))

    def test_agent_other_tenant_returns_same_not_found_as_absent(self):
        self.authenticate(agent=True)
        self.assert_not_found(self.get(self.row(8)))
        self.assert_not_found(self.get(None))

    def test_inexistent_ticket_preserves_existing_error(self):
        self.authenticate()
        self.assert_not_found(self.get(None, path='/cases/ticket/missing'))

    def test_body_and_query_cannot_select_foreign_tenant(self):
        for agent in (False, True):
            with self.subTest(agent=agent):
                self.authenticate(agent=agent)
                self.assert_not_found(self.get(self.row(8),
                    path='/cases/ticket/ticket-owned?tenantId=8&tenant_id=8&effective_tenant_id=8',
                    json={'tenantId': 8, 'tenant_id': 8}))
                self.assertEqual(200, self.get(self.row(), path='/cases/ticket/ticket-owned?tenantId=8').status_code)

    def test_authorizer_effective_tenant_wins_over_actor_tenant(self):
        self.authenticate(tenant=8, role='ADMIN_XOC', delegation=True, acting_tenant_id=7)
        self.assertEqual('7', self.claims['tenantId'])
        self.assertEqual(200, self.get(self.row(7)).status_code)
        self.assert_not_found(self.get(self.row(8)))

    def test_local_verified_claims_respect_delegation(self):
        self.authenticate(tenant=8, role='ADMIN_XOC', delegation=True, acting_tenant_id=7, projected=False)
        self.assertEqual(200, self.get(self.row(7)).status_code)
        self.assert_not_found(self.get(self.row(8)))

    def test_local_claims_do_not_activate_delegation_from_tenant_alone(self):
        self.authenticate(tenant=7, delegation=False, acting_tenant_id=8, projected=False)
        self.assert_not_found(self.get(self.row(8)))
        self.assertEqual(200, self.get(self.row(7)).status_code)

    def test_missing_tenant_rejected_before_lookup(self):
        for claims in ({}, {'type': 'access', 'sub': '12'},
                       {'tokenType': 'access', 'userId': 'service', 'scopes': ['agent:invoke']}):
            with self.subTest(claims=claims):
                self.claims = claims
                response = self.get(self.row())
                self.assertIn(response.status_code, (401, 403))
        self.store.query.assert_not_called()

    def test_case_without_tenant_is_not_disclosed(self):
        self.authenticate()
        for owner in (None, '', 'invalid'):
            row = self.row()
            row['tenant_id'] = owner
            self.assert_not_found(self.get(row))
        row.pop('tenant_id')
        self.assert_not_found(self.get(row))

    def test_existing_lookup_unchanged_and_no_store_writes(self):
        self.authenticate()
        self.get(self.row())
        self.store.query.assert_called_once()
        kwargs = self.store.query.call_args.kwargs
        self.assertEqual('TicketIndex', kwargs['IndexName'])
        self.assertEqual(1, kwargs['Limit'])
        expression = kwargs['KeyConditionExpression'].get_expression()
        self.assertEqual('gsi1pk', expression['values'][0].name)
        self.assertEqual('CASE#TICKET#ticket-owned', expression['values'][1])
        self.assertEqual(['query'], [call[0] for call in self.store.mock_calls])

    def test_non_access_tokens_remain_rejected_before_lookup(self):
        for kind in ('refresh', 'sophia_thread_capability', 'sophia_tool_capability'):
            self.claims = {'tokenType': kind, 'tenantId': '7', 'userId': '12'}
            self.assertEqual(403, self.get(self.row()).status_code)
        self.store.query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
