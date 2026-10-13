import unittest
from unittest.mock import Mock, patch
import requests
from src.shared import sophia_tool_chat as chat
from src.handlers.routes.chat import _clean_agent_response
from src.tool_gateway.schemas import ToolContext, ToolResult, ToolDecision

CTX = ToolContext(7, 7, 12, "ADMIN", False, "fixture", "sophia_chat")


class DirectSummaryTests(unittest.TestCase):
    def run_result(self, result, tool="xoc.tickets.list", error=None):
        execution = Mock()
        execution.execute.return_value = result
        post = Mock(side_effect=error or requests.Timeout("PRIVATE"))
        body = dict(protocol=chat.PROTOCOL, request_id=CTX.request_id,
                    thread_id="thread_fixture", continuation="opaque",
                    tool_request=dict(tool_name=tool, arguments={}))
        with patch("src.shared.sophia_tool_execution.selected_execution", return_value=execution):
            reply = chat.finish_turn(body, context=CTX, cleaner=_clean_agent_response,
                                     validate_thread=lambda _: None, post_result=post)
        execution.execute.assert_called_once()
        post.assert_called_once()
        self.assertNotIn("PRIVATE", str(reply))
        self.assertNotIn("ticket_proposal", reply)
        return reply

    def test_allowed_timeout_presents_only_projection(self):
        result = ToolResult(ToolDecision.ALLOWED, code="allowed", audit_id="audit",
                            data={"tickets": [{"ticket_id": "t1", "tenant_id": 7,
                                               "subject": "VPN", "status": "PENDING",
                                               "description": "PRIVATE", "access_token": "PRIVATE"}]})
        reply = self.run_result(result)
        self.assertIn("VPN", reply["text"])
        self.assertIn("PENDING", reply["text"])
        meta = reply["metadata"]["toolGateway"]
        self.assertEqual("direct_authorized_summary", meta["presentation"])
        self.assertEqual("runtime_unavailable", meta["completionCode"])
        self.assertEqual("audit", meta["auditId"])

    def test_denied_or_foreign_or_invalid_never_present(self):
        for result in (
            ToolResult(ToolDecision.DENIED, code="role_not_allowed", data={"tickets": [{"subject": "PRIVATE"}]}),
            ToolResult(ToolDecision.ALLOWED, data={"tickets": [{"tenant_id": 8, "subject": "PRIVATE"}]}),
            ToolResult(ToolDecision.ALLOWED, data={"tickets": "PRIVATE"}),
        ):
            reply = self.run_result(result)
            self.assertNotIn("presentation", reply["metadata"]["toolGateway"])

    def test_protocol_failure_not_hidden(self):
        reply = self.run_result(ToolResult(ToolDecision.ALLOWED, data={"tickets": []}),
                                error=chat.protocol_error("sophia_thread_mismatch"))
        self.assertNotIn("presentation", reply["metadata"]["toolGateway"])

    def test_four_tools_and_empty(self):
        for tool, data, expected in (
            ("xoc.tickets.list", {"tickets": []}, "no devolvio"),
            ("xoc.tickets.get", {"ticket": {"subject": "VPN"}}, "VPN"),
            ("xoc.documents.list", {"documents": [{"document_type": "report", "created_at": "2026"}]}, "2026"),
            ("xoc.notifications.unread_count", {"count": 0}, "0"),
        ):
            reply = self.run_result(ToolResult(ToolDecision.ALLOWED, data=data), tool)
            self.assertIn(expected, reply["text"])

    def test_bounded_escaped_and_redacted(self):
        row = {"subject": "<script>[click](https://example.invalid) password=PRIVATE"}
        result = chat.model_result(ToolResult(ToolDecision.ALLOWED, data={"tickets": [row] * 60}),
                                   "xoc.tickets.list", CTX)
        text = chat._direct_summary(result)
        self.assertNotIn("<script>", text)
        self.assertNotIn("[click](", text)
        self.assertNotIn("PRIVATE", text)
        self.assertIn("solo una parte", text)
        self.assertLess(len(text), 8000)
