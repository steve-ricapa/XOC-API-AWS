"""DynamoDB-only campaign summaries for synchronous push sends.

Campaign records intentionally contain delivery aggregates only. Device push
tokens, JWTs and authorization data never belong in this table.
"""
from __future__ import annotations

import os
from typing import Any

import boto3


def notification_campaigns_table():
    table_name = (os.environ.get("NOTIFICATION_CAMPAIGNS_TABLE_NAME") or "").strip()
    if not table_name:
        raise RuntimeError("NOTIFICATION_CAMPAIGNS_TABLE_NAME is not configured")
    return boto3.resource("dynamodb").Table(table_name)


def campaign_key(tenant_id: str, campaign_id: str) -> dict[str, str]:
    return {"PK": f"TENANT#{tenant_id}", "SK": f"CAMPAIGN#{campaign_id}"}


def create_notification_campaign(item: dict[str, Any]) -> None:
    notification_campaigns_table().put_item(Item=item)


def update_notification_campaign_result(
    tenant_id: str,
    campaign_id: str,
    attributes: dict[str, Any],
) -> None:
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

    notification_campaigns_table().update_item(
        Key=campaign_key(tenant_id, campaign_id),
        UpdateExpression="SET " + ", ".join(assignments),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )
