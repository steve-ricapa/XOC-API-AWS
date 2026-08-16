"""DynamoDB idempotency records for EventBridge notification events."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError


_STATUS_PROCESSING = "PROCESSING"
_STATUS_COMPLETED = "COMPLETED"
_STATUS_FAILED = "FAILED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def notification_event_inbox_table():
    table_name = (os.environ.get("NOTIFICATION_EVENT_INBOX_TABLE_NAME") or "").strip()
    if not table_name:
        raise RuntimeError("NOTIFICATION_EVENT_INBOX_TABLE_NAME is not configured")
    return boto3.resource("dynamodb").Table(table_name)


def inbox_key(tenant_id: str, dedupe_key: str) -> dict[str, str]:
    return {"PK": f"TENANT#{tenant_id}", "SK": f"DEDUPE#{dedupe_key}"}


def claim_notification_event(event: dict[str, Any]) -> bool:
    """Claim an event exactly once; completed/processing duplicates are ignored.

    FAILED records may be reclaimed on an SQS retry. This keeps transient AWS
    failures recoverable while completed events remain idempotent.
    """
    tenant_id = str(event["tenantId"])
    dedupe_key = str(event["dedupeKey"])
    key = inbox_key(tenant_id, dedupe_key)
    now = _now_iso()
    item = {
        **key,
        "tenantId": tenant_id,
        "dedupeKey": dedupe_key,
        "eventId": event["eventId"],
        "eventType": event["eventType"],
        "status": _STATUS_PROCESSING,
        "firstSeenAt": now,
        "updatedAt": now,
    }
    table = notification_event_inbox_table()
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise

    existing = table.get_item(Key=key).get("Item") or {}
    if str(existing.get("status") or "").upper() != _STATUS_FAILED:
        return False
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET #status = :processing, updatedAt = :updatedAt, eventId = :eventId, eventType = :eventType REMOVE lastError",
            ConditionExpression="#status = :failed",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":processing": _STATUS_PROCESSING,
                ":failed": _STATUS_FAILED,
                ":updatedAt": now,
                ":eventId": event["eventId"],
                ":eventType": event["eventType"],
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def complete_notification_event(tenant_id: str, dedupe_key: str, campaign_id: str | None = None) -> None:
    attributes = {"status": _STATUS_COMPLETED, "updatedAt": _now_iso()}
    if campaign_id:
        attributes["campaignId"] = campaign_id
    _update_event(tenant_id, dedupe_key, attributes)


def fail_notification_event(tenant_id: str, dedupe_key: str, error: str, campaign_id: str | None = None) -> None:
    attributes = {"status": _STATUS_FAILED, "updatedAt": _now_iso(), "lastError": str(error)[:512]}
    if campaign_id:
        attributes["campaignId"] = campaign_id
    _update_event(tenant_id, dedupe_key, attributes)


def _update_event(tenant_id: str, dedupe_key: str, attributes: dict[str, Any]) -> None:
    expression_names: dict[str, str] = {}
    expression_values: dict[str, Any] = {}
    assignments: list[str] = []
    for index, (field, value) in enumerate(attributes.items()):
        name_key = f"#field{index}"
        value_key = f":value{index}"
        expression_names[name_key] = field
        expression_values[value_key] = value
        assignments.append(f"{name_key} = {value_key}")
    notification_event_inbox_table().update_item(
        Key=inbox_key(tenant_id, dedupe_key),
        UpdateExpression="SET " + ", ".join(assignments),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )
