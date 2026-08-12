"""DynamoDB access helpers for the Phase 1 push-device registry.

This module intentionally has no RDS dependency. Tenant, user and role come
from the authenticated API Gateway authorizer context for this MVP.
"""
from __future__ import annotations

import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


def device_registry_table():
    """Return the registry table lazily so module imports stay side-effect free."""
    table_name = (os.environ.get("DEVICE_REGISTRY_TABLE_NAME") or "").strip()
    if not table_name:
        raise RuntimeError("DEVICE_REGISTRY_TABLE_NAME is not configured")
    return boto3.resource("dynamodb").Table(table_name)


def device_key(tenant_id: str, user_id: str, device_id: str) -> dict[str, str]:
    return {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"USER#{user_id}#DEVICE#{device_id}",
    }


def get_registered_device(tenant_id: str, user_id: str, device_id: str) -> dict[str, Any] | None:
    response = device_registry_table().get_item(Key=device_key(tenant_id, user_id, device_id))
    return response.get("Item")


def update_registered_device(
    tenant_id: str,
    user_id: str,
    device_id: str,
    attributes: dict[str, Any],
) -> None:
    """Update one authenticated user's device without changing its registry key."""
    if not attributes:
        return

    expression_names: dict[str, str] = {}
    expression_values: dict[str, Any] = {}
    assignments: list[str] = []
    for index, (field, value) in enumerate(attributes.items()):
        name_key = f"#field{index}"
        value_key = f":value{index}"
        expression_names[name_key] = field
        expression_values[value_key] = value
        assignments.append(f"{name_key} = {value_key}")

    device_registry_table().update_item(
        Key=device_key(tenant_id, user_id, device_id),
        UpdateExpression="SET " + ", ".join(assignments),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def _active_devices_from_query(
    *,
    tenant_id: str,
    key_condition_expression: Any,
    max_devices: int,
) -> list[dict[str, Any]]:
    """Read active devices for a single tenant, stopping after the MVP limit.

    The registry key design lets us scope every query to one tenant. The
    status/notification checks are repeated in Python because DynamoDB filter
    expressions are applied after the key query and old records may not have
    every expected attribute.
    """
    if max_devices < 1:
        return []

    table = device_registry_table()
    devices: list[dict[str, Any]] = []
    exclusive_start_key: dict[str, Any] | None = None

    while len(devices) < max_devices:
        query_args: dict[str, Any] = {
            "KeyConditionExpression": key_condition_expression,
            "FilterExpression": "#status = :active AND #notificationsEnabled = :enabled",
            "ExpressionAttributeNames": {
                "#status": "status",
                "#notificationsEnabled": "notificationsEnabled",
            },
            "ExpressionAttributeValues": {":active": "ACTIVE", ":enabled": True},
        }
        if exclusive_start_key:
            query_args["ExclusiveStartKey"] = exclusive_start_key

        response = table.query(**query_args)
        for item in response.get("Items") or []:
            if (
                str(item.get("tenantId") or "") == tenant_id
                and str(item.get("status") or "").upper() == "ACTIVE"
                and item.get("notificationsEnabled") is True
            ):
                devices.append(item)
                if len(devices) >= max_devices:
                    return devices

        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return devices


def list_active_devices_by_user(tenant_id: str, user_id: str, *, max_devices: int = 101) -> list[dict[str, Any]]:
    """Return only enabled, active devices owned by the authenticated user."""
    return _active_devices_from_query(
        tenant_id=tenant_id,
        key_condition_expression=Key("PK").eq(f"TENANT#{tenant_id}") & Key("SK").begins_with(f"USER#{user_id}#DEVICE#"),
        max_devices=max_devices,
    )


def list_active_devices_by_tenant(tenant_id: str, *, max_devices: int = 101) -> list[dict[str, Any]]:
    """Return only enabled, active devices for one tenant; never cross tenants."""
    return _active_devices_from_query(
        tenant_id=tenant_id,
        key_condition_expression=Key("PK").eq(f"TENANT#{tenant_id}"),
        max_devices=max_devices,
    )
