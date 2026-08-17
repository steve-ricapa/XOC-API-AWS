"""Canonical EventBridge notification events and safe domain-event builders."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import boto3


EVENT_SOURCE = "xoc.notifications"
EVENT_DETAIL_TYPE = "xoc.notification.requested"
EVENT_VERSION = 1
AUDIENCE_TYPES = {"SELF", "TENANT_ALL", "TENANT_ADMINS"}
PRIORITIES = {"low", "normal", "high", "critical"}
SUPPORTED_EVENT_TYPES = {
    "report.generated",
    "vulnerability.critical_detected",
    "attack.detected",
    "ticket.critical_created",
    "integration.down",
    "agent.disconnected",
    "sla.breached",
}
_FORBIDDEN_FIELD_NAMES = {
    "pushtoken",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "authorization",
    "authorizationheader",
    "privatekey",
}


class NotificationEventValidationError(ValueError):
    """Permanent event payload error; SQS should not retry this message."""


class NotificationEventPublishError(RuntimeError):
    """EventBridge did not accept the event; callers may retry safely."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_string(payload: dict[str, Any], field: str, *, max_length: int) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise NotificationEventValidationError(f"{field} is required")
    if len(value) > max_length:
        raise NotificationEventValidationError(f"{field} exceeds {max_length} characters")
    return value


def _optional_string(payload: dict[str, Any], field: str, *, max_length: int) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    normalized = str(value).strip()
    if len(normalized) > max_length:
        raise NotificationEventValidationError(f"{field} exceeds {max_length} characters")
    return normalized or None


def _assert_safe_payload(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).replace("-", "").replace("_", "").lower()
            if normalized_key in _FORBIDDEN_FIELD_NAMES:
                raise NotificationEventValidationError(f"{path or 'event'} contains a forbidden credential field")
            _assert_safe_payload(child, path=f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe_payload(child, path=f"{path}[{index}]")


def _safe_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise NotificationEventValidationError("metadata must be an object")
    _assert_safe_payload(value, path="metadata")
    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise NotificationEventValidationError("metadata must be JSON serializable") from exc
    if len(serialized) > 16_384:
        raise NotificationEventValidationError("metadata exceeds 16384 characters")
    return value


def normalize_notification_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the EventBridge detail used by the worker."""
    if not isinstance(payload, dict):
        raise NotificationEventValidationError("event detail must be an object")
    _assert_safe_payload(payload)

    version = payload.get("version")
    if version != EVENT_VERSION:
        raise NotificationEventValidationError("version must be 1")
    event_id = _required_string(payload, "eventId", max_length=128)
    event_type = _required_string(payload, "eventType", max_length=120)
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise NotificationEventValidationError("eventType is not supported")
    tenant_id = _required_string(payload, "tenantId", max_length=128)
    audience_type = _required_string(payload, "audienceType", max_length=32).upper()
    if audience_type not in AUDIENCE_TYPES:
        raise NotificationEventValidationError("audienceType must be SELF, TENANT_ALL, or TENANT_ADMINS")
    recipient_user_id = _optional_string(payload, "recipientUserId", max_length=128)
    if audience_type == "SELF" and not recipient_user_id:
        raise NotificationEventValidationError("recipientUserId is required for SELF events")

    priority = (_optional_string(payload, "priority", max_length=16) or "normal").lower()
    if priority not in PRIORITIES:
        raise NotificationEventValidationError("priority must be low, normal, high, or critical")
    resource_type = _optional_string(payload, "resourceType", max_length=120)
    resource_id = _optional_string(payload, "resourceId", max_length=256)
    dedupe_key = _optional_string(payload, "dedupeKey", max_length=512)
    if not dedupe_key:
        dedupe_key = ":".join((event_type, tenant_id, resource_id or event_id))

    return {
        "version": EVENT_VERSION,
        "eventId": event_id,
        "eventType": event_type,
        "tenantId": tenant_id,
        "audienceType": audience_type,
        "recipientUserId": recipient_user_id,
        "title": _required_string(payload, "title", max_length=200),
        "body": _required_string(payload, "body", max_length=2000),
        "deepLink": _optional_string(payload, "deepLink", max_length=2048),
        "priority": priority,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "dedupeKey": dedupe_key,
        "metadata": _safe_metadata(payload.get("metadata")),
        "createdAt": _optional_string(payload, "createdAt", max_length=64) or _now_iso(),
    }


def build_notification_event(
    *,
    event_type: str,
    tenant_id: str | int,
    audience_type: str,
    title: str,
    body: str,
    recipient_user_id: str | int | None = None,
    deep_link: str | None = None,
    priority: str = "normal",
    resource_type: str | None = None,
    resource_id: str | None = None,
    dedupe_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return normalize_notification_event(
        {
            "version": EVENT_VERSION,
            "eventId": f"evt-{uuid4()}",
            "eventType": event_type,
            "tenantId": str(tenant_id),
            "audienceType": audience_type,
            "recipientUserId": str(recipient_user_id) if recipient_user_id is not None else None,
            "title": title,
            "body": body,
            "deepLink": deep_link,
            "priority": priority,
            "resourceType": resource_type,
            "resourceId": resource_id,
            "dedupeKey": dedupe_key,
            "metadata": metadata,
            "createdAt": _now_iso(),
        }
    )


def build_notification_event_for_report_generated(
    *,
    tenant_id: str | int,
    report_id: str,
    recipient_user_id: str | int | None = None,
    report_type: str | None = None,
    report_title: str | None = None,
) -> dict[str, Any]:
    normalized_report_id = str(report_id).strip()
    metadata: dict[str, Any] = {
        "reportId": normalized_report_id,
        "downloadReady": True,
    }
    if report_type:
        metadata["reportType"] = str(report_type).strip()
    if report_title:
        metadata["reportTitle"] = str(report_title).strip()

    return build_notification_event(
        event_type="report.generated",
        tenant_id=tenant_id,
        audience_type="SELF" if recipient_user_id is not None else "TENANT_ALL",
        recipient_user_id=recipient_user_id,
        title="Reporte generado",
        body="El reporte ya está listo para descargar.",
        # Sophia Docs is the existing mobile document-download screen. Do not
        # expose the S3 key or a presigned download URL in the notification.
        deep_link=(
            "xoc://sophia-docs?"
            f"documentId={quote(normalized_report_id, safe='')}&action=download-docx"
        ),
        resource_type="report",
        resource_id=normalized_report_id,
        dedupe_key=f"report.generated:{tenant_id}:{normalized_report_id}",
        metadata=metadata,
    )


def build_notification_event_for_critical_vulnerability(
    *, tenant_id: str | int, finding_id: str
) -> dict[str, Any]:
    return build_notification_event(
        event_type="vulnerability.critical_detected",
        tenant_id=tenant_id,
        audience_type="TENANT_ALL",
        title="Vulnerabilidad crítica detectada",
        body="Se detectó una vulnerabilidad crítica en tu tenant.",
        deep_link=f"xoc://vulnerabilities/{finding_id}",
        priority="critical",
        resource_type="vulnerability",
        resource_id=finding_id,
        dedupe_key=f"vulnerability.critical_detected:{tenant_id}:{finding_id}",
        metadata={"findingId": finding_id, "severity": "CRITICAL"},
    )


def build_notification_event_for_attack_detected(*, tenant_id: str | int, alert_id: str) -> dict[str, Any]:
    return build_notification_event(
        event_type="attack.detected",
        tenant_id=tenant_id,
        audience_type="TENANT_ALL",
        title="Posible ataque detectado",
        body="XOC detectó actividad sospechosa o ataque activo.",
        deep_link=f"xoc://alerts/{alert_id}",
        priority="critical",
        resource_type="alert",
        resource_id=alert_id,
        dedupe_key=f"attack.detected:{tenant_id}:{alert_id}",
        metadata={"alertId": alert_id},
    )


def build_notification_event_for_ticket_critical_created(*, tenant_id: str | int, ticket_id: str) -> dict[str, Any]:
    return build_notification_event(
        event_type="ticket.critical_created",
        tenant_id=tenant_id,
        audience_type="TENANT_ALL",
        title="Ticket crítico creado",
        body="Se creó un ticket crítico que requiere atención.",
        deep_link=f"xoc://tickets/{ticket_id}",
        priority="critical",
        resource_type="ticket",
        resource_id=ticket_id,
        dedupe_key=f"ticket.critical_created:{tenant_id}:{ticket_id}",
        metadata={"ticketId": ticket_id},
    )


def build_notification_event_for_integration_down(*, tenant_id: str | int, integration_id: str | None = None) -> dict[str, Any]:
    return build_notification_event(
        event_type="integration.down",
        tenant_id=tenant_id,
        audience_type="TENANT_ALL",
        title="Integración caída",
        body="Una integración del tenant dejó de reportar datos.",
        deep_link="xoc://integrations",
        priority="high",
        resource_type="integration",
        resource_id=integration_id,
        dedupe_key=f"integration.down:{tenant_id}:{integration_id or 'tenant'}",
        metadata={"integrationId": integration_id} if integration_id else None,
    )


def publish_notification_requested(event_detail: dict[str, Any]) -> dict[str, str]:
    """Publish canonical detail to the dedicated notification EventBridge bus."""
    event = normalize_notification_event(event_detail)
    bus_name = (os.environ.get("NOTIFICATION_EVENT_BUS_NAME") or "").strip()
    if not bus_name:
        raise NotificationEventPublishError("NOTIFICATION_EVENT_BUS_NAME is not configured")
    region = (os.environ.get("AWS_REGION") or "us-east-1").strip()
    response = boto3.client("events", region_name=region).put_events(
        Entries=[
            {
                "Source": EVENT_SOURCE,
                "DetailType": EVENT_DETAIL_TYPE,
                "EventBusName": bus_name,
                "Detail": json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            }
        ]
    )
    if int(response.get("FailedEntryCount") or 0) > 0:
        entry = (response.get("Entries") or [{}])[0]
        raise NotificationEventPublishError(str(entry.get("ErrorMessage") or entry.get("ErrorCode") or "EventBridge rejected event"))
    return {"eventId": event["eventId"], "eventBusName": bus_name}
