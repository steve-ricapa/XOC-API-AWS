from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from src.shared.errors import NotFoundError, ValidationError


VALID_STATUSES = {
    "PENDING",
    "EXECUTED",
    "FAILED",
    "DERIVED",
    "PREAPROBADO",
    "APROBADO",
    "RECHAZADO",
    "PENDIENTE_EJECUCION",
    "EN_EJECUCION",
    "RESUELTO",
    "FALLIDO",
}
GLOBAL_CREATED_INDEX_PK = "ALL_TICKETS"


dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TICKETS_TABLE_NAME"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_status(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in VALID_STATUSES:
        raise ValidationError(f"Invalid status. Valid: {', '.join(sorted(VALID_STATUSES))}")
    return normalized


def tenant_pk(tenant_id: int) -> str:
    return f"TICKET#{tenant_id}"


def ticket_sk(ticket_id: str) -> str:
    return f"TICKET#{ticket_id}"


def status_index_pk(tenant_id: int, status: str) -> str:
    return f"TICKET#{tenant_id}#STATUS#{status}"


def status_index_sk(created_at: str, ticket_id: str) -> str:
    return f"{created_at}#{ticket_id}"


def ticket_lookup_pk(ticket_id: str) -> str:
    return f"TICKET#{ticket_id}"


def ticket_lookup_sk(tenant_id: int) -> str:
    return f"TICKET#{tenant_id}"


def global_created_sk(created_at: str, tenant_id: int, ticket_id: str) -> str:
    return f"{created_at}#{tenant_id}#{ticket_id}"


def build_secondary_index_fields(tenant_id: int, ticket_id: str, status: str, created_at: str) -> dict:
    return {
        "gsi1pk": status_index_pk(tenant_id, status),
        "gsi1sk": status_index_sk(created_at, ticket_id),
        "gsi2pk": ticket_lookup_pk(ticket_id),
        "gsi2sk": ticket_lookup_sk(tenant_id),
        "gsi3pk": GLOBAL_CREATED_INDEX_PK,
        "gsi3sk": global_created_sk(created_at, tenant_id, ticket_id),
    }


def serialize_ticket(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith(("pk", "sk", "gsi"))}


def parse_ticket_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sort_tickets_desc(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: parse_ticket_datetime(item.get("created_at")), reverse=True)


def ticket_key(tenant_id: int, ticket_id: str) -> dict:
    return {"pk": tenant_pk(tenant_id), "sk": ticket_sk(ticket_id)}


def get_tenant_ticket_or_none(tenant_id: int, ticket_id: str) -> dict | None:
    response = table.get_item(Key=ticket_key(tenant_id, ticket_id))
    return response.get("Item")


def get_tenant_ticket_or_404(tenant_id: int, ticket_id: str) -> dict:
    item = get_tenant_ticket_or_none(tenant_id, ticket_id)
    if not item:
        raise NotFoundError("Ticket not found")
    return item


def get_ticket_by_id_or_none(ticket_id: str) -> dict | None:
    response = table.query(
        IndexName="TicketLookupIndex",
        KeyConditionExpression=Key("gsi2pk").eq(ticket_lookup_pk(ticket_id)),
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def _query_all(**kwargs) -> list[dict]:
    items: list[dict] = []
    last_evaluated_key = None
    while True:
        query_kwargs = dict(kwargs)
        if last_evaluated_key:
            query_kwargs["ExclusiveStartKey"] = last_evaluated_key
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
    return items


def _query_count(**kwargs) -> int:
    total = 0
    last_evaluated_key = None
    while True:
        query_kwargs = dict(kwargs)
        query_kwargs["Select"] = "COUNT"
        if last_evaluated_key:
            query_kwargs["ExclusiveStartKey"] = last_evaluated_key
        response = table.query(**query_kwargs)
        total += response.get("Count", 0)
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
    return total


def list_tenant_tickets(tenant_id: int, status: str | None = None, limit: int = 50) -> dict:
    normalized_status = normalize_status(status)
    if normalized_status:
        items = _query_all(
            IndexName="StatusIndex",
            KeyConditionExpression=Key("gsi1pk").eq(status_index_pk(tenant_id, normalized_status)),
            ScanIndexForward=False,
        )
    else:
        items = _query_all(KeyConditionExpression=Key("pk").eq(tenant_pk(tenant_id)))
    tickets = [serialize_ticket(item) for item in sort_tickets_desc(items)[: min(limit, 200)]]
    return {"tickets": tickets, "count": len(tickets)}


def count_tenant_tickets(tenant_id: int, status: str | None = None) -> int:
    normalized_status = normalize_status(status)
    if normalized_status:
        return _query_count(
            IndexName="StatusIndex",
            KeyConditionExpression=Key("gsi1pk").eq(status_index_pk(tenant_id, normalized_status)),
        )
    return _query_count(KeyConditionExpression=Key("pk").eq(tenant_pk(tenant_id)))


def delete_tenant_tickets(tenant_id: int) -> int:
    items = _query_all(KeyConditionExpression=Key("pk").eq(tenant_pk(tenant_id)))
    deleted = 0
    if not items:
        return deleted
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
            deleted += 1
    return deleted


def list_superadmin_tickets(
    *,
    tenant_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    normalized_status = normalize_status(status)
    if tenant_id is not None and normalized_status:
        base_items = _query_all(
            IndexName="StatusIndex",
            KeyConditionExpression=Key("gsi1pk").eq(status_index_pk(int(tenant_id), normalized_status)),
            ScanIndexForward=False,
        )
    elif tenant_id is not None:
        base_items = _query_all(KeyConditionExpression=Key("pk").eq(tenant_pk(int(tenant_id))))
    else:
        base_items = _query_all(
            IndexName="GlobalCreatedIndex",
            KeyConditionExpression=Key("gsi3pk").eq(GLOBAL_CREATED_INDEX_PK),
            ScanIndexForward=False,
        )

    tickets = [serialize_ticket(item) for item in base_items]

    if tenant_id is not None:
        tickets = [ticket for ticket in tickets if int(ticket.get("tenant_id") or 0) == int(tenant_id)]
    if normalized_status:
        tickets = [ticket for ticket in tickets if ticket.get("status") == normalized_status]
    if q:
        query_text = q.strip().lower()
        tickets = [ticket for ticket in tickets if query_text in (ticket.get("subject") or "").lower()]
    if created_from:
        tickets = [ticket for ticket in tickets if parse_ticket_datetime(ticket.get("created_at")) >= created_from]
    if created_to:
        tickets = [ticket for ticket in tickets if parse_ticket_datetime(ticket.get("created_at")) <= created_to]

    tickets = sort_tickets_desc(tickets)
    total = len(tickets)
    sliced = tickets[offset : offset + limit]
    return {"tickets": sliced, "total": total, "limit": limit, "offset": offset}


def build_new_ticket_item(payload: dict, tenant_id: int, user_id: int | None) -> tuple[str, dict]:
    now = now_iso()
    ticket_id = str(uuid.uuid4())
    status = normalize_status(payload.get("status") or "PENDING")
    item = {
        "pk": tenant_pk(tenant_id),
        "sk": ticket_sk(ticket_id),
        "ticket_id": ticket_id,
        "tenant_id": tenant_id,
        "created_by_user_id": user_id,
        "subject": payload["subject"],
        "description": payload.get("description"),
        "status": status,
        "created_at": now,
        "updated_at": now,
        "executed_at": None,
        "action_plan": None,
        "action_plan_version": "v0",
        "approved_by_user_id": None,
        "approved_at": None,
        "rejected_by_user_id": None,
        "rejected_at": None,
        "execution_status": None,
        "execution_logs": None,
        "execution_summary": None,
        "pending_decision": None,
        "capability_level": None,
        "capability_policy_snapshot": None,
        "decision_timeout_minutes": payload.get("decision_timeout_minutes"),
        "on_decision_timeout": payload.get("on_decision_timeout"),
    }
    item.update(build_secondary_index_fields(tenant_id, ticket_id, status, now))
    return ticket_id, item


def update_ticket_fields(tenant_id: int, ticket_id: str, payload: dict) -> dict:
    item = get_tenant_ticket_or_404(tenant_id, ticket_id)
    if not payload:
        raise ValidationError("Request body is required")

    now = now_iso()
    updates = []
    expr_values = {}
    expr_names = {}
    status_changed = False
    new_status = None
    created_at = item.get("created_at") or now

    updatable = {
        "subject": "subject",
        "description": "description",
        "status": "status",
        "execution_status": "execution_status",
        "execution_logs": "execution_logs",
        "execution_summary": "execution_summary",
        "pending_decision": "pending_decision",
        "capability_level": "capability_level",
        "capability_policy_snapshot": "capability_policy_snapshot",
        "decision_timeout_minutes": "decision_timeout_minutes",
        "on_decision_timeout": "on_decision_timeout",
        "action_plan": "action_plan",
        "action_plan_version": "action_plan_version",
        "approval_task_token": "approval_task_token",
    }

    for key, attr in updatable.items():
        if key not in payload:
            continue
        value = payload[key]
        if key == "status":
            value = normalize_status(value)
            status_changed = True
            new_status = value
        updates.append(f"#{attr} = :{attr}")
        expr_values[f":{attr}"] = value
        expr_names[f"#{attr}"] = attr

    if payload.get("status") and normalize_status(payload["status"]) == "EXECUTED":
        updates.append("#executed_at = :executed_at")
        expr_values[":executed_at"] = now
        expr_names["#executed_at"] = "executed_at"

    if not updates:
        return item

    updates.append("#updated_at = :updated_at")
    expr_values[":updated_at"] = now
    expr_names["#updated_at"] = "updated_at"

    if status_changed and new_status:
        secondary = build_secondary_index_fields(tenant_id, ticket_id, new_status, created_at)
        for field_name in ("gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk", "gsi3pk", "gsi3sk"):
            updates.append(f"#{field_name} = :{field_name}")
            expr_names[f"#{field_name}"] = field_name
            expr_values[f":{field_name}"] = secondary[field_name]

    table.update_item(
        Key=ticket_key(tenant_id, ticket_id),
        UpdateExpression="SET " + ", ".join(updates),
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames=expr_names,
    )
    return get_tenant_ticket_or_404(tenant_id, ticket_id)
