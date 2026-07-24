from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from src.shared.errors import NotFoundError, ValidationError


VALID_CASE_STATUSES = {"RESUELTO", "NO_RESUELTO"}


dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["CASES_TABLE_NAME"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cases_pk(tenant_id: int) -> str:
    return f"CASE#{tenant_id}"


def cases_sk(case_id: str) -> str:
    return f"CASE#{case_id}"


def ticket_index_pk(ticket_id: str) -> str:
    return f"CASE#TICKET#{ticket_id}"


def ticket_index_sk(created_at: str) -> str:
    return f"CASE#{created_at}"


def status_index_pk(tenant_id: int, status: str) -> str:
    return f"CASE#{tenant_id}#STATUS#{status}"


def status_index_sk(created_at: str) -> str:
    return f"CASE#{created_at}"


def case_key(tenant_id: int, case_id: str) -> dict:
    return {"pk": cases_pk(tenant_id), "sk": cases_sk(case_id)}


def build_secondary_index_fields(tenant_id: int, case_id: str, status: str, created_at: str, ticket_id: str) -> dict:
    return {
        "gsi1pk": ticket_index_pk(ticket_id),
        "gsi1sk": ticket_index_sk(created_at),
        "gsi2pk": status_index_pk(tenant_id, status),
        "gsi2sk": status_index_sk(created_at),
    }


def serialize_case(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith(("pk", "sk", "gsi"))}


def parse_case_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sort_cases_desc(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: parse_case_datetime(item.get("created_at")), reverse=True)


def create_case(
    tenant_id: int,
    ticket_id: str,
    subject: str,
    action: str,
    total_attempts: int,
    solution_applied: str | None,
    plan_used: dict | None,
    attempts_log: list | None,
    similar_case_id: str | None,
) -> dict:
    now = now_iso()
    case_id = str(uuid.uuid4())
    status = "RESUELTO" if action == "success" else "NO_RESUELTO"
    item = {
        "pk": cases_pk(tenant_id),
        "sk": cases_sk(case_id),
        "case_id": case_id,
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "subject": subject,
        "status": status,
        "resolution_result": "SUCCESS" if action == "success" else "FAILED_AFTER_ATTEMPTS",
        "total_attempts": total_attempts,
        "solution_applied": solution_applied,
        "plan_used": plan_used,
        "similar_case_id": similar_case_id,
        "attempts_log": attempts_log or [],
        "created_at": now,
        "updated_at": now,
    }
    item.update(build_secondary_index_fields(tenant_id, case_id, status, now, ticket_id))
    table.put_item(Item=item)
    return item


def get_case_or_none(tenant_id: int, case_id: str) -> dict | None:
    response = table.get_item(Key=case_key(tenant_id, case_id))
    return response.get("Item")


def get_case_or_404(tenant_id: int, case_id: str) -> dict:
    item = get_case_or_none(tenant_id, case_id)
    if not item:
        raise NotFoundError("Case not found")
    return item


def get_case_by_ticket(ticket_id: str) -> dict | None:
    response = table.query(
        IndexName="TicketIndex",
        KeyConditionExpression=Key("gsi1pk").eq(ticket_index_pk(ticket_id)),
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def list_tenant_cases(tenant_id: int, status: str | None = None, limit: int = 50) -> dict:
    if status:
        normalized = status.strip().upper()
        if normalized not in VALID_CASE_STATUSES:
            raise ValidationError(f"Invalid case status. Valid: {', '.join(sorted(VALID_CASE_STATUSES))}")
        response = table.query(
            IndexName="StatusIndex",
            KeyConditionExpression=Key("gsi2pk").eq(status_index_pk(tenant_id, normalized)),
            ScanIndexForward=False,
        )
        items = response.get("Items", [])
    else:
        response = table.query(
            KeyConditionExpression=Key("pk").eq(cases_pk(tenant_id)),
            ScanIndexForward=False,
        )
        items = response.get("Items", [])
    cases = [serialize_case(item) for item in sort_cases_desc(items)[: min(limit, 200)]]
    return {"cases": cases, "count": len(cases)}


def search_similar_cases(tenant_id: int, subject: str) -> dict | None:
    response = table.query(
        KeyConditionExpression=Key("pk").eq(cases_pk(tenant_id)),
        ScanIndexForward=False,
    )
    items = response.get("Items", [])
    subject_lower = (subject or "").strip().lower()
    keywords = [w for w in subject_lower.split() if len(w) > 3]
    if not keywords:
        return None
    for item in items:
        if item.get("status") != "RESUELTO":
            continue
        item_subject = (item.get("subject") or "").lower()
        if any(kw in item_subject for kw in keywords):
            return item
    return None


def delete_tenant_cases(tenant_id: int) -> int:
    response = table.query(
        KeyConditionExpression=Key("pk").eq(cases_pk(tenant_id)),
    )
    items = response.get("Items", [])
    deleted = 0
    if not items:
        return deleted
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
            deleted += 1
    return deleted
