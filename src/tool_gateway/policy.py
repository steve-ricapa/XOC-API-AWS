"""Deterministic authorization policy for internal AI tools."""
from __future__ import annotations

from collections.abc import Mapping

from src.shared.context import normalize_role
from src.tool_gateway.registry import get_tool_definition
from src.tool_gateway.schemas import (
    ToolAccessLevel,
    ToolContext,
    ToolDecision,
    ToolDefinition,
    ToolPolicyDecision,
    ToolRequest,
)


_KNOWN_ROLES = frozenset({"USER", "ADMIN", "ADMIN_XOC", "SUPERADMIN"})
_OWNERSHIP_ARGUMENTS = frozenset(
    {
        "tenantid",
        "tenant_id",
        "effectivetenantid",
        "effective_tenant_id",
        "userid",
        "user_id",
    }
)


def _deny(reason: str, code: str) -> ToolPolicyDecision:
    return ToolPolicyDecision(ToolDecision.DENIED, reason, code)


def _has_ownership_argument(arguments: Mapping[str, object]) -> bool:
    return any(str(key).strip().lower() in _OWNERSHIP_ARGUMENTS for key in arguments)


def evaluate_tool_request(
    context: ToolContext,
    request: ToolRequest,
    *,
    definition: ToolDefinition | None = None,
) -> ToolPolicyDecision:
    """Authorize a request independently from model instructions or input text."""
    if not isinstance(request.arguments, Mapping):
        return _deny("Tool arguments must be an object", "invalid_arguments")
    if _has_ownership_argument(request.arguments):
        return _deny("Tenant and user ownership are resolved from authenticated context", "ownership_argument_forbidden")

    definition = definition or get_tool_definition(request.tool_name)
    if definition is None:
        return _deny("Tool is not registered", "unknown_tool")

    role = normalize_role(context.role)
    if role not in _KNOWN_ROLES:
        return _deny("Authenticated role is missing or invalid", "invalid_role")
    if not context.effective_tenant_id or not context.user_id:
        return _deny("Authenticated tenant or user context is missing", "invalid_context")
    if role == "SUPERADMIN":
        return _deny("AI tools are disabled for SUPERADMIN", "superadmin_ai_blocked")

    if definition.access_level is ToolAccessLevel.DESTRUCTIVE_BLOCKED:
        return _deny("Destructive tools are blocked for AI", "destructive_tool_blocked")
    if definition.access_level is ToolAccessLevel.WRITE_REQUIRES_APPROVAL:
        return ToolPolicyDecision(
            ToolDecision.NEEDS_APPROVAL,
            "This operation requires an explicit human approval flow",
            "approval_required",
        )
    if definition.access_level is ToolAccessLevel.WRITE_SAFE:
        return _deny("Write tools are not enabled during Fase B", "write_tools_disabled")
    if not definition.enabled:
        return _deny("Tool is disabled", "tool_disabled")
    if definition.access_level is not ToolAccessLevel.READ_ONLY:
        return _deny("Unsupported tool access level", "unsupported_access_level")
    if role not in definition.allowed_roles:
        return _deny("Role is not allowed to use this tool", "role_forbidden")
    if role == "ADMIN_XOC" and definition.requires_delegation_for_admin_xoc:
        if not context.delegation_active or not context.effective_tenant_id:
            return _deny("ADMIN_XOC requires an active delegated tenant context", "delegation_required")

    return ToolPolicyDecision(ToolDecision.ALLOWED, "Tool is allowed", "allowed")
