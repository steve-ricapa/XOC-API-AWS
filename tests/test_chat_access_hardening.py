"""Offline checks: existing AgentSession mapping on SQLite, no RDS or runtime.

The query predicates execute against an in-memory DB instead of mocking their
results; external HTTP, signing and audit writes are replaced at the boundary.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.handlers.routes import agents, chat
from src.persistence.models import AgentSession
from src.shared.errors import AppError, ForbiddenError, ValidationError


def user(*, user_id=12, tenant_id=7, role="ADMIN", delegated=False, plan="ACTIVE"):
    return SimpleNamespace(
        id=user_id, tenant_id=tenant_id, effective_tenant_id=tenant_id,
        role=role, delegation_active=delegated, username="test-user",
        email="test@example.invalid", tenant=SimpleNamespace(plan_status=plan),
    )


class AgentExchangeTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        token_patch = patch.object(agents, "create_access_token", return_value="test-token")
        audit_patch = patch.object(agents, "log_audit")
        self.token = token_patch.start()
        self.audit = audit_patch.start()
        self.addCleanup(token_patch.stop)
        self.addCleanup(audit_patch.stop)

    def test_default_and_normalized_sophia_keep_response_and_claims(self):
        for payload in (None, {}, {"agentType": "SOPHIA"}, {"agentType": " sophia "}):
            with self.subTest(payload=payload):
                result = agents.authenticate_agent_from_user(payload, user(), self.db)
                self.assertEqual("test-token", result["access_token"])
                self.assertEqual("Bearer", result["token_type"])
                self.assertEqual(3600, result["expires_in"])
                self.assertEqual(7, result["tenant_id"])
                self.assertEqual("ADMIN", result["role"])
                self.assertEqual("ACTIVE", result["plan_status"])
                self.assertEqual("agent-runtime-7-SOPHIA", self.token.call_args.kwargs["identity"])
                self.assertEqual(
                    {"scopes": ["agent:invoke"], "tenant_id": 7, "agent_type": "SOPHIA"},
                    self.token.call_args.kwargs["additional_claims"],
                )
                self.assertEqual(timedelta(hours=1), self.token.call_args.kwargs["expires_delta"])

    def test_unknown_operational_and_malformed_types_do_not_issue_or_audit(self):
        for value in ("VICTOR", "VIKTOR", "SVAFUNC", "ADMIN", "unknown", "", " ",
                      None, True, 1, [], {}, ["SOPHIA"], "SOPHIA\nVICTOR"):
            with self.subTest(value=value), self.assertRaises(ValidationError) as caught:
                agents.authenticate_agent_from_user({"agentType": value}, user(), self.db)
            self.assertEqual("Unsupported agentType", caught.exception.message)
        self.token.assert_not_called()
        self.audit.assert_not_called()
        self.db.assert_not_called()
        self.assertEqual([], self.db.mock_calls)

    def test_non_object_body_is_rejected(self):
        for payload in ([], "SOPHIA", 1, False):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                agents.authenticate_agent_from_user(payload, user(), self.db)
        self.token.assert_not_called()
        self.audit.assert_not_called()

    def test_delegation_and_role_rules_are_preserved(self):
        for role in ("USER", "ADMIN", "ADMIN_XOC", "SUPERADMIN"):
            with self.subTest(role=role):
                actor = user(role=role, delegated=role in {"ADMIN_XOC", "SUPERADMIN"})
                result = agents.authenticate_agent_from_user({}, actor, self.db)
                self.assertEqual(role, result["role"])
        self.token.reset_mock()
        self.audit.reset_mock()
        for actor in (user(role="ADMIN_XOC"), user(role="SUPERADMIN"), user(role="unknown")):
            with self.assertRaises(ForbiddenError):
                agents.authenticate_agent_from_user({}, actor, self.db)
        self.token.assert_not_called()
        self.audit.assert_not_called()

    def test_body_cannot_override_trusted_tenant_or_token_scope(self):
        actor = user(role="ADMIN_XOC", tenant_id=8, delegated=True)
        actor.tenant_id = None
        agents.authenticate_agent_from_user(
            {"tenant_id": 99, "user_id": 99, "scopes": ["admin"], "role": "SUPERADMIN"},
            actor, self.db,
        )
        self.assertEqual(
            {"scopes": ["agent:invoke"], "tenant_id": 8, "agent_type": "SOPHIA"},
            self.token.call_args.kwargs["additional_claims"],
        )

    def test_http_body_optional_matches_web_caller_and_unknown_type_is_400(self):
        app = FastAPI()
        app.include_router(agents.router)
        app.dependency_overrides[agents.get_current_user] = lambda: user()
        app.dependency_overrides[agents.get_db_session] = lambda: self.db

        @app.exception_handler(AppError)
        async def error_handler(_request, exc):
            return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status_code)

        with TestClient(app) as client:
            response = client.post("/agents/auth/token-from-user")
            self.assertEqual(200, response.status_code)
            self.assertEqual("test-token", response.json()["access_token"])
            response = client.post("/agents/auth/token-from-user", json={"agentType": "VICTOR"})
            self.assertEqual(400, response.status_code)
            self.assertEqual("validation_error", response.json()["code"])


class ChatThreadOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        # Only the existing model's table in ephemeral SQLite, never application
        # get_engine(), metadata.create_all() against RDS, or a migration.
        AgentSession.__table__.create(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.actor = user()
        for session_id, tenant_id, user_id, thread_id, purpose in (
            (1, 7, 12, "thread_owned", "sophia_chat"),
            (2, 7, 12, "thread_older", "sophia_chat"),
            (3, 7, 13, "thread_other_user", "sophia_chat"),
            (4, 8, 12, "thread_other_tenant", "sophia_chat"),
            (5, 8, 13, "thread_foreign", "sophia_chat"),
            (6, 7, 12, "thread_demo", "sophia_demo"),
        ):
            self.db.add(AgentSession(
                id=session_id, tenant_id=tenant_id, user_id=user_id,
                external_thread_id=thread_id, purpose=purpose, title="Saved conversation",
                last_activity_at=datetime(2026, 1, 2) if session_id == 1 else datetime(2026, 1, 1),
            ))
        self.db.commit()
        self.runtime = self.start_patch("_resolve_agent_routes", return_value={
            "function_base_url": "https://example.invalid", "function_route_sophia": "/chat",
            "function_route_sophia_history": "/history", "extra_json": {},
        })
        self.token = self.start_patch("_build_agent_invoke_token", return_value="test-service-token")
        self.post = self.start_patch("requests.post", side_effect=self.reply)
        self.get = self.start_patch("requests.get", return_value=self.response({"messages": []}))
        self.delete = self.start_patch("requests.delete")
        cache_patch = patch.object(chat, "_SESSION_AFFINITY", {1: {"ARRAffinity": "owned-cookie"}})
        cache_patch.start()
        self.addCleanup(cache_patch.stop)

    def start_patch(self, path, **kwargs):
        target = "src.handlers.routes.chat." + path
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    @staticmethod
    def response(payload):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = payload
        response.cookies = {}
        return response

    def reply(self, _url, **kwargs):
        return self.response({"text": "Hola", "thread_id": kwargs["json"].get("thread_id", "thread_created")})

    def send(self, payload=None, actor=None):
        return chat.proxy_chat({"message": "Hola", **(payload or {})}, actor or self.actor, self.db)

    def history(self, actor=None, **kwargs):
        return chat.chat_history(actor or self.actor, self.db, **kwargs)

    def snapshot(self):
        return [(s.id, s.tenant_id, s.user_id, s.external_thread_id, s.title, s.last_activity_at)
                for s in self.db.scalars(select(AgentSession).order_by(AgentSession.id))]

    def assert_denied_before_runtime(self, call):
        before = self.snapshot()
        with self.assertRaises(ValidationError) as caught:
            call()
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.delete.assert_not_called()
        self.token.assert_not_called()
        self.runtime.assert_not_called()
        self.assertEqual(before, self.snapshot())
        return caught.exception

    def test_chat_session_and_alias_resolve_saved_thread(self):
        for payload in ({"session_id": 1}, {"sessionId": "1"},
                        {"session_id": "01", "sessionId": 1}):
            with self.subTest(payload=payload):
                result = self.send(payload)
                self.assertEqual(1, result["session_id"])
                self.assertEqual("thread_owned", self.post.call_args.kwargs["json"]["thread_id"])
                self.assertEqual("thread_owned", self.post.call_args.kwargs["params"]["thread_id"])

    def test_chat_direct_thread_uses_its_session_not_latest_and_correct_affinity(self):
        for key in ("thread_id", "threadId"):
            result = self.send({key: "thread_older"})
            self.assertEqual(2, result["session_id"])
            self.assertEqual("thread_owned", self.db.get(AgentSession, 1).external_thread_id)
            self.assertIsNone(self.post.call_args.kwargs["cookies"])
        self.send({"thread_id": "thread_owned"})
        self.assertEqual({"ARRAffinity": "owned-cookie"}, self.post.call_args.kwargs["cookies"])

    def test_history_accepts_owned_session_thread_and_matching_pair(self):
        for kwargs in ({"session_id": "1"}, {"sessionId": "1"}, {"thread_id": "thread_owned"},
                       {"threadId": "thread_owned"}, {"session_id": "1", "threadId": "thread_owned"}):
            with self.subTest(kwargs=kwargs):
                result = self.history(**kwargs)
                self.assertEqual(1, result["session_id"])
                self.assertEqual("thread_owned", self.get.call_args.kwargs["params"]["thread_id"])

    def test_unknown_foreign_user_and_foreign_tenant_thread_have_same_error(self):
        for thread_id in ("thread_unknown", "thread_other_user", "thread_other_tenant", "thread_foreign"):
            for route in (self.send, lambda payload: self.history(**payload)):
                with self.subTest(thread_id=thread_id, route=route):
                    error = self.assert_denied_before_runtime(lambda: route({"thread_id": thread_id}))
                    self.assertEqual("Agent session not found", error.message)
                    self.assertNotIn(thread_id, error.message)

    def test_foreign_session_never_falls_back_to_latest(self):
        for session_id in (3, 4, 5, 999):
            for route in (self.send, lambda payload: self.history(**payload)):
                with self.subTest(session_id=session_id):
                    self.assert_denied_before_runtime(lambda: route({"session_id": session_id}))

    def test_session_plus_different_thread_rejected_even_for_same_owner(self):
        for thread_id in ("thread_older", "thread_other_user", "thread_other_tenant", "thread_unknown"):
            for route in (self.send, lambda payload: self.history(**payload)):
                with self.subTest(thread_id=thread_id):
                    self.assert_denied_before_runtime(
                        lambda: route({"session_id": 1, "thread_id": thread_id}))

    def test_matching_pair_does_not_change_binding(self):
        result = self.send({"sessionId": 1, "threadId": "thread_owned"})
        self.assertEqual(1, result["session_id"])
        self.assertEqual("thread_owned", self.db.get(AgentSession, 1).external_thread_id)

    def test_conflicting_aliases_rejected(self):
        for payload in ({"session_id": 1, "sessionId": 2},
                        {"thread_id": "thread_owned", "threadId": "thread_older"}):
            for route in (self.send, lambda payload: self.history(**payload)):
                self.assert_denied_before_runtime(lambda: route(payload))

    def test_malformed_ids_rejected_without_fallback_or_http(self):
        for value in (True, False, [], {}, 1.1, 0, -1, "", "abc", "1.1", 2147483648):
            for route in (self.send, lambda payload: self.history(**payload)):
                with self.subTest(session_id=value):
                    self.assert_denied_before_runtime(lambda: route({"session_id": value}))
        for value in (True, 1, [], {}, "", " ", " thread_owned", "bad\nthread", "x" * 501):
            for route in (self.send, lambda payload: self.history(**payload)):
                with self.subTest(thread_id=value):
                    self.assert_denied_before_runtime(lambda: route({"thread_id": value}))

    def test_history_requires_saved_thread(self):
        self.assert_denied_before_runtime(lambda: self.history())
        self.db.get(AgentSession, 1).external_thread_id = None
        self.db.commit()
        self.assert_denied_before_runtime(lambda: self.history(session_id="1"))

    def test_thread_ids_are_opaque_not_tied_to_one_provider_prefix(self):
        opaque_id = "26c471a8-6bad-45c8-b71c-e5d323f4c94e"
        self.db.get(AgentSession, 1).external_thread_id = opaque_id
        self.db.commit()
        self.assertEqual(1, self.send({"thread_id": opaque_id})["session_id"])
        self.assertEqual(1, self.history(thread_id=opaque_id)["session_id"])

    def test_database_failure_never_falls_back_to_client_thread(self):
        with patch.object(self.db, "execute", side_effect=RuntimeError("test database unavailable")):
            for call in (lambda: self.send({"thread_id": "thread_owned"}),
                         lambda: self.history(thread_id="thread_owned")):
                with self.assertRaises(RuntimeError):
                    call()
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.token.assert_not_called()

    def test_payload_tenant_must_match_authenticated_effective_tenant(self):
        self.assert_denied_before_runtime(lambda: self.send({"tenantId": 8, "thread_id": "thread_other_tenant"}))
        self.assert_denied_before_runtime(lambda: self.history(tenantId="8", thread_id="thread_other_tenant"))

    def test_regular_user_can_continue_own_conversation(self):
        actor = user(role="USER")
        self.assertEqual(1, self.send({"session_id": 1}, actor)["session_id"])
        self.assertEqual(1, self.history(actor=actor, session_id="1")["session_id"])

    def test_no_ids_reuses_latest_owned_non_demo_session(self):
        result = self.send()
        self.assertEqual(1, result["session_id"])
        self.assertEqual("thread_owned", self.post.call_args.kwargs["json"]["thread_id"])

    def test_new_session_ignores_stale_client_ids_and_cookies(self):
        result = self.send({"new_session": True, "session_id": 3, "thread_id": "thread_foreign"})
        self.assertNotIn("thread_id", self.post.call_args.kwargs["json"])
        self.assertEqual({}, self.post.call_args.kwargs["params"])
        self.assertIsNone(self.post.call_args.kwargs["cookies"])
        created = self.db.get(AgentSession, result["session_id"])
        self.assertEqual((7, 12, "sophia_chat", "thread_created"),
                         (created.tenant_id, created.user_id, created.purpose, created.external_thread_id))
        self.assertEqual("thread_foreign", self.db.get(AgentSession, 5).external_thread_id)

    def test_first_conversation_without_any_session_can_create_thread(self):
        result = self.send(actor=user(user_id=90))
        self.assertNotIn("thread_id", self.post.call_args.kwargs["json"])
        self.assertEqual(90, self.db.get(AgentSession, result["session_id"]).user_id)

    def test_demo_always_uses_server_selected_owned_thread(self):
        result = self.send({"session_id": 3, "thread_id": "thread_foreign", "new_session": True},
                           actor=user(plan="DEMO"))
        self.assertEqual(6, result["session_id"])
        self.assertEqual("thread_demo", self.post.call_args.kwargs["json"]["thread_id"])
        self.assertEqual("consulta", self.post.call_args.kwargs["json"]["chat_mode"])
        self.assert_denied_before_runtime_after_reset(lambda: self.history(actor=user(plan="DEMO"), session_id="6"))

    def assert_denied_before_runtime_after_reset(self, call):
        for mock in (self.get, self.post, self.token, self.runtime):
            mock.reset_mock()
        self.assert_denied_before_runtime(call)

    def test_first_demo_conversation_never_adopts_client_thread(self):
        result = self.send({"thread_id": "thread_foreign"}, actor=user(user_id=90, plan="DEMO"))
        self.assertNotIn("thread_id", self.post.call_args.kwargs["json"])
        self.assertEqual("sophia_demo", self.db.get(AgentSession, result["session_id"]).purpose)

    def test_delegated_admin_uses_effective_tenant_and_original_actor(self):
        actor = user(role="ADMIN_XOC", tenant_id=8, delegated=True)
        actor.tenant_id = None
        self.assert_denied_before_runtime(lambda: self.send({"thread_id": "thread_owned"}, actor))
        self.assert_denied_before_runtime(lambda: self.history(actor=actor, thread_id="thread_foreign"))
        result = self.send({"thread_id": "thread_other_tenant"}, actor)
        self.assertEqual(4, result["session_id"])
        self.token.assert_called_with(8, "SOPHIA")
        self.assertEqual(4, self.history(actor=actor, thread_id="thread_other_tenant")["session_id"])

    def test_nondelegated_admin_xoc_cannot_invoke_runtime(self):
        for route in (lambda: self.send(actor=user(role="ADMIN_XOC")),
                      lambda: self.history(actor=user(role="ADMIN_XOC"), session_id="1")):
            with self.assertRaises(ForbiddenError):
                route()
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.token.assert_not_called()

    def test_runtime_cannot_rebind_existing_conversation(self):
        self.post.side_effect = None
        self.post.return_value = self.response({"thread_id": "thread_foreign", "text": "private"})
        before = self.snapshot()
        with patch.object(chat, "_maybe_execute_chat_tool_request") as execute_tool:
            with self.assertRaises(AppError) as caught:
                self.send({"session_id": 1})
            execute_tool.assert_not_called()
        self.assertEqual(502, caught.exception.status_code)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn("private", caught.exception.message)

    def test_new_runtime_thread_cannot_adopt_known_foreign_binding(self):
        self.post.side_effect = None
        before = self.snapshot()
        for thread_id in ("thread_other_user", "thread_other_tenant", "thread_foreign", "", [], 123):
            with self.subTest(thread_id=thread_id):
                self.post.return_value = self.response({"thread_id": thread_id})
                with self.assertRaises(AppError) as caught:
                    self.send({"new_session": True})
                self.assertEqual(502, caught.exception.status_code)
                self.assertEqual(before, self.snapshot())

    def test_timeout_does_not_replace_binding(self):
        self.post.side_effect = chat.requests.exceptions.Timeout("private provider text")
        result = self.send({"session_id": 1})
        self.assertEqual(1, result["session_id"])
        self.assertEqual("thread_owned", result["thread_id"])
        self.assertNotIn("private provider text", result["text"])


if __name__ == "__main__":
    unittest.main()
