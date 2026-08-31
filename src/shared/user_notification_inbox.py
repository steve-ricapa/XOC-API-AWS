"""DynamoDB user-visible notification inbox.

This store is deliberately separate from ``notification-event-inbox``.  The
latter is a worker idempotency ledger; this module owns the notifications a
user can read, open, and archive in the mobile application.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


STATUS_UNREAD = "UNREAD"
STATUS_READ = "READ"
STATUS_ARCHIVED = "ARCHIVED"
_VALID_LIST_STATUSES = {"unread", "all"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def user_notification_inbox_table():
    table_name = (os.environ.get("USER_NOTIFICATION_INBOX_TABLE_NAME") or "").strip()
    if not table_name:
        raise RuntimeError("USER_NOTIFICATION_INBOX_TABLE_NAME is not configured")
    return boto3.resource("dynamodb").Table(table_name)


def user_partition_key(tenant_id: str | int, user_id: str | int) -> str:
    return f"TENANT#{tenant_id}#USER#{user_id}"


def notification_id_for(tenant_id: str | int, user_id: str | int, dedupe_key: str) -> str:
    raw = f"v1|{tenant_id}|{user_id}|{dedupe_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def notification_key(tenant_id: str | int, user_id: str | int, notification_id: str) -> dict[str, str]:
    return {
        "PK": user_partition_key(tenant_id, user_id),
        "SK": f"NOTIF#{notification_id}",
    }


def _created_sort_key(created_at: str, notification_id: str) -> str:
    return f"CREATED#{created_at}#{notification_id}"


def _status_partition_key(tenant_id: str | int, user_id: str | int, status: str) -> str:
    return f"{user_partition_key(tenant_id, user_id)}#STATUS#{status}"


def create_ticket_user_notification(event: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Persist one visible notification for a ticket SELF event.

    Returns ``(item, created)``. Non-ticket/non-SELF events intentionally do
    not create a visible inbox record in this MVP.
    """
    event_type = str(event.get("eventType") or "")
    recipient_user_id = str(event.get("recipientUserId") or "").strip()
    if not event_type.startswith("ticket.") or str(event.get("audienceType") or "").upper() != "SELF" or not recipient_user_id:
        return None, False

    tenant_id = str(event["tenantId"])
    dedupe_key = str(event["dedupeKey"])
    notification_id = notification_id_for(tenant_id, recipient_user_id, dedupe_key)
    created_at = str(event.get("createdAt") or _now_iso())
    status = STATUS_UNREAD
    key = notification_key(tenant_id, recipient_user_id, notification_id)
    item: dict[str, Any] = {
        **key,
        "notificationId": notification_id,
        "tenantId": tenant_id,
        "userId": recipient_user_id,
        "eventType": event_type,
        "resourceType": event.get("resourceType"),
        "resourceId": event.get("resourceId"),
        "title": event["title"],
        "body": event["body"],
        "deepLink": event.get("deepLink"),
        "priority": event["priority"],
        "status": status,
        "createdAt": created_at,
        "eventId": event["eventId"],
        "dedupeKey": dedupe_key,
        "metadata": event.get("metadata"),
        "GSI1PK": user_partition_key(tenant_id, recipient_user_id),
        "GSI1SK": _created_sort_key(created_at, notification_id),
        "GSI2PK": _status_partition_key(tenant_id, recipient_user_id, status),
        "GSI2SK": _created_sort_key(created_at, notification_id),
    }
    table = user_notification_inbox_table()
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
        return item, True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return table.get_item(Key=key).get("Item"), False


def _encode_cursor(key: dict[str, Any] | None) -> str | None:
    if not key:
        return None
    payload = json.dumps(key, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(value, dict) or not all(isinstance(value.get(key), str) for key in ("PK", "SK")):
        raise ValueError("cursor is invalid")
    return value


def serialize_user_notification(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "notificationId": item.get("notificationId"),
        "eventType": item.get("eventType"),
        "resourceType": item.get("resourceType"),
        "resourceId": item.get("resourceId"),
        "title": item.get("title"),
        "body": item.get("body"),
        "deepLink": item.get("deepLink"),
        "priority": item.get("priority"),
        "status": item.get("status"),
        "createdAt": item.get("createdAt"),
        "readAt": item.get("readAt"),
        "openedAt": item.get("openedAt"),
        "archivedAt": item.get("archivedAt"),
        "eventId": item.get("eventId"),
        "campaignId": item.get("campaignId"),
        "metadata": item.get("metadata"),
    }


def list_user_notifications(
    tenant_id: str | int,
    user_id: str | int,
    *,
    status: str,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    normalized_status = status.strip().lower()
    if normalized_status not in _VALID_LIST_STATUSES:
        raise ValueError("status must be unread or all")

    table = user_notification_inbox_table()
    kwargs: dict[str, Any] = {
        "Limit": limit,
        "ScanIndexForward": False,
    }
    decoded_cursor = _decode_cursor(cursor)
    if decoded_cursor:
        kwargs["ExclusiveStartKey"] = decoded_cursor

    if normalized_status == "unread":
        kwargs.update({
            "IndexName": "UserStatusCreatedAtIndex",
            "KeyConditionExpression": Key("GSI2PK").eq(_status_partition_key(tenant_id, user_id, STATUS_UNREAD)),
        })
    else:
        kwargs.update({
            "IndexName": "UserCreatedAtIndex",
            "KeyConditionExpression": Key("GSI1PK").eq(user_partition_key(tenant_id, user_id)),
            "FilterExpression": "#status <> :archived",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {":archived": STATUS_ARCHIVED},
        })

    response = table.query(**kwargs)
    return [serialize_user_notification(item) for item in response.get("Items") or []], _encode_cursor(response.get("LastEvaluatedKey"))


def unread_count_for_user(tenant_id: str | int, user_id: str | int) -> int:
    table = user_notification_inbox_table()
    total = 0
    start_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "IndexName": "UserStatusCreatedAtIndex",
            "KeyConditionExpression": Key("GSI2PK").eq(_status_partition_key(tenant_id, user_id, STATUS_UNREAD)),
            "Select": "COUNT",
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.query(**kwargs)
        total += int(response.get("Count") or 0)
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return total


def mark_user_notification_read(
    tenant_id: str | int,
    user_id: str | int,
    notification_id: str,
    *,
    opened: bool = False,
) -> dict[str, Any] | None:
    table = user_notification_inbox_table()
    key = notification_key(tenant_id, user_id, notification_id)
    current = table.get_item(Key=key).get("Item")
    if not current:
        return None
    if str(current.get("status") or "") == STATUS_ARCHIVED:
        return current
    if str(current.get("status") or "") == STATUS_READ:
        return current

    now = _now_iso()
    updates = ["#status = :read", "readAt = :now", "GSI2PK = :statusPk"]
    values: dict[str, Any] = {
        ":read": STATUS_READ,
        ":now": now,
        ":statusPk": _status_partition_key(tenant_id, user_id, STATUS_READ),
        ":unread": STATUS_UNREAD,
    }
    if opened:
        updates.append("openedAt = :now")
    try:
        response = table.update_item(
            Key=key,
            UpdateExpression="SET " + ", ".join(updates),
            ConditionExpression="#status = :unread",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return table.get_item(Key=key).get("Item")


def archive_user_notification(tenant_id: str | int, user_id: str | int, notification_id: str) -> dict[str, Any] | None:
    table = user_notification_inbox_table()
    key = notification_key(tenant_id, user_id, notification_id)
    current = table.get_item(Key=key).get("Item")
    if not current:
        return None
    if str(current.get("status") or "") == STATUS_ARCHIVED:
        return current

    now = _now_iso()
    response = table.update_item(
        Key=key,
        UpdateExpression="SET #status = :archived, archivedAt = :now, GSI2PK = :statusPk",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":archived": STATUS_ARCHIVED,
            ":now": now,
            ":statusPk": _status_partition_key(tenant_id, user_id, STATUS_ARCHIVED),
        },
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes")
