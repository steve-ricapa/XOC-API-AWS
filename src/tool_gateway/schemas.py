"""Small, dependency-free contracts for the XOC internal tool gateway."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping


class ToolRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ToolAccessLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    WRITE_SAFE = "WRITE_SAFE"
    WRITE_REQUIRES_APPROVAL = "WRITE_REQUIRES_APPROVAL"
    DESTRUCTIVE_BLOCKED = "DESTRUCTIVE_BLOCKED"


class ToolDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class ToolContext:
    """Trusted identity resolved from XOC authentication, never tool input."""

    tenant_id: int | None
    effective_tenant_id: int | None
    user_id: int | None
    role: str | None
    delegation_active: bool = False
    request_id: str | None = None
    source: str = "internal"


@dataclass(frozen=True)
class ToolRequest:
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    request_id: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    access_level: ToolAccessLevel
    risk_level: ToolRiskLevel
    allowed_roles: frozenset[str]
    requires_delegation_for_admin_xoc: bool = True
    handler_name: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class ToolPolicyDecision:
    decision: ToolDecision
    reason: str
    code: str

    @property
    def allowed(self) -> bool:
        return self.decision is ToolDecision.ALLOWED


@dataclass(frozen=True)
class ToolResult:
    status: ToolDecision
    data: dict[str, Any] | None = None
    error: str | None = None
    code: str | None = None
    audit_id: str | None = None


ToolHandler = Callable[[ToolContext, Mapping[str, Any]], dict[str, Any]]
