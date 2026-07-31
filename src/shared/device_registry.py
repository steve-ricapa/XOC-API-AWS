"""DynamoDB access helpers for the Phase 1 push-device registry.

This module intentionally has no RDS dependency. Tenant, user and role come
from the authenticated API Gateway authorizer context for this MVP.
"""
from __future__ import annotations

import os
from typing import Any

import boto3


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

