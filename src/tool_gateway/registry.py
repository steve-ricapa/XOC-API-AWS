"""Explicit allowlist for XOC AI tools.

Definitions are deliberately local and static.  A model can request a name,
but it can never create a new executable tool at runtime.
"""
from __future__ import annotations

from src.tool_gateway.schemas import ToolAccessLevel, ToolDefinition, ToolRiskLevel


_TENANT_READ_ROLES = frozenset({"USER", "ADMIN", "ADMIN_XOC"})


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "xoc.tickets.list": ToolDefinition(
        name="xoc.tickets.list",
        description="List tickets belonging to the authenticated effective tenant.",
        access_level=ToolAccessLevel.READ_ONLY,
        risk_level=ToolRiskLevel.LOW,
        allowed_roles=_TENANT_READ_ROLES,
        handler_name="tickets.list",
    ),
    "xoc.tickets.get": ToolDefinition(
        name="xoc.tickets.get",
        description="Read one ticket only within the authenticated effective tenant.",
        access_level=ToolAccessLevel.READ_ONLY,
        risk_level=ToolRiskLevel.LOW,
        allowed_roles=_TENANT_READ_ROLES,
        handler_name="tickets.get",
    ),
    "xoc.documents.list": ToolDefinition(
        name="xoc.documents.list",
        description="List document jobs belonging to the authenticated effective tenant.",
        access_level=ToolAccessLevel.READ_ONLY,
        risk_level=ToolRiskLevel.LOW,
        allowed_roles=_TENANT_READ_ROLES,
        handler_name="documents.list",
    ),
    "xoc.notifications.unread_count": ToolDefinition(
        name="xoc.notifications.unread_count",
        description="Read the authenticated user's unread notification count.",
        access_level=ToolAccessLevel.READ_ONLY,
        risk_level=ToolRiskLevel.LOW,
        allowed_roles=_TENANT_READ_ROLES,
        handler_name="notifications.unread_count",
    ),
    # These concepts are intentionally visible in the registry, but disabled.
    # Their current helpers require a SQLAlchemy/RDS session.  Fase B must not
    # introduce new RDS access or SQL coupling into the gateway.
    "xoc.dashboard.summary": ToolDefinition(
        name="xoc.dashboard.summary",
        description="Reserved pending a read-only, non-RDS adapter.",
        access_level=ToolAccessLevel.READ_ONLY,
        risk_level=ToolRiskLevel.LOW,
        allowed_roles=_TENANT_READ_ROLES,
        handler_name=None,
        enabled=False,
    ),
    "xoc.integrations.summary": ToolDefinition(
        name="xoc.integrations.summary",
        description="Reserved pending a read-only, non-RDS adapter.",
        access_level=ToolAccessLevel.READ_ONLY,
        risk_level=ToolRiskLevel.MEDIUM,
        allowed_roles=_TENANT_READ_ROLES,
        handler_name=None,
        enabled=False,
    ),
}


def get_tool_definition(tool_name: str | None) -> ToolDefinition | None:
    return TOOL_REGISTRY.get(str(tool_name or "").strip())
