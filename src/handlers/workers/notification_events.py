"""SQS worker for EventBridge-driven XOC push notifications.

Domain modules publish canonical events to EventBridge. This worker owns
idempotency, audience lookup, campaign summaries and push delivery.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Iterator
from uuid import uuid4

from src.handlers.routes.devices import (
    _resolve_notification_audience,
    _send_push_to_registered_device,
)
from src.shared.errors import ConfigurationError, ValidationError
from src.shared.logging import logger
from src.shared.notification_campaigns import (
    campaign_key,
    create_notification_campaign,
    update_notification_campaign_result,
)
from src.notifications.events import NotificationEventValidationError, normalize_notification_event
from src.notifications.inbox import claim_notification_event, complete_notification_event, fail_notification_event
from src.shared.user_notification_inbox import create_ticket_user_notification


_MAX_DEVICES_DEFAULT = 500
_SEND_BATCH_SIZE_DEFAULT = 100
_CAMPAIGN_PROCESSING = "PROCESSING"
_CAMPAIGN_COMPLETED = "COMPLETED"
_CAMPAIGN_PARTIAL_FAILED = "PARTIAL_FAILED"
_CAMPAIGN_FAILED = "FAILED"
_CAMPAIGN_NO_DEVICES = "NO_DEVICES"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default
    return min(max(value, 1), maximum)


def _chunks(items: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _event_detail_from_record(record: dict[str, Any]) -> dict[str, Any]:
    try:
        body = json.loads(record.get("body") or "{}")
    except json.JSONDecodeError as exc:
        raise NotificationEventValidationError("SQS message body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise NotificationEventValidationError("SQS message body must be an object")
    detail = body.get("detail", body)
    if not isinstance(detail, dict):
        raise NotificationEventValidationError("EventBridge detail must be an object")
    return detail


def _campaign_status(total: int, sent: int) -> tuple[str, str]:
    if total == 0:
        return _CAMPAIGN_NO_DEVICES, "no_devices"
    if sent == total:
        return _CAMPAIGN_COMPLETED, "completed"
    if sent == 0:
        return _CAMPAIGN_FAILED, "failed"
    return _CAMPAIGN_PARTIAL_FAILED, "partial_failed"


def _create_campaign(event: dict[str, Any], queue_message_id: str | None, total_devices: int) -> str:
    campaign_id = f"campaign-{uuid4()}"
    now = _now_iso()
    create_notification_campaign(
        {
            **campaign_key(event["tenantId"], campaign_id),
            "tenantId": event["tenantId"],
            "campaignId": campaign_id,
            "audienceType": event["audienceType"],
            "title": event["title"],
            "body": event["body"],
            "deepLink": event.get("deepLink"),
            "metadata": event.get("metadata"),
            "status": _CAMPAIGN_PROCESSING,
            "totalDevices": total_devices,
            "sentCount": 0,
            "failedCount": 0,
            "invalidTokenCount": 0,
            "createdByUserId": event.get("recipientUserId"),
            "createdByRole": "SYSTEM",
            "sourceEventId": event["eventId"],
            "sourceEventType": event["eventType"],
            "dedupeKey": event["dedupeKey"],
            "resourceType": event.get("resourceType"),
            "resourceId": event.get("resourceId"),
            "priority": event["priority"],
            "triggerSource": "eventbridge",
            "queueMessageId": queue_message_id,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    return campaign_id


def _complete_campaign(
    *,
    event: dict[str, Any],
    campaign_id: str,
    total_devices: int,
    sent_count: int,
    failed_count: int,
    invalid_token_count: int,
    error: str | None = None,
) -> tuple[str, str]:
    campaign_status, response_status = _campaign_status(total_devices, sent_count)
    attributes: dict[str, Any] = {
        "status": campaign_status,
        "totalDevices": total_devices,
        "sentCount": sent_count,
        "failedCount": failed_count,
        "invalidTokenCount": invalid_token_count,
        "updatedAt": _now_iso(),
    }
    if error:
        attributes["lastError"] = error[:512]
    update_notification_campaign_result(event["tenantId"], campaign_id, attributes)
    logger.info(
        "notification_event_campaign_completed",
        extra={
            "event": "notification_event_campaign_completed",
            "tenantId": event["tenantId"],
            "eventId": event["eventId"],
            "campaignId": campaign_id,
            "eventType": event["eventType"],
            "audienceType": event["audienceType"],
            "totalDevices": total_devices,
            "sentCount": sent_count,
            "failedCount": failed_count,
            "invalidTokenCount": invalid_token_count,
            "status": campaign_status,
        },
    )
    return campaign_status, response_status


def process_notification_event(event_detail: dict[str, Any], *, queue_message_id: str | None = None) -> dict[str, Any]:
    """Process one canonical event. Raises only for retryable infrastructure errors."""
    event = normalize_notification_event(event_detail)
    logger.info(
        "notification_event_received",
        extra={
            "event": "notification_event_received",
            "tenantId": event["tenantId"],
            "eventId": event["eventId"],
            "eventType": event["eventType"],
            "dedupeKey": event["dedupeKey"],
            "audienceType": event["audienceType"],
            "resourceType": event.get("resourceType"),
            "resourceId": event.get("resourceId"),
        },
    )
    if not claim_notification_event(event):
        logger.info(
            "notification_event_duplicate_ignored",
            extra={
                "event": "notification_event_duplicate_ignored",
                "tenantId": event["tenantId"],
                "eventId": event["eventId"],
                "dedupeKey": event["dedupeKey"],
            },
        )
        return {"status": "duplicate_ignored", "eventId": event["eventId"]}

    campaign_id: str | None = None
    try:
        # Ticket notifications are durable user-facing records. Persist before
        # resolving devices or attempting APNs/FCM, so a failed push is never
        # the reason a creator loses the ticket state change.
        user_notification, _created = create_ticket_user_notification(event)
        notification_data = (
            {
                "notificationId": str(user_notification["notificationId"]),
                "eventType": str(event["eventType"]),
                "resourceType": str(event.get("resourceType") or ""),
                "resourceId": str(event.get("resourceId") or ""),
            }
            if user_notification
            else None
        )
        max_devices = _positive_int_env("NOTIFICATION_MAX_DEVICES_PER_EVENT", _MAX_DEVICES_DEFAULT, 5_000)
        batch_size = _positive_int_env("NOTIFICATION_SEND_BATCH_SIZE", _SEND_BATCH_SIZE_DEFAULT, 100)
        recipient_user_id = event.get("recipientUserId") or ""
        devices = _resolve_notification_audience(
            event["tenantId"], recipient_user_id, event["audienceType"], max_devices=max_devices + 1
        )
        campaign_id = _create_campaign(event, queue_message_id, len(devices))
        if len(devices) > max_devices:
            message = "Audience exceeds max devices for Phase 3 worker. Increase limit or implement PushDeliveryQueue."
            _complete_campaign(
                event=event,
                campaign_id=campaign_id,
                total_devices=len(devices),
                sent_count=0,
                failed_count=len(devices),
                invalid_token_count=0,
                error=message,
            )
            fail_notification_event(event["tenantId"], event["dedupeKey"], message, campaign_id)
            return {"status": "failed", "eventId": event["eventId"], "campaignId": campaign_id}

        sent_count = 0
        failed_count = 0
        invalid_token_count = 0
        for device_batch in _chunks(devices, batch_size):
            for device in device_batch:
                device_id = str(device.get("deviceId") or "")
                device_user_id = str(device.get("userId") or "")
                if not device_id or not device_user_id:
                    failed_count += 1
                    continue
                try:
                    result = _send_push_to_registered_device(
                        tenant_id=event["tenantId"],
                        user_id=device_user_id,
                        device_id=device_id,
                        device=device,
                        title=event["title"],
                        body=event["body"],
                        deep_link=event.get("deepLink"),
                        notification_data=notification_data,
                    )
                except (ConfigurationError, ValidationError):
                    failed_count += 1
                    continue
                if result["deliveryStatus"] == "SUCCESSFUL":
                    sent_count += 1
                else:
                    failed_count += 1
                if result.get("invalidToken"):
                    invalid_token_count += 1

        campaign_status, response_status = _complete_campaign(
            event=event,
            campaign_id=campaign_id,
            total_devices=len(devices),
            sent_count=sent_count,
            failed_count=failed_count,
            invalid_token_count=invalid_token_count,
        )
        complete_notification_event(event["tenantId"], event["dedupeKey"], campaign_id)
        return {
            "status": response_status,
            "campaignStatus": campaign_status,
            "eventId": event["eventId"],
            "campaignId": campaign_id,
        }
    except NotificationEventValidationError:
        raise
    except ValidationError as exc:
        # TENANT_ADMINS (without the approved RDS helper) and malformed
        # device/audience data are permanent event failures, not SQS retries.
        fail_notification_event(event["tenantId"], event["dedupeKey"], str(exc), campaign_id)
        raise NotificationEventValidationError(str(exc)) from exc
    except Exception as exc:
        # Keep the failed inbox record reclaimable on SQS retry. No event body
        # or push token is included in this error path.
        fail_notification_event(event["tenantId"], event["dedupeKey"], str(exc), campaign_id)
        raise


def handler(event: dict[str, Any], context: Any) -> dict[str, list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    for record in event.get("Records") or []:
        message_id = str(record.get("messageId") or "")
        try:
            process_notification_event(_event_detail_from_record(record), queue_message_id=message_id or None)
        except NotificationEventValidationError as exc:
            logger.warning(
                "notification_event_permanent_failure",
                extra={"event": "notification_event_permanent_failure", "messageId": message_id, "error": str(exc)[:256]},
            )
            # Permanent schema/audience failures are acknowledged so SQS does
            # not retry them forever.
            continue
        except Exception as exc:
            logger.exception(
                "notification_event_retryable_failure",
                extra={"event": "notification_event_retryable_failure", "messageId": message_id, "error": type(exc).__name__},
            )
            if message_id:
                failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
