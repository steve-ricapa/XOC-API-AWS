"""Authorization policy for authenticated document generation requests.

This policy is intentionally enforced by the backend, independently from any
mobile/web presentation rules.  It does not resolve users or tenants from RDS;
the route supplies the authenticated role and effective tenant context.
"""
from __future__ import annotations

from typing import Any

from src.reports.schemas import DOCUMENT_TYPES
from src.shared.context import normalize_role
from src.shared.errors import ForbiddenError, ValidationError


# Product policy for the currently available advanced report types.  New report
# types must be added here deliberately; they must never inherit a broad role
# allowlist by default.
_REPORT_TYPES_BY_ROLE: dict[str, frozenset[str]] = {
    "ADMIN": frozenset({"minority_report"}),
    "ADMIN_XOC": frozenset({"small_report"}),
    "USER": frozenset(),
    "SUPERADMIN": frozenset(),
}


def get_allowed_report_types_for_role(role: str | None, context: dict[str, Any] | None = None) -> frozenset[str]:
    """Return the explicit report types granted to an authenticated role.

    ``context`` is accepted so callers can pass authenticated/delegated context
    without allowing client-controlled tenant or user fields to change policy.
    Delegation is enforced by the route's existing effective-tenant checks.
    """
    del context
    return _REPORT_TYPES_BY_ROLE.get(normalize_role(role), frozenset())


def validate_report_type_for_role(
    report_type: str | None,
    role: str | None,
    auth_context: dict[str, Any] | None = None,
) -> frozenset[str]:
    """Validate an authenticated request before any document side effect."""
    normalized_type = str(report_type or "").strip().lower()
    if not normalized_type:
        raise ValidationError("document_type is required")
    if normalized_type not in DOCUMENT_TYPES:
        raise ValidationError("document_type is not supported")

    normalized_role = normalize_role(role)
    allowed_types = get_allowed_report_types_for_role(normalized_role, auth_context)
    if normalized_type not in allowed_types:
        raise ForbiddenError("User role is not allowed to request this document type")
    return allowed_types
