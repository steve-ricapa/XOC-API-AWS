"""Focused regression checks for governed SOPHIA Chat integration."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.handlers.routes import chat
from src.shared.errors import ForbiddenError


class _User:
    def __init__(self, role: str = "ADMIN") -> None:
        self.id = 12
        self.role = role
        self.tenant_id = 7
        self.effective_tenant_id = 7
        self.delegation_active = False


class ChatToolGatewayIntegrationTests(unittest.TestCase):
    @patch.object(chat, "get_jwt_secret_key", return_value="test-secret")
    def test_action_plan_becomes_confirmation_proposal_not_ticket_write(self, _secret) -> None:
        payload = {"action_plan": {"subject": "Revisar VPN", "description": "Solo diagnostico", "severity": "high"}}
        result = chat._maybe_create_ticket_from_action_plan(payload, 7, _User(), "request-1")

        self.assertNotIn("ticket_created", result)
        self.assertNotIn("ticket_id", result)
        self.assertEqual("NEEDS_CONFIRMATION", result["ticket_proposal"]["status"])
        self.assertTrue(result["ticket_proposal"]["confirmation_token"])

    def test_external_tool_request_is_denied_by_gateway_when_unknown(self) -> None:
        payload = {"tool_request": {"tool_name": "xoc.delete_everything", "arguments": {}}}
        result = chat._maybe_execute_chat_tool_request(payload, _User(), 7, "request-2")

        self.assertNotIn("tool_request", result)
        self.assertEqual("denied", result["metadata"]["toolGateway"]["status"])
        self.assertEqual("unknown_tool", result["metadata"]["toolGateway"]["code"])

    def test_unprivileged_user_receives_proposal_without_confirmation_token(self) -> None:
        result = chat._maybe_create_ticket_from_action_plan(
            {"action_plan": {"subject": "Revisar VPN"}}, 7, _User("USER"), "request-3"
        )
        self.assertEqual("NEEDS_CONFIRMATION", result["ticket_proposal"]["status"])
        self.assertNotIn("confirmation_token", result["ticket_proposal"])

    @patch.object(chat, "_create_ticket_from_confirmed_proposal")
    @patch.object(chat, "get_jwt_secret_key", return_value="test-secret")
    def test_confirmed_proposal_creates_ticket_for_same_authenticated_actor(self, _secret, create_ticket) -> None:
        create_ticket.return_value = {"ticket_id": "ticket-123", "ticket": {"id": "ticket-123"}}
        user = _User()
        token = chat._ticket_confirmation_token(
            action_plan={"subject": "Revisar VPN", "description": "Diagnostico", "severity": "high"},
            tenant_id=7,
            current_user=user,
            request_id="request-4",
        )

        result = chat.confirm_chat_ticket_proposal({"confirmation_token": token}, user)

        self.assertTrue(result["ticket_created"])
        self.assertEqual("ticket-123", result["ticket_id"])
        create_ticket.assert_called_once()
        self.assertEqual(7, create_ticket.call_args.kwargs["tenant_id"])
        self.assertEqual(12, create_ticket.call_args.kwargs["user_id"])

    @patch.object(chat, "get_jwt_secret_key", return_value="test-secret")
    def test_unprivileged_user_cannot_confirm_ticket_proposal(self, _secret) -> None:
        token = chat._ticket_confirmation_token(
            action_plan={"subject": "Revisar VPN"},
            tenant_id=7,
            current_user=_User(),
            request_id="request-5",
        )

        with self.assertRaises(ForbiddenError):
            chat.confirm_chat_ticket_proposal({"confirmation_token": token}, _User("USER"))


if __name__ == "__main__":
    unittest.main()
