from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.models import TenantPreference
from src.shared.errors import ValidationError

VALID_VULNERABILITY_SEVERITIES = (
    "critical",
    "high",
    "medium",
    "low",
    "informational",
)

VALID_PROVIDERS = (
    "wazuh",
    "zabbix",
    "uptime_kuma",
    "nessus",
    "openvas",
    "insightvm",
)

DEFAULT_TENANT_PREFERENCES: dict[str, Any] = {
    "dashboard": {
        "preventions": {
            "visibleVulnerabilitySeverities": ["critical", "high", "medium", "low", "informational"],
            "visibleProviders": list(VALID_PROVIDERS),
            "healthIndexMode": "weighted_visible_with_residual_penalty",
        },
        "integrations": {
            "visibleProviders": list(VALID_PROVIDERS),
            "hideUnconfiguredProviders": True,
        },
    }
}

VISIBLE_VULNERABILITY_WEIGHTS = {
    "critical": 1.4,
    "high": 0.55,
    "medium": 0.14,
    "low": 0.04,
    "informational": 0.01,
}

HIDDEN_VULNERABILITY_WEIGHTS = {
    "critical": 0.45,
    "high": 0.18,
    "medium": 0.05,
    "low": 0.015,
    "informational": 0.003,
}

WAZUH_ALERT_WEIGHTS = {
    "critical": 0.18,
    "high": 0.08,
    "medium": 0.025,
    "low": 0.01,
    "informational": 0.002,
}


def default_tenant_preferences() -> dict[str, Any]:
    return deepcopy(DEFAULT_TENANT_PREFERENCES)


def _normalize_severity(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "info":
        return "informational"
    return normalized


def _normalize_string_list(values: Any, *, allowed: tuple[str, ...], field_name: str, transform=None) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValidationError(f"{field_name} must be a list")
    result: list[str] = []
    allowed_set = set(allowed)
    for raw in values:
        value = transform(raw) if transform else str(raw or "").strip().lower()
        if value not in allowed_set:
            raise ValidationError(f"Invalid value in {field_name}: {raw}")
        if value not in result:
            result.append(value)
    return result


def normalize_tenant_preferences(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return default_tenant_preferences()
    if not isinstance(payload, dict):
        raise ValidationError("Preferences payload must be an object")

    preferences = default_tenant_preferences()
    dashboard = payload.get("dashboard")
    if dashboard is not None and not isinstance(dashboard, dict):
        raise ValidationError("dashboard must be an object")
    dashboard = dashboard or {}

    preventions = dashboard.get("preventions")
    if preventions is not None and not isinstance(preventions, dict):
        raise ValidationError("dashboard.preventions must be an object")
    preventions = preventions or {}

    integrations = dashboard.get("integrations")
    if integrations is not None and not isinstance(integrations, dict):
        raise ValidationError("dashboard.integrations must be an object")
    integrations = integrations or {}

    if "visibleVulnerabilitySeverities" in preventions:
        preferences["dashboard"]["preventions"]["visibleVulnerabilitySeverities"] = _normalize_string_list(
            preventions.get("visibleVulnerabilitySeverities"),
            allowed=VALID_VULNERABILITY_SEVERITIES,
            field_name="dashboard.preventions.visibleVulnerabilitySeverities",
            transform=_normalize_severity,
        )

    if "visibleProviders" in preventions:
        preferences["dashboard"]["preventions"]["visibleProviders"] = _normalize_string_list(
            preventions.get("visibleProviders"),
            allowed=VALID_PROVIDERS,
            field_name="dashboard.preventions.visibleProviders",
        )

    if "healthIndexMode" in preventions:
        health_index_mode = str(preventions.get("healthIndexMode") or "").strip()
        if health_index_mode != "weighted_visible_with_residual_penalty":
            raise ValidationError("Unsupported dashboard.preventions.healthIndexMode")
        preferences["dashboard"]["preventions"]["healthIndexMode"] = health_index_mode

    if "visibleProviders" in integrations:
        preferences["dashboard"]["integrations"]["visibleProviders"] = _normalize_string_list(
            integrations.get("visibleProviders"),
            allowed=VALID_PROVIDERS,
            field_name="dashboard.integrations.visibleProviders",
        )

    if "hideUnconfiguredProviders" in integrations:
        preferences["dashboard"]["integrations"]["hideUnconfiguredProviders"] = bool(integrations.get("hideUnconfiguredProviders"))

    return preferences


def merge_tenant_preferences(base: dict[str, Any] | None, patch: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_tenant_preferences()

    def _merge_into(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                _merge_into(target[key], value)
            else:
                target[key] = deepcopy(value)

    if isinstance(base, dict):
        _merge_into(merged, base)
    if isinstance(patch, dict):
        _merge_into(merged, patch)
    return normalize_tenant_preferences(merged)


def get_tenant_preferences_record(session: Session, tenant_id: int) -> TenantPreference | None:
    return session.scalar(select(TenantPreference).where(TenantPreference.tenant_id == tenant_id))


def get_tenant_preferences(session: Session, tenant_id: int) -> dict[str, Any]:
    record = get_tenant_preferences_record(session, tenant_id)
    return merge_tenant_preferences(record.dashboard_preferences if record else None, None)


def get_visible_vulnerability_severities(preferences: dict[str, Any]) -> list[str]:
    return list((preferences.get("dashboard") or {}).get("preventions", {}).get("visibleVulnerabilitySeverities") or [])


def get_visible_prevention_providers(preferences: dict[str, Any]) -> list[str]:
    return list((preferences.get("dashboard") or {}).get("preventions", {}).get("visibleProviders") or [])


def get_visible_integration_providers(preferences: dict[str, Any]) -> list[str]:
    return list((preferences.get("dashboard") or {}).get("integrations", {}).get("visibleProviders") or [])


def hide_unconfigured_providers(preferences: dict[str, Any]) -> bool:
    return bool((preferences.get("dashboard") or {}).get("integrations", {}).get("hideUnconfiguredProviders", True))


def calculate_health_index(*, vulnerability_counts: dict[str, int], visible_severities: list[str], wazuh_alerts: dict[str, Any] | None, zabbix_alerts: int, uptime_down: int) -> int:
    visible = set(visible_severities)
    visible_penalty = 0.0
    hidden_penalty = 0.0
    for severity in VALID_VULNERABILITY_SEVERITIES:
        count = int(vulnerability_counts.get(severity, 0) or 0)
        if severity in visible:
            visible_penalty += count * VISIBLE_VULNERABILITY_WEIGHTS[severity]
        else:
            hidden_penalty += count * HIDDEN_VULNERABILITY_WEIGHTS[severity]

    visible_penalty = min(42.0, visible_penalty)
    hidden_penalty = min(10.0, hidden_penalty)

    wazuh = wazuh_alerts or {}
    operational_penalty = 0.0
    for severity, weight in WAZUH_ALERT_WEIGHTS.items():
        if severity == "informational":
            count = int(wazuh.get("info", 0) or wazuh.get("informational", 0) or 0)
        else:
            count = int(wazuh.get(severity, 0) or 0)
        operational_penalty += count * weight

    operational_penalty += int(zabbix_alerts or 0) * 0.03
    operational_penalty += int(uptime_down or 0) * 0.6
    operational_penalty = min(18.0, operational_penalty)

    penalty = visible_penalty + hidden_penalty + operational_penalty
    return max(0, min(100, int(round(100 - penalty))))
