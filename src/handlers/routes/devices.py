"""Phase 1 endpoints for registering and testing a user's push devices.

There is deliberately no RDS lookup here. The trusted request-authorizer
context is the Phase 1 source for tenant, user and role; RDS validation is a
future concern and must not be added here without an established helper.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from typing import Any

import boto3
from fastapi import APIRouter, Depends

from src.shared.dependencies import require_access_claims
from src.shared.device_registry import device_key, device_registry_table, get_registered_device
from src.shared.errors import ConfigurationError, NotFoundError, ValidationError
from src.shared.logging import logger


router = APIRouter(tags=["devices", "notifications"])

_PLATFORM_CONFIG = {
    "android": {"push_provider": "fcm", "channel_type": "GCM"},
    "ios": {"push_provider": "apns", "channel_type": "APNS"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_string(payload: dict[str, Any], field: str, *, max_length: int = 512) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValidationError(f"{field} is required")
    if len(value) > max_length:
        raise ValidationError(f"{field} exceeds {max_length} characters")
    return value


def _request_identity(claims: dict[str, Any]) -> tuple[str, str, str]:
    tenant_id = str(claims.get("tenantId") or claims.get("tenant_id") or "").strip()
    user_id = str(claims.get("userId") or claims.get("sub") or claims.get("principalId") or "").strip()
    role = str(claims.get("role") or "").strip()
    if not tenant_id:
        raise ValidationError("tenant_id not found in request context")
    if not user_id:
        raise ValidationError("user_id not found in request context")
    return tenant_id, user_id, role


def _platform_details(payload: dict[str, Any]) -> tuple[str, str, str]:
    platform = _required_string(payload, "platform", max_length=16).lower()
    config = _PLATFORM_CONFIG.get(platform)
    if not config:
        raise ValidationError("platform must be android or ios")
    push_provider = _required_string(payload, "pushProvider", max_length=16).lower()
    if push_provider != config["push_provider"]:
        raise ValidationError(f"pushProvider must be {config['push_provider']} for {platform}")
    return platform, push_provider, config["channel_type"]


def _optional_string(payload: dict[str, Any], field: str, *, max_length: int = 256) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    normalized = str(value).strip()
    if len(normalized) > max_length:
        raise ValidationError(f"{field} exceeds {max_length} characters")
    return normalized or None


@router.post("/devices", status_code=201)
def register_device(payload: dict[str, Any], claims: dict[str, Any] = Depends(require_access_claims)) -> dict[str, Any]:
    tenant_id, user_id, role = _request_identity(claims)
    device_id = _required_string(payload, "deviceId", max_length=128)
    push_token = _required_string(payload, "pushToken", max_length=4096)
    platform, push_provider, _ = _platform_details(payload)
    notifications_enabled = payload.get("notificationsEnabled", True)
    if not isinstance(notifications_enabled, bool):
        raise ValidationError("notificationsEnabled must be a boolean")

    now = _now_iso()
    table = device_registry_table()
    existing = table.get_item(Key=device_key(tenant_id, user_id, device_id)).get("Item") or {}
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
        "status": "ACTIVE",
        "notificationsEnabled": notifications_enabled,
        "appVersion": _optional_string(payload, "appVersion"),
        "osVersion": _optional_string(payload, "osVersion"),
        "deviceModel": _optional_string(payload, "deviceModel"),
        "lastSeenAt": now,
        "lastTokenRefreshAt": now,
        "createdAt": existing.get("createdAt") or now,
        "updatedAt": now,
    }
    table.put_item(Item=item)
    logger.info("Registered push device %s for tenant %s and user %s", device_id, tenant_id, user_id)
    return {
        "status": "registered",
        "deviceId": device_id,
        "platform": platform,
        "notificationsEnabled": notifications_enabled,
    }


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, claims: dict[str, Any] = Depends(require_access_claims)) -> dict[str, str]:
    tenant_id, user_id, _ = _request_identity(claims)
    normalized_device_id = _required_string({"deviceId": device_id}, "deviceId", max_length=128)
    table = device_registry_table()
    key = device_key(tenant_id, user_id, normalized_device_id)
    if not table.get_item(Key=key).get("Item"):
        raise NotFoundError("Device not found")
    table.delete_item(Key=key)
    logger.info("Deleted push device %s for tenant %s and user %s", normalized_device_id, tenant_id, user_id)
    return {"status": "deleted", "deviceId": normalized_device_id}


def _pinpoint_client():
    region = (os.environ.get("AWS_REGION") or "us-east-1").strip()
    return boto3.client("pinpoint", region_name=region)


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
    if device.get("status") != "ACTIVE" or not device.get("notificationsEnabled"):
        raise ValidationError("Notifications are disabled for this device")

    platform = str(device.get("platform") or "").lower()
    config = _PLATFORM_CONFIG.get(platform)
    push_token = str(device.get("pushToken") or "")
    if not config or not push_token:
        raise ValidationError("Device registration is invalid")
    application_id = (os.environ.get("END_USER_MESSAGING_APPLICATION_ID") or "").strip()
    if not application_id:
        raise ConfigurationError("END_USER_MESSAGING_APPLICATION_ID is not configured")

    message = {"Action": "OPEN_APP", "Title": title, "Body": body}
    if deep_link:
        message["Data"] = {"deepLink": deep_link}
    response = _pinpoint_client().send_messages(
        ApplicationId=application_id,
        MessageRequest={
            "Addresses": {push_token: {"ChannelType": config["channel_type"]}},
            "MessageConfiguration": {f"{config['channel_type']}Message": message},
        },
    )
    endpoint_result = (response.get("MessageResponse", {}).get("Result", {}).get(push_token, {}))
    delivery_status = endpoint_result.get("DeliveryStatus", "UNKNOWN")
    status_code = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
    logger.info(
        "Sent test push to device %s for tenant %s and user %s with delivery status %s",
        device_id,
        tenant_id,
        user_id,
        delivery_status,
    )
    return {
        "status": "sent" if delivery_status == "SUCCESSFUL" else "failed",
        "deliveryStatus": delivery_status,
        "statusCode": status_code,
    }
