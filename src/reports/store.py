from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from src.shared.errors import NotFoundError


REPORT_STATUSES = frozenset({"PENDING", "PROCESSING", "COMPLETED", "FAILED"})

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["REPORTS_TABLE_NAME"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tenant_pk(tenant_id: int) -> str:
    return f"REPORT#{tenant_id}"


def report_sk(report_id: str) -> str:
    return f"REPORT#{report_id}"


def lookup_pk(report_id: str) -> str:
    return f"REPORT#{report_id}"


def lookup_sk(tenant_id: int) -> str:
    return f"REPORT#{tenant_id}"


def status_index_pk(tenant_id: int, status: str) -> str:
    return f"REPORT#{tenant_id}#STATUS#{status}"


def status_index_sk(created_at: str, report_id: str) -> str:
    return f"{created_at}#{report_id}"


def tenant_created_sk(created_at: str, report_id: str) -> str:
    return f"{created_at}#{report_id}"


def build_secondary_index_fields(tenant_id: int, report_id: str, status: str, created_at: str) -> dict:
    return {
        "gsi1pk": lookup_pk(report_id),
        "gsi1sk": lookup_sk(tenant_id),
        "gsi2pk": status_index_pk(tenant_id, status),
        "gsi2sk": status_index_sk(created_at, report_id),
        "gsi3pk": tenant_pk(tenant_id),
        "gsi3sk": tenant_created_sk(created_at, report_id),
    }


def report_key(tenant_id: int, report_id: str) -> dict:
    return {"pk": tenant_pk(tenant_id), "sk": report_sk(report_id)}


def create_report_item(
    tenant_id: int,
    report_type: str,
    created_by_user_id: int | None = None,
    filters: dict | None = None,
    request_payload: dict | None = None,
    request_hash: str | None = None,
) -> tuple[str, dict]:
    now = now_iso()
    report_id = str(uuid.uuid4())
    status = "PENDING"
    item = {
        "pk": tenant_pk(tenant_id),
        "sk": report_sk(report_id),
        "report_id": report_id,
        "tenant_id": tenant_id,
        "created_by_user_id": created_by_user_id,
        "report_type": report_type,
        "status": status,
        "filters": filters,
        "request_payload": request_payload,
        "request_hash": request_hash,
        "execution_arn": None,
        "s3_bucket": None,
        "s3_key": None,
        "s3_version_id": None,
        "size_bytes": None,
        "error_code": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }
    item.update(build_secondary_index_fields(tenant_id, report_id, status, now))
    return report_id, item


def serialize_report(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith(("pk", "sk", "gsi"))}


def get_report(tenant_id: int, report_id: str) -> dict | None:
    response = table.get_item(Key=report_key(tenant_id, report_id))
    return response.get("Item")


def get_report_or_404(tenant_id: int, report_id: str) -> dict:
    item = get_report(tenant_id, report_id)
    if not item:
        raise NotFoundError("Report not found")
    return item


def update_report_status(
    tenant_id: int,
    report_id: str,
    status: str,
    **extra,
) -> dict:
    item = get_report_or_404(tenant_id, report_id)
    now = now_iso()
    created_at = item.get("created_at") or now

    updates = ["#status = :status", "#updated_at = :updated_at"]
    expr_values = {":status": status, ":updated_at": now}
    expr_names = {"#status": "status", "#updated_at": "updated_at"}

    if status == "PROCESSING":
        updates.append("#started_at = :started_at")
        expr_values[":started_at"] = now
        expr_names["#started_at"] = "started_at"

    if status == "COMPLETED":
        updates.append("#completed_at = :completed_at")
        expr_values[":completed_at"] = now
        expr_names["#completed_at"] = "completed_at"

    for key, value in extra.items():
        if value is not None:
            updates.append(f"#{key} = :{key}")
            expr_values[f":{key}"] = value
            expr_names[f"#{key}"] = key

    secondary = build_secondary_index_fields(tenant_id, report_id, status, created_at)
    for field in ("gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk", "gsi3pk", "gsi3sk"):
        updates.append(f"#{field} = :{field}")
        expr_names[f"#{field}"] = field
        expr_values[f":{field}"] = secondary[field]

    table.update_item(
        Key=report_key(tenant_id, report_id),
        UpdateExpression="SET " + ", ".join(updates),
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames=expr_names,
    )
    return get_report_or_404(tenant_id, report_id)


def list_tenant_reports(
    tenant_id: int,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    if status:
        items = []
        last_key = None
        while True:
            kwargs = {
                "IndexName": "StatusIndex",
                "KeyConditionExpression": Key("gsi2pk").eq(status_index_pk(tenant_id, status)),
                "ScanIndexForward": False,
                "Limit": limit,
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            response = table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key or len(items) >= limit:
                break
        return [serialize_report(item) for item in items[:limit]]
    else:
        items = []
        last_key = None
        while True:
            kwargs = {
                "KeyConditionExpression": Key("pk").eq(tenant_pk(tenant_id)),
                "ScanIndexForward": False,
                "Limit": limit,
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            response = table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key or len(items) >= limit:
                break
        return [serialize_report(item) for item in items[:limit]]
