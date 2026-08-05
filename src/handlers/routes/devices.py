"""Phase 1 endpoints for authenticated push-device registration and testing.

There is deliberately no RDS lookup here. Tenant, user and role come from the
trusted request-authorizer context. Device registration is DynamoDB-only for
this phase; audience resolution and campaigns are explicitly out of scope.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends

from src.shared.dependencies import require_access_claims
from src.shared.device_registry import (
    device_key,
    device_registry_table,
    get_registered_device,
    update_registered_device,
)
from src.shared.errors import ConfigurationError, NotFoundError, ValidationError
from src.shared.logging import logger


router = APIRouter(tags=["devices", "notifications"])

_DEVICE_STATUS_ACTIVE = "ACTIVE"
_DEVICE_STATUS_INACTIVE = "INACTIVE"
_DEVICE_STATUS_INVALID = "INVALID"

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

    push_token = str(device.get("pushToken") or "")
    if not push_token:
        raise ValidationError("Device registration is invalid")
    channel_type, message_key, apns_environment = _channel_details(device)
    application_id = (os.environ.get("END_USER_MESSAGING_APPLICATION_ID") or "").strip()
    if not application_id:
        raise ConfigurationError("END_USER_MESSAGING_APPLICATION_ID is not configured")

    message: dict[str, Any] = {"Action": "OPEN_APP", "Title": title, "Body": body}
    if deep_link:
        message["Data"] = {"deepLink": deep_link}

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
            ApplicationId=application_id,
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
        if _is_invalid_device_failure(status_message):
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
        }

    endpoint_result = response.get("MessageResponse", {}).get("Result", {}).get(push_token, {})
    delivery_status = str(endpoint_result.get("DeliveryStatus") or "UNKNOWN")
    http_status_code = _safe_status_code(response.get("ResponseMetadata", {}).get("HTTPStatusCode"))
    status_code = _safe_status_code(endpoint_result.get("StatusCode"), fallback=http_status_code)
    status_message = _safe_status_message(
        endpoint_result.get("StatusMessage"),
        fallback="Delivered" if delivery_status == "SUCCESSFUL" else "No status message returned by push provider",
    )
    request_id = response.get("ResponseMetadata", {}).get("RequestId")

    if _is_invalid_device_failure(status_message):
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
            request_id=request_id,
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
    }
