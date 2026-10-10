"""I01-I30: actual Chat handler and supervised TCP MCP, fictional auth/providers."""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import json
import socket
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.handlers.routes import chat
from src.persistence.models import AgentSession
from src.shared import dependencies, sophia_tool_chat as d
from src.shared.errors import AppError
from src.shared.sophia_mcp_client import LocalMCPExecution
from src.shared.sophia_tool_execution import local_execution
from src.tool_gateway.schemas import ToolContext, ToolRequest
from fixture_issuer import issue

SERVER = None
EVIDENCE = []
SECRET = "IC1-PUBLIC-FICTIONAL-SIGNING-KEY-ONLY"


class Integration(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.engine = create_engine("sqlite:///:memory:")
        AgentSession.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.calls = []
        self.runtime_timings = []
        self.records = []
        self.actor = self.user()
        self.port = LocalMCPExecution(SERVER.url, self.sign)
        self.first = {}
        self.final_tool = False
        self.final_failure = False
        self.stack.enter_context(patch.object(d, "enabled", return_value=True))
        self.stack.enter_context(patch.object(d, "get_jwt_secret_key", return_value=SECRET))
        self.stack.enter_context(patch.object(chat, "get_jwt_secret_key", return_value=SECRET))
        self.stack.enter_context(patch.object(chat, "_resolve_agent_routes", return_value={
            "function_base_url": "https://runtime.invalid", "function_route_sophia": "/run", "extra_json": {}}))
        self.stack.enter_context(patch.object(chat.requests, "post", side_effect=self.runtime))
        self.local = self.stack.enter_context(patch.object(d, "ToolExecutor", side_effect=AssertionError("NO_LOCAL_FALLBACK")))
        self.legacy = self.stack.enter_context(patch.object(chat, "_maybe_execute_chat_tool_request", side_effect=AssertionError("NO_LEGACY")))
        self.create = self.stack.enter_context(patch.object(chat, "_create_ticket_from_confirmed_proposal", side_effect=AssertionError("NO_WRITE")))

    def tearDown(self):
        self.local.assert_not_called()
        self.legacy.assert_not_called()
        self.create.assert_not_called()

    def user(self, tenant=91001, user=92001, role="ADMIN", delegated=False, effective=None):
        stored = SimpleNamespace(id=user, tenant_id=tenant, role=role, username="fixture",
            email="fixture@example.invalid", tenant=SimpleNamespace(plan_status="ACTIVE"))
        auth_store = MagicMock()
        auth_store.get.return_value = stored
        # Existing dependency resolves context from AUTH claims + stored actor.
        claims = dependencies.require_access_claims({"type": "access", "sub": str(user),
            "delegation": delegated, "acting_tenant_id": effective})
        return dependencies.get_current_user(claims, auth_store)

    def sign(self, context, request, deadline):
        self.records.append((context, request, deadline))  # ephemeral test assertion, never report/log
        return issue(context, request, deadline)

    def runtime(self, url, **kwargs):
        started = time.monotonic()
        self.assertEqual("https://runtime.invalid/run", url)
        body = kwargs["json"]
        self.assertFalse(kwargs["allow_redirects"])
        self.assertLessEqual(kwargs["timeout"], 9)
        self.calls.append(body)
        reply = {"protocol": d.PROTOCOL, "request_id": body["request_id"],
                 "thread_id": body.get("thread_id", "thread_" + body["request_id"])}
        if body["phase"] == "request":
            reply.update(self.first)
        elif self.final_failure:
            raise requests.Timeout("TEST SENSITIVE ERROR")
        elif self.final_tool:
            reply["tool_request"] = {"tool_name": "xoc.tickets.list", "arguments": {}}
        else:
            self.assertEqual({"request_id", "tool_name", "status", "code", "audit_id", "data"}, set(body["tool_result"]))
            reply["text"] = "Respuesta final ficticia"
        self.runtime_timings.append({"phase": body["phase"], "seconds": time.monotonic()-started})
        return SimpleNamespace(status_code=200, cookies={}, json=lambda: reply)

    def send(self, name="xoc.tickets.list", arguments=None, *, body=None, first=None, expected=1, actor=None):
        self.calls = []
        self.runtime_timings = []
        self.first = first if first is not None else {
            "tool_request": {"tool_name": name, "arguments": arguments or {}}, "continuation": "opaque-fixture"}
        rid = "lab-ic1-" + uuid4().hex
        before = len(self.port.measurements)
        started = time.monotonic()
        with patch.object(chat, "uuid", SimpleNamespace(uuid4=lambda: rid)), local_execution(self.port):
            result = chat.proxy_chat(body or {"message": "Consulta ficticia", "new_session": True}, actor or self.actor, self.db)
        self.assertLessEqual(len(self.calls), 2)
        stages = self.port.measurements[before:]
        self.assertLessEqual(sum(r["tools_call_count"] for r in stages), 1)
        events = [r for r in SERVER.events() if r.get("request_id") == rid and r.get("event") == "request_finished"]
        executed = sum(r.get("handler_calls", 0) for r in events)
        self.assertEqual(expected, executed)
        for row in events:
            for key in ("writes", "write_attempts", "provider_attempts", "network_attempts", "secret_file_attempts"):
                self.assertEqual(0, row.get(key, 0))
        EVIDENCE.append(dict(case=self._testMethodName, request_id=rid, runtime_turns=len(self.calls),
            mcp_tools_call=sum(r["tools_call_count"] for r in stages), handler_calls=executed,
            total_seconds=time.monotonic()-started, mcp_stages=stages, runtime_stages=self.runtime_timings))
        self.assertNotIn("LAB_CANARY_NEVER_PUBLIC", json.dumps(self.calls) + json.dumps(result))
        return result

    def data(self):
        return self.calls[-1]["tool_result"]["data"]

    def test_I01_normal_chat(self):
        self.send(first={"text": "Hola"}, expected=0)
        self.assertEqual(1, len(self.calls))
        self.assertEqual([], self.records)

    def test_I02_list(self):
        result = self.send()
        self.assertEqual("Respuesta final ficticia", result["text"])
        self.assertEqual(2, len(self.calls))
        self.assertEqual(2, self.data()["count"])

    def test_I03_get_owned_and_cross_tenant(self):
        self.send("xoc.tickets.get", {"ticket_id": "lab-ticket-a1"})
        self.assertEqual("lab-ticket-a1", self.data()["ticket"]["ticket_id"])
        self.send("xoc.tickets.get", {"ticket_id": "lab-ticket-b1"})
        self.assertIsNone(self.data())

    def test_I04_documents(self):
        self.send("xoc.documents.list")
        self.assertEqual(2, self.data()["count"])
        self.assertNotIn("s3_key", json.dumps(self.data()))

    def test_I05_unread(self):
        self.send("xoc.notifications.unread_count")
        self.assertEqual({"count": 2}, self.data())

    def test_I06_two_tenants(self):
        self.send()
        self.assertTrue(all("-a" in t["ticket_id"] for t in self.data()["tickets"]))
        self.send(actor=self.user(91002, 92003))
        self.assertTrue(all("-b" in t["ticket_id"] for t in self.data()["tickets"]))

    def test_I07_user_unread_isolation(self):
        self.send("xoc.notifications.unread_count")
        self.assertEqual(2, self.data()["count"])
        self.send("xoc.notifications.unread_count", actor=self.user(user=92002))
        self.assertEqual(5, self.data()["count"])

    def test_I08_user(self):
        self.send(actor=self.user(role="USER"))
        self.assertEqual("allowed", self.calls[-1]["tool_result"]["status"])

    def test_I09_admin(self):
        self.send()
        self.assertEqual("ADMIN", self.records[-1][0].role)

    def test_I10_delegated_admin_xoc(self):
        self.send(actor=self.user(None, 92999, "ADMIN_XOC", True, 91002))
        self.assertIsNone(self.records[-1][0].tenant_id)
        self.assertEqual(91002, self.records[-1][0].effective_tenant_id)
        self.assertTrue(all("-b" in t["ticket_id"] for t in self.data()["tickets"]))

    def test_I11_admin_xoc_not_delegated(self):
        with self.assertRaises(AppError):
            self.send(actor=self.user(None, 92999, "ADMIN_XOC"), expected=0)
        self.assertEqual([], self.records)
        # Also prove MCP policy denies even a valid fixture assertion of this role.
        ctx = ToolContext(None, 91001, 92999, "ADMIN_XOC", False, "lab-ic1-nondelegated", "sophia_chat")
        result = self.port.execute(ctx, ToolRequest("xoc.tickets.list", {}, ctx.request_id), deadline=time.monotonic()+4)
        self.assertEqual("denied", result.status.value)
        self.assertEqual("delegation_required", result.code)

    def test_I12_superadmin(self):
        self.send(actor=self.user(91001, 92998, "SUPERADMIN", True, 91001), expected=0)
        self.assertEqual("denied", self.calls[-1]["tool_result"]["status"])

    def test_I13_unknown_and_disabled(self):
        for name in ("xoc.evil.execute", "xoc.dashboard.summary", "xoc.integrations.summary"):
            self.send(name, expected=0)
            self.assertIsNone(self.data())

    def test_I14_write(self):
        self.send("xoc.tickets.create", expected=0)
        self.assertIsNone(self.data())

    def test_I15_injection(self):
        for key in ("tenant_id", "tenantId", "user_id", "userId", "role", "delegation_active", "_meta"):
            self.send(arguments={key: "evil"}, expected=0)
            self.assertIsNone(self.data())

    def test_I16_down_and_I28_no_fallback(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))  # bound, deliberately not listening
            self.port = LocalMCPExecution(f"http://127.0.0.1:{reserved.getsockname()[1]}/mcp", self.sign)
            self.send(expected=0)
        self.assertIsNone(self.data())
        self.local.assert_not_called()

    def test_I17_timeout_and_no_remaining_margin(self):
        from mcp import ClientSession
        async def delayed(*a, **kw):
            await asyncio.sleep(10)
        with patch.object(ClientSession, "call_tool", side_effect=delayed):
            started = time.monotonic()
            self.send(expected=0)
            self.assertLess(time.monotonic()-started, 4)
            self.assertIsNone(self.data())
        ctx = self.records[-1][0]
        calls = len(self.records)
        result = self.port.execute(ctx, ToolRequest("xoc.tickets.list", {}, ctx.request_id), deadline=time.monotonic()+.2)
        self.assertEqual("mcp_deadline", result.code)
        self.assertEqual(calls, len(self.records))

    def bad_wire(self, changes):
        payload = dict(request_id="lab-unbound", tool_name="xoc.tickets.list", status="allowed",
            code="allowed", audit_id="a"*24, data={"tickets": []})
        def wire(encoded, request, timeout, row):
            sent = json.loads(encoded)
            payload["request_id"] = sent["request_id"]
            payload.update(changes)
            return SimpleNamespace(structuredContent=payload, isError=False,
                content=[SimpleNamespace(type="text", text=json.dumps(payload))])
        return AsyncMock(side_effect=wire)

    def test_I18_invalid_result(self):
        with patch.object(self.port, "_call", self.bad_wire({"extra": "UNTRUSTED"})):
            self.send(expected=0)
        self.assertIsNone(self.data())

    def test_I19_request_binding(self):
        with patch.object(self.port, "_call", self.bad_wire({"request_id": "lab-other"})):
            self.send(expected=0)
        self.assertIsNone(self.data())

    def test_I20_tool_binding(self):
        with patch.object(self.port, "_call", self.bad_wire({"tool_name": "xoc.documents.list"})):
            self.send(expected=0)
        self.assertIsNone(self.data())

    def test_I21_second_tool(self):
        self.final_tool = True
        result = self.send()
        self.assertEqual("tool_call_limit", result["metadata"]["toolGateway"]["completionCode"])

    def test_I22_second_turn_failure(self):
        self.final_failure = True
        result = self.send()
        self.assertEqual("runtime_unavailable", result["metadata"]["toolGateway"]["completionCode"])
        self.assertNotIn("SENSITIVE", json.dumps(result))

    def test_I23_foreign_session(self):
        foreign = AgentSession(tenant_id=91002, user_id=92003, external_thread_id="foreign", purpose="sophia_chat")
        self.db.add(foreign)
        self.db.commit()
        with self.assertRaises(AppError):
            self.send(body={"message": "Consulta", "session_id": foreign.id}, expected=0)
        self.assertEqual([], self.calls)
        self.assertEqual([], self.records)

    def test_I24_new_session(self):
        result = self.send()
        session = self.db.get(AgentSession, result["session_id"])
        self.assertEqual((91001, 92001), (session.tenant_id, session.user_id))

    def test_I25_own_continuation_and_runtime_thread_rejection(self):
        result = self.send()
        self.send(body={"message": "Continuar", "session_id": result["session_id"]})
        self.assertEqual(result["thread_id"], self.calls[0]["thread_id"])
        with self.assertRaises(AppError):
            self.send(body={"message": "Continuar", "session_id": result["session_id"]},
                first={"thread_id": "foreign", "tool_request": {"tool_name": "xoc.tickets.list", "arguments": {}}, "continuation": "opaque"})

    def test_I26_proposal_I27_cancel_no_write(self):
        result = self.send(first={"text": "Propuesta", "action_plan": {"subject": "Prueba", "description": "Solo lectura"}}, expected=0)
        self.assertEqual("NEEDS_CONFIRMATION", result["ticket_proposal"]["status"])
        # Cancellation means do not invoke /chat/tickets/confirm. No write tool.
        self.send(first={"text": "Cancelado"}, body={"message": "Cancelar", "new_session": True}, expected=0)
        self.create.assert_not_called()

    def test_I29_tampered_assertion(self):
        def tamper(context, request, deadline):
            envelope = self.sign(context, request, deadline)
            parts = envelope["context_assertion"].split(".")
            parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
            envelope["context_assertion"] = ".".join(parts)
            return envelope
        self.port.assertion_issuer = tamper
        self.send(expected=0)
        self.assertIsNone(self.data())

    def test_I30_model_context_cannot_select_identity_or_transport(self):
        self.send(body={"message": "Consulta", "new_session": True, "user_id": 92003,
            "role": "SUPERADMIN", "effective_tenant_id": 91002, "transport": "current"},
            first={"actor_user_id": 92003, "tenant_id": 91002, "role": "SUPERADMIN", "transport": "current",
                "tool_request": {"tool_name": "xoc.tickets.list", "arguments": {}}, "continuation": "opaque"})
        context = self.records[-1][0]
        self.assertEqual((91001, 91001, 92001, "ADMIN", False), (context.tenant_id, context.effective_tenant_id,
            context.user_id, context.role, context.delegation_active))
        self.assertTrue(all("-a" in t["ticket_id"] for t in self.data()["tickets"]))

    def test_redirect_is_blocked_before_sdk_replay(self):
        import httpx
        seen = []
        async def redirect(_, request):
            seen.append(str(request.url))  # endpoint only, never headers/assertion
            return httpx.Response(307, headers={"Location": "https://external.invalid/mcp"}, request=request)
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", redirect):
            self.send(expected=0)
        self.assertEqual([SERVER.url], seen)
        self.assertIsNone(self.data())

    def test_mcp_config_cannot_use_legacy_flag_false(self):
        with patch.object(d, "enabled", return_value=False), self.assertRaises(AppError):
            self.send(expected=0)
        self.assertEqual([], self.calls)
        self.assertEqual([], self.records)

    def test_deadline_after_runtime1_prevents_tool(self):
        # Advance only the handler's monotonic clock; do not change any timeout.
        from types import SimpleNamespace
        with patch.object(chat, "time", SimpleNamespace(monotonic=lambda: time.monotonic()-30)):
            self.send(expected=0)
        self.assertEqual([], self.records)

    def test_signed_deadline_does_not_extend_budget(self):
        self.send()
        context, request, deadline = self.records[-1]
        self.assertEqual(context.request_id, request.request_id)
        self.assertLessEqual(deadline, int(time.time()+3.5))


if __name__ == "__main__":
    raise SystemExit("Use installed IC-1 run.py; requires real supervised server")
