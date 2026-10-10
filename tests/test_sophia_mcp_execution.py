"""IC-1 seam/result validation, no optional SDK, real credentials or network."""
import asyncio
from dataclasses import replace
import json
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.shared import sophia_tool_chat as d
from src.shared.sophia_mcp_client import LocalMCPExecution, local_endpoint, validate_result
from src.shared.sophia_tool_execution import local_execution, selected_local_port
from src.tool_gateway.schemas import ToolContext, ToolRequest, ToolResult, ToolDecision

CTX = ToolContext(91001, 91001, 92001, "ADMIN", False, "lab-unit", "sophia_chat")
REQ = ToolRequest("xoc.tickets.list", {}, CTX.request_id)


class MCPExecutionTests(unittest.TestCase):
    def test_only_explicit_loopback(self):
        for url in ("https://127.0.0.1:1/mcp", "http://localhost:1/mcp", "http://0.0.0.0:1/mcp",
                    "http://example.invalid:1/mcp", "http://127.0.0.1/mcp", "http://127.0.0.1:1/mcp?x=1",
                    "http://u:p@127.0.0.1:1/mcp", "http://127.0.0.1:1/mcp#x", "http://127.0.0.1:1/other"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_endpoint(url)
        self.assertEqual("http://127.0.0.1:1234/mcp", local_endpoint("http://127.0.0.1:1234/mcp"))

    def test_context_selection_restored_and_isolated(self):
        self.assertIsNone(selected_local_port())
        port = object()
        with local_execution(port):
            self.assertIs(selected_local_port(), port)
            with local_execution(object()):
                self.assertIsNot(selected_local_port(), port)
            self.assertIs(selected_local_port(), port)
        self.assertIsNone(selected_local_port())

    def test_local_config_not_cloud(self):
        with patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "fixture"}):
            with self.assertRaises(RuntimeError), local_execution(object()):
                self.fail("must not enter")

    def test_no_budget_or_invalid_context_never_signs(self):
        signer = MagicMock()
        port = LocalMCPExecution("http://127.0.0.1:1234/mcp", signer)
        for deadline in (None, time.monotonic()-.1, time.monotonic()+.1, float("nan")):
            self.assertEqual(ToolDecision.DENIED, port.execute(CTX, REQ, deadline=deadline).status)
        port.execute(replace(CTX, source="voice"), REQ, deadline=time.monotonic()+4)
        signer.assert_not_called()

    def test_port_exception_never_local_fallback(self):
        port = MagicMock()
        port.execute.side_effect = RuntimeError("SECRET")
        second = MagicMock(return_value={"protocol": d.PROTOCOL, "request_id": CTX.request_id,
                                        "thread_id": "thread", "text": "No disponible"})
        with local_execution(port), patch.object(d, "ToolExecutor") as factory:
            result = d.finish_turn({"protocol": d.PROTOCOL, "request_id": CTX.request_id,
                "thread_id": "thread", "continuation": "opaque", "tool_request": {"tool_name": REQ.tool_name}},
                context=CTX, cleaner=lambda p: p, validate_thread=lambda _: None, post_result=second,
                deadline=time.monotonic()+3)
            factory.assert_not_called()
        self.assertEqual(1, port.execute.call_count)
        self.assertEqual(1, second.call_count)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_wire_shape_and_binding_fail_closed(self):
        base = dict(request_id=CTX.request_id, tool_name=REQ.tool_name, status="allowed", code="allowed",
                    data={"tickets": []}, audit_id="a"*24)
        def wire(p, error=False):
            return SimpleNamespace(structuredContent=p, isError=error,
                content=[SimpleNamespace(type="text", text=json.dumps(p))])
        self.assertEqual(ToolDecision.ALLOWED, validate_result(wire(base), CTX, REQ).status)
        for change in ({"request_id": "lab-other"}, {"tool_name": "xoc.documents.list"}, {"audit_id": None},
                       {"status": "unknown"}, {"extra": True}, {"code": "SECRET token=foo"}, {"data": []}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                validate_result(wire(dict(base, **change)), CTX, REQ)
        denied = dict(base, status="denied", code="denied")
        with self.assertRaises(ValueError):
            validate_result(wire(denied, True), CTX, REQ)  # denied cannot carry data
        mismatched = wire(base)
        mismatched.content[0].text = "{}"
        with self.assertRaises(ValueError):
            validate_result(mismatched, CTX, REQ)


if __name__ == "__main__":
    unittest.main()
