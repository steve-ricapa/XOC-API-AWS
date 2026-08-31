"""Unit coverage for policy-first tool execution without AWS dependencies."""
from __future__ import annotations

import unittest

from src.tool_gateway.executor import ToolExecutor
from src.tool_gateway.schemas import (
    ToolAccessLevel,
    ToolContext,
    ToolDecision,
    ToolDefinition,
    ToolRequest,
    ToolRiskLevel,
)


def _context(role: str = "USER") -> ToolContext:
    return ToolContext(
        tenant_id=7,
        effective_tenant_id=7,
        user_id=12,
        role=role,
        request_id="executor-test",
        source="internal",
    )


def _read_definition(*, enabled: bool = True, handler_name: str = "dummy") -> ToolDefinition:
    return ToolDefinition(
        name="xoc.test.read",
        description="test only",
        access_level=ToolAccessLevel.READ_ONLY,
        risk_level=ToolRiskLevel.LOW,
        allowed_roles=frozenset({"USER", "ADMIN"}),
        handler_name=handler_name,
        enabled=enabled,
    )


class ToolGatewayExecutorTests(unittest.TestCase):
    def test_unknown_tool_never_executes(self) -> None:
        called = False

        def handler(_context, _arguments):
            nonlocal called
            called = True
            return {}

        result = ToolExecutor(handlers={"dummy": handler}).execute(
            _context(), ToolRequest("xoc.unknown")
        )
        self.assertEqual(ToolDecision.DENIED, result.status)
        self.assertEqual("unknown_tool", result.code)
        self.assertFalse(called)

    def test_denied_tool_never_executes(self) -> None:
        called = False

        def handler(_context, _arguments):
            nonlocal called
            called = True
            return {}

        definition = _read_definition(enabled=False)
        result = ToolExecutor(registry={definition.name: definition}, handlers={"dummy": handler}).execute(
            _context(), ToolRequest(definition.name)
        )
        self.assertEqual("tool_disabled", result.code)
        self.assertFalse(called)

    def test_needs_approval_never_executes(self) -> None:
        called = False

        def handler(_context, _arguments):
            nonlocal called
            called = True
            return {}

        definition = ToolDefinition(
            name="xoc.test.approval",
            description="test only",
            access_level=ToolAccessLevel.WRITE_REQUIRES_APPROVAL,
            risk_level=ToolRiskLevel.HIGH,
            allowed_roles=frozenset({"ADMIN"}),
            handler_name="dummy",
        )
        result = ToolExecutor(registry={definition.name: definition}, handlers={"dummy": handler}).execute(
            _context("ADMIN"), ToolRequest(definition.name)
        )
        self.assertEqual(ToolDecision.NEEDS_APPROVAL, result.status)
        self.assertFalse(called)

    def test_allowed_read_only_handler_executes_with_context_not_arguments(self) -> None:
        definition = _read_definition()

        def handler(context, arguments):
            return {"tenant": context.effective_tenant_id, "user": context.user_id, "arguments": dict(arguments)}

        result = ToolExecutor(registry={definition.name: definition}, handlers={"dummy": handler}).execute(
            _context(), ToolRequest(definition.name, arguments={"limit": 5})
        )
        self.assertEqual(ToolDecision.ALLOWED, result.status)
        self.assertEqual(7, result.data["tenant"])
        self.assertEqual(12, result.data["user"])
        self.assertEqual({"limit": 5}, result.data["arguments"])

    def test_handler_failure_is_safe(self) -> None:
        definition = _read_definition()

        def handler(_context, _arguments):
            raise RuntimeError("secret endpoint value must not escape")

        result = ToolExecutor(registry={definition.name: definition}, handlers={"dummy": handler}).execute(
            _context(), ToolRequest(definition.name)
        )
        self.assertEqual(ToolDecision.DENIED, result.status)
        self.assertEqual("execution_failed", result.code)
        self.assertEqual("Tool execution failed", result.error)
        self.assertNotIn("secret", result.error.lower())

    def test_ownership_arguments_are_rejected_before_handler(self) -> None:
        definition = _read_definition()
        called = False

        def handler(_context, _arguments):
            nonlocal called
            called = True
            return {}

        result = ToolExecutor(registry={definition.name: definition}, handlers={"dummy": handler}).execute(
            _context(), ToolRequest(definition.name, arguments={"tenantId": 999})
        )
        self.assertEqual("ownership_argument_forbidden", result.code)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
