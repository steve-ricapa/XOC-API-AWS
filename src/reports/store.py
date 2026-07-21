from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from src.shared.errors import NotFoundError


DOCUMENT_STATUSES = frozenset({"PENDING", "PROCESSING", "COMPLETED", "FAILED"})

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["REPORTS_TABLE_NAME"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tenant_pk(tenant_id: int) -> str:
    return f"DOCUMENT#{tenant_id}"


def document_sk(document_id: str) -> str:
    return f"DOCUMENT#{document_id}"


def lookup_pk(document_id: str) -> str:
    return f"DOCUMENT#{document_id}"


def lookup_sk(tenant_id: int) -> str:
    return f"DOCUMENT#{tenant_id}"


def status_index_pk(tenant_id: int, status: str) -> str:
    return f"DOCUMENT#{tenant_id}#STATUS#{status}"


def status_index_sk(created_at: str, document_id: str) -> str:
    return f"{created_at}#{document_id}"


def tenant_created_sk(created_at: str, document_id: str) -> str:
    return f"{created_at}#{document_id}"


def build_secondary_index_fields(tenant_id: int, document_id: str, status: str, created_at: str) -> dict:
    return {
        "gsi1pk": lookup_pk(document_id),
        "gsi1sk": lookup_sk(tenant_id),
        "gsi2pk": status_index_pk(tenant_id, status),
        "gsi2sk": status_index_sk(created_at, document_id),
        "gsi3pk": tenant_pk(tenant_id),
        "gsi3sk": tenant_created_sk(created_at, document_id),
    }


def document_key(tenant_id: int, document_id: str) -> dict:
    return {"pk": tenant_pk(tenant_id), "sk": document_sk(document_id)}


def create_document_job(
    tenant_id: int,
    document_type: str,
    created_by_user_id: int | None = None,
    filters: dict | None = None,
    parameters: dict | None = None,
    request_payload: dict | None = None,
    request_hash: str | None = None,
) -> tuple[str, dict]:
    now = now_iso()
    document_id = str(uuid.uuid4())
    status = "PENDING"
    item = {
        "pk": tenant_pk(tenant_id),
        "sk": document_sk(document_id),
        "document_id": document_id,
        "tenant_id": tenant_id,
        "created_by_user_id": created_by_user_id,
        "document_type": document_type,
        "status": status,
        "filters": filters,
        "parameters": parameters,
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
    item.update(build_secondary_index_fields(tenant_id, document_id, status, now))
    return document_id, item


def serialize_report(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith(("pk", "sk", "gsi"))}


def get_document_job(tenant_id: int, document_id: str) -> dict | None:
    response = table.get_item(Key=document_key(tenant_id, document_id))
    return response.get("Item")


def get_document_job_or_404(tenant_id: int, document_id: str) -> dict:
    item = get_document_job(tenant_id, document_id)
    if not item:
        raise NotFoundError("Document not found")
    return item


def update_document_status(
    tenant_id: int,
    document_id: str,
    status: str,
    **extra,
) -> dict:
    item = get_document_job_or_404(tenant_id, document_id)
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

    secondary = build_secondary_index_fields(tenant_id, document_id, status, created_at)
    for field in ("gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk", "gsi3pk", "gsi3sk"):
        updates.append(f"#{field} = :{field}")
        expr_names[f"#{field}"] = field
        expr_values[f":{field}"] = secondary[field]

    table.update_item(
        Key=document_key(tenant_id, document_id),
        UpdateExpression="SET " + ", ".join(updates),
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames=expr_names,
    )
    return get_document_job_or_404(tenant_id, document_id)


def list_tenant_document_jobs(
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


def count_tenant_document_jobs(tenant_id: int, status: str | None = None) -> int:
    if status:
        total = 0
        last_key = None
        while True:
            kwargs = {
                "IndexName": "StatusIndex",
                "KeyConditionExpression": Key("gsi2pk").eq(status_index_pk(tenant_id, status)),
                "Select": "COUNT",
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            response = table.query(**kwargs)
            total += response.get("Count", 0)
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
        return total
    total = 0
    last_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(tenant_pk(tenant_id)),
            "Select": "COUNT",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        total += response.get("Count", 0)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    return total


def delete_tenant_document_jobs(tenant_id: int) -> int:
    items = []
    last_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(tenant_pk(tenant_id)),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    deleted = 0
    if not items:
        return deleted
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
            deleted += 1
    return deleted


REPORT_STATUSES = DOCUMENT_STATUSES
report_sk = document_sk
report_key = document_key
create_report_item = create_document_job
get_report = get_document_job
get_report_or_404 = get_document_job_or_404
update_report_status = update_document_status
list_tenant_reports = list_tenant_document_jobs
