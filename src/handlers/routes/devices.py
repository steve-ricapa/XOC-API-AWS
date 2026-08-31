"""Authenticated push-device, controlled-audience, and campaign endpoints.

There is deliberately no RDS lookup here. Tenant, user and role come from the
trusted request-authorizer context. Device registration is DynamoDB-only for
this service. Campaign persistence and audience selection are DynamoDB-only
until a safe, existing read-only RDS audience helper is available.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends

from src.shared.dependencies import require_access_claims
from src.shared.device_registry import (
    device_key,
    device_registry_table,
    get_registered_device,
    list_active_devices_by_tenant,
    list_active_devices_by_user,
    update_registered_device,
)
from src.shared.errors import ConfigurationError, ForbiddenError, NotFoundError, ValidationError
from src.shared.logging import logger
from src.shared.notification_campaigns import (
    campaign_key,
    create_notification_campaign,
    update_notification_campaign_result,
)
from src.notifications.events import (
    NotificationEventValidationError,
    build_notification_event,
    publish_notification_requested,
)


router = APIRouter(tags=["devices", "notifications"])

_DEVICE_STATUS_ACTIVE = "ACTIVE"
_DEVICE_STATUS_INACTIVE = "INACTIVE"
_DEVICE_STATUS_INVALID = "INVALID"
_CAMPAIGN_STATUS_PROCESSING = "PROCESSING"
_CAMPAIGN_STATUS_COMPLETED = "COMPLETED"
_CAMPAIGN_STATUS_PARTIAL_FAILED = "PARTIAL_FAILED"
_CAMPAIGN_STATUS_FAILED = "FAILED"
_CAMPAIGN_STATUS_NO_DEVICES = "NO_DEVICES"
_MAX_SYNCHRONOUS_AUDIENCE_DEVICES = 100
_NOTIFICATION_AUDIENCES = {"SELF", "TENANT_ALL", "TENANT_ADMINS"}

_PLATFORM_CONFIG = {
    "android": {
        "push_provider": "fcm",
        "channel_type": "GCM",
        "message_key": "GCMMessage",
    },
    "ios": {
        "push_provider": "apns",
        "channel_type": "APNS",
        "message_key": "APNSMessage",
    },
}
_IOS_APNS_ENVIRONMENTS = {"sandbox", "production"}
_INVALID_DEVICE_MESSAGE_MARKERS = (
    "baddevicetoken",
    "devicetokennotfortopic",
    "unregistered",
    "notregistered",
    "invalidregistration",
    "endpoint disabled",
    "endpointdisabled",
    "token invalido",
    "invalid token",
    "badcertificateenvironment",
)
_PROVIDER_CONFIGURATION_MESSAGE_MARKERS = (
    "invalidprovidertoken",
    "expiredprovidertoken",
    "missingprovidertoken",
    "topicdisallowed",
    "badcertificate",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_string(payload: dict[str, Any], field: str, *, max_length: int = 512) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValidationError(f"{field} is required")
    if len(value) > max_length:
        raise ValidationError(f"{field} exceeds {max_length} characters")
    return value


def _optional_string(payload: dict[str, Any], field: str, *, max_length: int = 256) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    normalized = str(value).strip()
    if len(normalized) > max_length:
        raise ValidationError(f"{field} exceeds {max_length} characters")
    return normalized or None


def _request_identity(claims: dict[str, Any]) -> tuple[str, str, str]:
    tenant_id = str(claims.get("tenantId") or claims.get("tenant_id") or "").strip()
    user_id = str(claims.get("userId") or claims.get("sub") or claims.get("principalId") or "").strip()
    role = str(claims.get("role") or "").strip()
    if not tenant_id:
        raise ValidationError("tenant_id not found in request context")
    if not user_id:
        raise ValidationError("user_id not found in request context")
    return tenant_id, user_id, role


def _platform_details(payload: dict[str, Any]) -> tuple[str, str]:
    platform = _required_string(payload, "platform", max_length=16).lower()
    config = _PLATFORM_CONFIG.get(platform)
    if not config:
        raise ValidationError("platform must be android or ios")
    push_provider = _required_string(payload, "pushProvider", max_length=16).lower()
    if push_provider != config["push_provider"]:
        raise ValidationError(f"pushProvider must be {config['push_provider']} for {platform}")
    return platform, push_provider


def _default_apns_environment() -> str:
    return "production" if (os.environ.get("APP_STAGE") or "dev").strip().lower() == "prod" else "sandbox"


def _apns_environment_for_registration(payload: dict[str, Any], platform: str, push_provider: str) -> str | None:
    requested_environment = _optional_string(payload, "apnsEnvironment", max_length=16)
    if platform != "ios" or push_provider != "apns":
        if requested_environment is not None:
            raise ValidationError("apnsEnvironment is only valid for ios/apns devices")
        return None

    apns_environment = (requested_environment or _default_apns_environment()).lower()
    if apns_environment not in _IOS_APNS_ENVIRONMENTS:
        raise ValidationError("apnsEnvironment must be sandbox or production")
    return apns_environment


def _channel_details(device: dict[str, Any]) -> tuple[str, str, str | None]:
    platform = str(device.get("platform") or "").lower()
    push_provider = str(device.get("pushProvider") or "").lower()
    config = _PLATFORM_CONFIG.get(platform)
    if not config or push_provider != config["push_provider"]:
        raise ValidationError("Device registration is invalid")

    if platform == "android":
        return "GCM", "GCMMessage", None

    apns_environment = str(device.get("apnsEnvironment") or _default_apns_environment()).lower()
    if apns_environment not in _IOS_APNS_ENVIRONMENTS:
        raise ValidationError("Device APNs environment is invalid")
    channel_type = "APNS_SANDBOX" if apns_environment == "sandbox" else "APNS"
    return channel_type, "APNSMessage", apns_environment


def _safe_token_hash(device: dict[str, Any]) -> str:
    token_hash = str(device.get("tokenHash") or "").strip()
    if not token_hash:
        token_hash = hashlib.sha256(str(device.get("pushToken") or "").encode("utf-8")).hexdigest()
    return f"{token_hash[:12]}..." if token_hash else ""


def _safe_status_message(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized[:512] if normalized else fallback


def _safe_status_code(value: Any, *, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _build_safe_push_log(
    *,
    event: str,
    tenant_id: str,
    user_id: str,
    device_id: str,
    device: dict[str, Any],
    channel_type: str,
    delivery_status: str | None = None,
    status_code: int | None = None,
    status_message: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event": event,
        "tenantId": tenant_id,
        "userId": user_id,
        "deviceId": device_id,
        "platform": str(device.get("platform") or "").lower(),
        "pushProvider": str(device.get("pushProvider") or "").lower(),
        "apnsEnvironment": device.get("apnsEnvironment") if device.get("platform") == "ios" else None,
        "channelType": channel_type,
        "deliveryStatus": delivery_status,
        "statusCode": status_code,
        "statusMessage": status_message,
        "tokenHash": _safe_token_hash(device),
        "requestId": request_id,
    }


def _is_invalid_device_failure(status_message: str) -> bool:
    normalized = status_message.lower().replace("_", "").replace("-", "")
    if "badcertificateenvironment" in normalized:
        return True
    if any(marker in normalized for marker in _PROVIDER_CONFIGURATION_MESSAGE_MARKERS):
        return False
    return any(marker in normalized for marker in _INVALID_DEVICE_MESSAGE_MARKERS)


def _mark_device_invalid(
    tenant_id: str,
    user_id: str,
    device_id: str,
    *,
    status_code: int,
    status_message: str,
) -> None:
    now = _now_iso()
    update_registered_device(
        tenant_id,
        user_id,
        device_id,
        {
            "status": _DEVICE_STATUS_INVALID,
            "invalidatedAt": now,
            "updatedAt": now,
            "lastFailureReason": status_message,
            "lastFailureStatusCode": status_code,
            "lastFailureStatusMessage": status_message,
        },
    )


def _pinpoint_client():
    region = (os.environ.get("AWS_REGION") or "us-east-1").strip()
    return boto3.client("pinpoint", region_name=region)


def _push_application_id() -> str:
    application_id = (os.environ.get("END_USER_MESSAGING_APPLICATION_ID") or "").strip()
    if not application_id:
        raise ConfigurationError("END_USER_MESSAGING_APPLICATION_ID is not configured")
    return application_id


def _send_push_to_registered_device(
    *,
    tenant_id: str,
    user_id: str,
    device_id: str,
    device: dict[str, Any],
    title: str,
    body: str,
    deep_link: str | None,
    notification_data: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send one registered-device push with Phase 1 channel and invalidation rules.

    This is shared by the Phase 1 test endpoint and the Phase 2 campaign
    endpoint so Android, APNS and APNS_SANDBOX behavior cannot drift.
    """
    push_token = str(device.get("pushToken") or "")
    if not push_token:
        raise ValidationError("Device registration is invalid")

    channel_type, message_key, apns_environment = _channel_details(device)
    message: dict[str, Any] = {"Action": "OPEN_APP", "Title": title, "Body": body}
    data: dict[str, str] = {}
    if deep_link:
        data["deepLink"] = deep_link
    for key, value in (notification_data or {}).items():
        if key and value:
            data[str(key)] = str(value)
    if data:
        message["Data"] = data

    logger.info(
        "push_send_requested",
        extra=_build_safe_push_log(
            event="push_send_requested",
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=device_id,
            device=device,
            channel_type=channel_type,
        ),
    )

    try:
        response = _pinpoint_client().send_messages(
            ApplicationId=_push_application_id(),
            MessageRequest={
                "Addresses": {push_token: {"ChannelType": channel_type}},
                "MessageConfiguration": {message_key: message},
            },
        )
    except (ClientError, BotoCoreError) as exc:
        error_response = exc.response if isinstance(exc, ClientError) else {}
        metadata = error_response.get("ResponseMetadata", {})
        error_details = error_response.get("Error", {})
        status_code = _safe_status_code(metadata.get("HTTPStatusCode"), fallback=502)
        status_message = _safe_status_message(
            error_details.get("Code") or error_details.get("Message"),
            fallback="Push provider request failed",
        )
        invalid_token = _is_invalid_device_failure(status_message)
        if invalid_token:
            _mark_device_invalid(
                tenant_id,
                user_id,
                device_id,
                status_code=status_code,
                status_message=status_message,
            )
        logger.warning(
            "push_send_result",
            extra=_build_safe_push_log(
                event="push_send_result",
                tenant_id=tenant_id,
                user_id=user_id,
                device_id=device_id,
                device=device,
                channel_type=channel_type,
                delivery_status="FAILED",
                status_code=status_code,
                status_message=status_message,
                request_id=metadata.get("RequestId"),
            ),
        )
        return {
            "status": "failed",
            "deliveryStatus": "FAILED",
            "statusCode": status_code,
            "statusMessage": status_message,
            "channelType": channel_type,
            "platform": device.get("platform"),
            "pushProvider": device.get("pushProvider"),
            "apnsEnvironment": apns_environment,
            "deviceId": device_id,
            "invalidToken": invalid_token,
        }

    endpoint_result = response.get("MessageResponse", {}).get("Result", {}).get(push_token, {})
    delivery_status = str(endpoint_result.get("DeliveryStatus") or "UNKNOWN")
    http_status_code = _safe_status_code(response.get("ResponseMetadata", {}).get("HTTPStatusCode"))
    status_code = _safe_status_code(endpoint_result.get("StatusCode"), fallback=http_status_code)
    status_message = _safe_status_message(
        endpoint_result.get("StatusMessage"),
        fallback="Delivered" if delivery_status == "SUCCESSFUL" else "No status message returned by push provider",
    )
    invalid_token = _is_invalid_device_failure(status_message)
    if invalid_token:
        _mark_device_invalid(
            tenant_id,
            user_id,
            device_id,
            status_code=status_code,
            status_message=status_message,
        )

    logger.info(
        "push_send_result",
        extra=_build_safe_push_log(
            event="push_send_result",
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=device_id,
            device=device,
            channel_type=channel_type,
            delivery_status=delivery_status,
            status_code=status_code,
            status_message=status_message,
            request_id=response.get("ResponseMetadata", {}).get("RequestId"),
        ),
    )
    return {
        "status": "sent" if delivery_status == "SUCCESSFUL" else "failed",
        "deliveryStatus": delivery_status,
        "statusCode": status_code,
        "statusMessage": status_message,
        "channelType": channel_type,
        "platform": device.get("platform"),
        "pushProvider": device.get("pushProvider"),
        "apnsEnvironment": apns_environment,
        "deviceId": device_id,
        "invalidToken": invalid_token,
    }


def _notification_audience(payload: dict[str, Any]) -> str:
    audience_type = _required_string(payload, "audienceType", max_length=32).upper()
    if audience_type not in _NOTIFICATION_AUDIENCES:
        raise ValidationError("audienceType must be SELF, TENANT_ALL, or TENANT_ADMINS")
    return audience_type


def _optional_metadata(payload: dict[str, Any]) -> dict[str, Any] | None:
    metadata = payload.get("metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be an object")
    try:
        serialized = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("metadata must be JSON serializable") from exc
    if len(serialized) > 16_384:
        raise ValidationError("metadata exceeds 16384 characters")
    return metadata


def _delegation_is_active(claims: dict[str, Any]) -> bool:
    return str(claims.get("delegation") or "").strip().lower() in {"true", "1", "yes"}


def _authorize_campaign_audience(claims: dict[str, Any], audience_type: str) -> None:
    role = str(claims.get("role") or "").strip().upper()
    if role == "USER":
        if audience_type == "SELF":
            return
        raise ForbiddenError("USER role may only send notifications to SELF")
    if role == "ADMIN":
        return
    if role == "ADMIN_XOC":
        if audience_type not in {"TENANT_ALL", "TENANT_ADMINS"}:
            raise ForbiddenError("ADMIN_XOC may only send tenant notifications with delegated tenant context")
        if not _delegation_is_active(claims) or not str(claims.get("tenantId") or claims.get("actingTenantId") or "").strip():
            raise ForbiddenError("Delegated tenant context required for ADMIN_XOC notifications")
        return
    if role == "SUPERADMIN":
        raise ForbiddenError("SUPERADMIN notification behavior is not defined")
    raise ForbiddenError("Role is not allowed to send notifications")


def _resolve_notification_audience(
    tenant_id: str,
    user_id: str,
    audience_type: str,
    *,
    max_devices: int = _MAX_SYNCHRONOUS_AUDIENCE_DEVICES + 1,
) -> list[dict[str, Any]]:
    if audience_type == "SELF":
        return list_active_devices_by_user(tenant_id, user_id, max_devices=max_devices)
    if audience_type == "TENANT_ALL":
        return list_active_devices_by_tenant(tenant_id, max_devices=max_devices)

    # TODO(push-phase-2): replace this with a known-safe read-only RDS helper
    # when the project exposes one for active tenant ADMIN user IDs. Do not
    # infer SQL tables or columns here.
    raise ValidationError("audience resolver not implemented for TENANT_ADMINS")


@router.post("/devices", status_code=201)
def register_device(payload: dict[str, Any], claims: dict[str, Any] = Depends(require_access_claims)) -> dict[str, Any]:
    tenant_id, user_id, role = _request_identity(claims)
    device_id = _required_string(payload, "deviceId", max_length=128)
    push_token = _required_string(payload, "pushToken", max_length=4096)
    platform, push_provider = _platform_details(payload)
    apns_environment = _apns_environment_for_registration(payload, platform, push_provider)
    notifications_enabled = payload.get("notificationsEnabled", True)
    if not isinstance(notifications_enabled, bool):
        raise ValidationError("notificationsEnabled must be a boolean")

    now = _now_iso()
    table = device_registry_table()
    existing = table.get_item(Key=device_key(tenant_id, user_id, device_id)).get("Item") or {}
    status = _DEVICE_STATUS_ACTIVE if notifications_enabled else _DEVICE_STATUS_INACTIVE
    item = {
        **device_key(tenant_id, user_id, device_id),
        "tenantId": tenant_id,
        "userId": user_id,
        "role": role,
        "deviceId": device_id,
        "platform": platform,
        "pushProvider": push_provider,
        "pushToken": push_token,
        "tokenHash": hashlib.sha256(push_token.encode("utf-8")).hexdigest(),
        "status": status,
        "notificationsEnabled": notifications_enabled,
        "appVersion": _optional_string(payload, "appVersion"),
        "osVersion": _optional_string(payload, "osVersion"),
        "deviceModel": _optional_string(payload, "deviceModel"),
        "lastSeenAt": now,
        "lastTokenRefreshAt": now,
        "createdAt": existing.get("createdAt") or now,
        "updatedAt": now,
    }
    if apns_environment:
        item["apnsEnvironment"] = apns_environment
    if not notifications_enabled:
        item["deactivatedAt"] = now
    table.put_item(Item=item)
    logger.info(
        "push_device_registered",
        extra={
            "tenantId": tenant_id,
            "userId": user_id,
            "deviceId": device_id,
            "platform": platform,
            "pushProvider": push_provider,
            "apnsEnvironment": apns_environment,
            "status": status,
        },
    )
    return {
        "status": "registered",
        "deviceId": device_id,
        "platform": platform,
        "pushProvider": push_provider,
        "apnsEnvironment": apns_environment,
        "notificationsEnabled": notifications_enabled,
    }


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, claims: dict[str, Any] = Depends(require_access_claims)) -> dict[str, str]:
    tenant_id, user_id, _ = _request_identity(claims)
    normalized_device_id = _required_string({"deviceId": device_id}, "deviceId", max_length=128)
    if not get_registered_device(tenant_id, user_id, normalized_device_id):
        raise NotFoundError("Device not found")

    now = _now_iso()
    update_registered_device(
        tenant_id,
        user_id,
        normalized_device_id,
        {
            "status": _DEVICE_STATUS_INACTIVE,
            "notificationsEnabled": False,
            "deactivatedAt": now,
            "updatedAt": now,
        },
    )
    logger.info(
        "push_device_deactivated",
        extra={"tenantId": tenant_id, "userId": user_id, "deviceId": normalized_device_id},
    )
    return {"status": _DEVICE_STATUS_INACTIVE, "deviceId": normalized_device_id}


@router.post("/notifications/test")
def send_test_notification(payload: dict[str, Any], claims: dict[str, Any] = Depends(require_access_claims)) -> dict[str, Any]:
    tenant_id, user_id, _ = _request_identity(claims)
    device_id = _required_string(payload, "deviceId", max_length=128)
    title = _required_string(payload, "title", max_length=200)
    body = _required_string(payload, "body", max_length=2000)
    deep_link = _optional_string(payload, "deepLink", max_length=2048)
    device = get_registered_device(tenant_id, user_id, device_id)
    if not device:
        raise NotFoundError("Device not found")
    if str(device.get("status") or "").upper() != _DEVICE_STATUS_ACTIVE or not device.get("notificationsEnabled"):
        raise ValidationError("Notifications are disabled for this device")
    result = _send_push_to_registered_device(
        tenant_id=tenant_id,
        user_id=user_id,
        device_id=device_id,
        device=device,
        title=title,
        body=body,
        deep_link=deep_link,
    )
    # Keep the Phase 1 endpoint response contract unchanged. This flag is an
    # internal aggregate used by Phase 2 campaign accounting only.
    result.pop("invalidToken", None)
    return result


@router.post("/notifications/send")
def send_notification_to_audience(
    payload: dict[str, Any], claims: dict[str, Any] = Depends(require_access_claims)
) -> dict[str, Any]:
    audience_type = _notification_audience(payload)
    _authorize_campaign_audience(claims, audience_type)
    tenant_id, user_id, role = _request_identity(claims)
    title = _required_string(payload, "title", max_length=200)
    body = _required_string(payload, "body", max_length=2000)
    deep_link = _optional_string(payload, "deepLink", max_length=2048)
    metadata = _optional_metadata(payload)

    # Validate the configuration before persisting a PROCESSING campaign.
    _push_application_id()
    devices = _resolve_notification_audience(tenant_id, user_id, audience_type)
    if len(devices) > _MAX_SYNCHRONOUS_AUDIENCE_DEVICES:
        raise ValidationError("Audience exceeds max devices for synchronous send. Use async campaign flow in Phase 3.")

    campaign_id = f"campaign-{uuid4()}"
    now = _now_iso()
    campaign = {
        **campaign_key(tenant_id, campaign_id),
        "tenantId": tenant_id,
        "campaignId": campaign_id,
        "audienceType": audience_type,
        "title": title,
        "body": body,
        "deepLink": deep_link,
        "metadata": metadata,
        "status": _CAMPAIGN_STATUS_PROCESSING,
        "totalDevices": len(devices),
        "sentCount": 0,
        "failedCount": 0,
        "invalidTokenCount": 0,
        "createdByUserId": user_id,
        "createdByRole": role.upper(),
        "createdAt": now,
        "updatedAt": now,
    }
    create_notification_campaign(campaign)

    sent_count = 0
    failed_count = 0
    invalid_token_count = 0
    for device in devices:
        device_id = str(device.get("deviceId") or "")
        device_user_id = str(device.get("userId") or "")
        if not device_id or not device_user_id:
            failed_count += 1
            continue
        try:
            result = _send_push_to_registered_device(
                tenant_id=tenant_id,
                user_id=device_user_id,
                device_id=device_id,
                device=device,
                title=title,
                body=body,
                deep_link=deep_link,
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

    if not devices:
        campaign_status = _CAMPAIGN_STATUS_NO_DEVICES
        response_status = "no_devices"
    elif sent_count == len(devices):
        campaign_status = _CAMPAIGN_STATUS_COMPLETED
        response_status = "completed"
    elif sent_count == 0:
        campaign_status = _CAMPAIGN_STATUS_FAILED
        response_status = "failed"
    else:
        campaign_status = _CAMPAIGN_STATUS_PARTIAL_FAILED
        response_status = "partial_failed"

    updated_at = _now_iso()
    summary = {
        "status": campaign_status,
        "totalDevices": len(devices),
        "sentCount": sent_count,
        "failedCount": failed_count,
        "invalidTokenCount": invalid_token_count,
        "updatedAt": updated_at,
    }
    update_notification_campaign_result(tenant_id, campaign_id, summary)
    logger.info(
        "notification_campaign_result",
        extra={
            "event": "notification_campaign_result",
            "tenantId": tenant_id,
            "campaignId": campaign_id,
            "audienceType": audience_type,
            "totalDevices": len(devices),
            "sentCount": sent_count,
            "failedCount": failed_count,
            "invalidTokenCount": invalid_token_count,
            "status": campaign_status,
            "createdByUserId": user_id,
            "createdByRole": role.upper(),
        },
    )
    return {
        "status": response_status,
        "campaignId": campaign_id,
        "audienceType": audience_type,
        "totalDevices": len(devices),
        "sentCount": sent_count,
        "failedCount": failed_count,
        "invalidTokenCount": invalid_token_count,
    }


@router.post("/notifications/events/test", status_code=202)
def publish_notification_event_for_qa(
    payload: dict[str, Any], claims: dict[str, Any] = Depends(require_access_claims)
) -> dict[str, Any]:
    """Admin-only QA entry point for EventBridge -> SQS -> worker validation."""
    role = str(claims.get("role") or "").strip().upper()
    if role == "ADMIN_XOC":
        if not _delegation_is_active(claims) or not str(claims.get("tenantId") or claims.get("actingTenantId") or "").strip():
            raise ForbiddenError("Delegated tenant context required for ADMIN_XOC notifications")
    elif role != "ADMIN":
        raise ForbiddenError("Admin access required to publish notification test events")

    tenant_id, user_id, _ = _request_identity(claims)
    audience_type = _notification_audience(payload)
    if audience_type == "TENANT_ADMINS":
        raise ValidationError("audience resolver not implemented for TENANT_ADMINS")
    event_type = _required_string(payload, "eventType", max_length=120)
    metadata = _optional_metadata(payload)
    try:
        event = build_notification_event(
            event_type=event_type,
            tenant_id=tenant_id,
            audience_type=audience_type,
            recipient_user_id=user_id if audience_type == "SELF" else None,
            title=_required_string(payload, "title", max_length=200),
            body=_required_string(payload, "body", max_length=2000),
            deep_link=_optional_string(payload, "deepLink", max_length=2048),
            priority=_optional_string(payload, "priority", max_length=16) or "normal",
            resource_type=_optional_string(payload, "resourceType", max_length=120),
            resource_id=_optional_string(payload, "resourceId", max_length=256),
            dedupe_key=_optional_string(payload, "dedupeKey", max_length=512),
            metadata=metadata,
        )
    except NotificationEventValidationError as exc:
        raise ValidationError(str(exc)) from exc
    result = publish_notification_requested(event)
    return {"status": "queued", "eventId": result["eventId"], "eventBusName": result["eventBusName"]}
