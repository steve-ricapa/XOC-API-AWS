"""Unit coverage for deterministic XOC AI tool policy decisions."""
from __future__ import annotations

import unittest

from src.tool_gateway.policy import evaluate_tool_request
from src.tool_gateway.registry import get_tool_definition
from src.tool_gateway.schemas import (
    ToolAccessLevel,
    ToolContext,
    ToolDecision,
    ToolDefinition,
    ToolRequest,
    ToolRiskLevel,
)


def _context(
    role: str | None = "USER",
    *,
    delegated: bool = False,
    effective_tenant_id: int | None = 7,
) -> ToolContext:
    return ToolContext(
        tenant_id=7,
        effective_tenant_id=effective_tenant_id,
        user_id=12,
        role=role,
        delegation_active=delegated,
        request_id="test-request",
        source="internal",
    )


class ToolGatewayPolicyTests(unittest.TestCase):
    def test_unknown_tool_is_denied(self) -> None:
        decision = evaluate_tool_request(_context(), ToolRequest("xoc.unknown"))
        self.assertEqual(ToolDecision.DENIED, decision.decision)
        self.assertEqual("unknown_tool", decision.code)

    def test_disabled_tool_is_denied(self) -> None:
        decision = evaluate_tool_request(_context(), ToolRequest("xoc.dashboard.summary"))
        self.assertEqual(ToolDecision.DENIED, decision.decision)
        self.assertEqual("tool_disabled", decision.code)

    def test_missing_or_invalid_role_is_denied(self) -> None:
        for role in (None, "", "OPERATOR", "MODEL"):
            with self.subTest(role=role):
                decision = evaluate_tool_request(_context(role), ToolRequest("xoc.tickets.list"))
                self.assertEqual(ToolDecision.DENIED, decision.decision)
                self.assertEqual("invalid_role", decision.code)

    def test_user_and_admin_can_use_allowed_read_only_tool(self) -> None:
        for role in ("USER", "ADMIN"):
            with self.subTest(role=role):
                decision = evaluate_tool_request(_context(role), ToolRequest("xoc.tickets.list"))
                self.assertEqual(ToolDecision.ALLOWED, decision.decision)

    def test_admin_xoc_requires_delegation(self) -> None:
        denied = evaluate_tool_request(_context("ADMIN_XOC"), ToolRequest("xoc.tickets.list"))
        allowed = evaluate_tool_request(
            _context("ADMIN_XOC", delegated=True), ToolRequest("xoc.tickets.list")
        )
        self.assertEqual(ToolDecision.DENIED, denied.decision)
        self.assertEqual("delegation_required", denied.code)
        self.assertEqual(ToolDecision.ALLOWED, allowed.decision)

    def test_superadmin_is_blocked_by_default(self) -> None:
        decision = evaluate_tool_request(_context("SUPERADMIN", delegated=True), ToolRequest("xoc.tickets.list"))
        self.assertEqual(ToolDecision.DENIED, decision.decision)
        self.assertEqual("superadmin_ai_blocked", decision.code)

    def test_context_ownership_cannot_be_supplied_as_arguments(self) -> None:
        for key in ("tenantId", "tenant_id", "userId", "user_id", "effectiveTenantId"):
            with self.subTest(key=key):
                decision = evaluate_tool_request(
                    _context(), ToolRequest("xoc.tickets.list", arguments={key: "other"})
                )
                self.assertEqual(ToolDecision.DENIED, decision.decision)
                self.assertEqual("ownership_argument_forbidden", decision.code)

    def test_destructive_is_always_denied(self) -> None:
        destructive = ToolDefinition(
            name="xoc.test.delete",
            description="test only",
            access_level=ToolAccessLevel.DESTRUCTIVE_BLOCKED,
            risk_level=ToolRiskLevel.CRITICAL,
            allowed_roles=frozenset({"ADMIN"}),
            enabled=True,
        )
        decision = evaluate_tool_request(
            _context("ADMIN"), ToolRequest(destructive.name), definition=destructive
        )
        self.assertEqual(ToolDecision.DENIED, decision.decision)
        self.assertEqual("destructive_tool_blocked", decision.code)

    def test_write_tools_are_never_allowed_in_fase_b(self) -> None:
        write_safe = ToolDefinition(
            name="xoc.test.write",
            description="test only",
            access_level=ToolAccessLevel.WRITE_SAFE,
            risk_level=ToolRiskLevel.MEDIUM,
            allowed_roles=frozenset({"ADMIN"}),
        )
        approval = ToolDefinition(
            name="xoc.test.approval",
            description="test only",
            access_level=ToolAccessLevel.WRITE_REQUIRES_APPROVAL,
            risk_level=ToolRiskLevel.HIGH,
            allowed_roles=frozenset({"ADMIN"}),
        )
        for role in ("USER", "ADMIN"):
            with self.subTest(role=role, access="write_safe"):
                decision = evaluate_tool_request(_context(role), ToolRequest(write_safe.name), definition=write_safe)
                self.assertEqual(ToolDecision.DENIED, decision.decision)
                self.assertEqual("write_tools_disabled", decision.code)
            with self.subTest(role=role, access="write_requires_approval"):
                decision = evaluate_tool_request(_context(role), ToolRequest(approval.name), definition=approval)
                self.assertEqual(ToolDecision.NEEDS_APPROVAL, decision.decision)
                self.assertEqual("approval_required", decision.code)

    def test_registered_read_tools_have_explicit_definitions(self) -> None:
        for tool_name in (
            "xoc.tickets.list",
            "xoc.tickets.get",
            "xoc.documents.list",
            "xoc.notifications.unread_count",
        ):
            with self.subTest(tool_name=tool_name):
                definition = get_tool_definition(tool_name)
                self.assertIsNotNone(definition)
                self.assertEqual(ToolAccessLevel.READ_ONLY, definition.access_level)
                self.assertTrue(definition.enabled)


if __name__ == "__main__":
    unittest.main()
