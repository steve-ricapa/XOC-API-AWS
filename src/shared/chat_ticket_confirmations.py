"""Atomic SOPHIA confirmation in the existing tickets table (no RDS writes).

The receipt has a separate partition and no GSI attributes: it cannot appear
in ticket lists/counts. Keep it even if the resulting ticket is deleted.
Only the transaction winner may publish ticket.created; replay never publishes.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from src.shared.errors import AppError, ConflictError, ValidationError
from src.shared.logging import logger


_CONDITION = "attribute_not_exists(pk) AND attribute_not_exists(sk)"
_SEVERITIES = {"critical", "high", "medium", "low", "info"}


def _encode(item: dict) -> dict:
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in item.items()}


def _read(client, table_name: str, key: dict) -> dict | None:
    raw = client.get_item(TableName=table_name, Key=_encode(key), ConsistentRead=True).get("Item")
    if not raw:
        return None
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(value) for key, value in raw.items()}


def _recover(client, table_name: str, key: dict, fingerprint: str, tenant_id: int) -> dict | None:
    from src.shared.tickets_store import ticket_key, serialize_ticket

    receipt = _read(client, table_name, key)
    if not receipt:
        return None
    if receipt.get("fingerprint") != fingerprint:
        raise ConflictError("Proposal was confirmed with different content or context")
    ticket_id = receipt["ticket_id"]
    ticket = _read(client, table_name, ticket_key(tenant_id, ticket_id))
    if not ticket:
        # Do not resurrect deleted tickets or overwrite a broken receipt.
        raise ConflictError("Proposal was already confirmed but its ticket is no longer available")
    return {"ticket_id": ticket_id, "ticket": serialize_ticket(ticket), "already_confirmed": True}


def _publish_created(tenant_id: int, ticket_id: str, item: dict) -> str:
    """Existing best-effort envelope, without automatic retry of an uncertain send."""
    from src.shared.config import get_settings

    try:
        client = boto3.client("events", config=Config(retries={"total_max_attempts": 1}))
        response = client.put_events(Entries=[{
            "Source": "xoc.ticket",
            "DetailType": "ticket.created",
            "Detail": json.dumps({"tenant_id": tenant_id, "ticket_id": ticket_id,
                                  "subject": item["subject"], "status": item["status"]}),
            "EventBusName": get_settings().event_bus_name,
        }])
        if response.get("FailedEntryCount", 0) or not (response.get("Entries") or [{}])[0].get("EventId"):
            logger.warning("sophia_ticket_event_failed tenant=%s ticket=%s", tenant_id, ticket_id)
            return "FAILED"
        return "PUBLISHED"
    except Exception as exc:
        # Never log raw provider responses, subjects, tokens or credentials.
        logger.warning("sophia_ticket_event_unknown tenant=%s ticket=%s errorType=%s",
                       tenant_id, ticket_id, type(exc).__name__)
        return "UNKNOWN"


def confirm_ticket(*, tenant_id: int, user_id: int, actor_role: str, delegation_active: bool,
                   proposal_id: str, proposal_request_id: str | None, subject: str,
                   description: str, severity: str) -> dict:
    """Caller must first validate the signed proposal and current authorization."""
    from src.shared import tickets_store

    priority = severity.strip().lower()
    if priority not in _SEVERITIES:
        raise ValidationError("Ticket severity must be critical, high, medium, low, or info")
    # Backend-generated proposal identity is distinct from caller-supplied trace IDs.
    proposal_id = str(uuid.UUID(proposal_id))
    context = {"tenant_id": tenant_id, "user_id": user_id, "actor_role": actor_role,
               "delegation_active": delegation_active, "proposal_id": proposal_id,
               "subject": subject, "description": description, "priority": priority}
    fingerprint = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    key = {"pk": f"SOPHIA_CONFIRMATION#{tenant_id}", "sk": f"PROPOSAL#{proposal_id}"}
    table_name = tickets_store.table.name
    client = boto3.client("dynamodb", config=Config(retries={"total_max_attempts": 1}))
    previous = _recover(client, table_name, key, fingerprint, tenant_id)
    if previous:
        return previous

    # Shared builder behavior is unchanged. Only this path adds vetted fields.
    ticket_id, item = tickets_store.build_new_ticket_item(
        {"subject": subject, "description": description, "status": "PENDING"}, tenant_id, user_id,
    )
    item.update({"priority": priority, "severity": priority.upper(), "metadata": {
        "source": "sophia_chat_confirmed", "proposal_id": proposal_id,
        "proposal_request_id": proposal_request_id,
    }})
    receipt = {**key, "entity_type": "SOPHIA_TICKET_CONFIRMATION", "status": "CONFIRMED",
               "tenant_id": tenant_id, "user_id": user_id, "proposal_id": proposal_id,
               "ticket_id": ticket_id, "fingerprint": fingerprint, "created_at": item["created_at"],
               "event_status": "UNCONFIRMED"}
    # One token per invocation, stable across retries of this exact transaction.
    transaction = {"ClientRequestToken": str(uuid.uuid4()), "TransactItems": [
        {"Put": {"TableName": table_name, "Item": _encode(receipt), "ConditionExpression": _CONDITION}},
        {"Put": {"TableName": table_name, "Item": _encode(item), "ConditionExpression": _CONDITION}},
    ]}
    for attempt in range(3):
        try:
            client.transact_write_items(**transaction)
            break
        except (ClientError, BotoCoreError) as exc:
            # Includes response loss after commit: recover, but never republish.
            previous = _recover(client, table_name, key, fingerprint, tenant_id)
            if previous:
                return previous
            reasons = exc.response.get("CancellationReasons", []) if isinstance(exc, ClientError) else []
            code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
            if code == "TransactionCanceledException" and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
                raise ConflictError("Confirmation conflicts with existing ticket data") from exc
            retryable = (isinstance(exc, BotoCoreError) or code in {
                "TransactionCanceledException", "TransactionConflictException", "TransactionInProgressException",
                "InternalServerError", "ProvisionedThroughputExceededException", "ThrottlingException",
            })
            if not retryable:
                raise
            if attempt == 2:
                raise AppError("Confirmation temporarily unavailable; retry the same proposal",
                               status_code=503, code="confirmation_retryable") from exc
            time.sleep(0.05 * (attempt + 1))

    # Only a successful creator reaches this point. No event on recovery/replay.
    event_status = _publish_created(tenant_id, ticket_id, item)
    try:
        client.update_item(TableName=table_name, Key=_encode(key),
                           UpdateExpression="SET event_status = :event_status",
                           ExpressionAttributeValues=_encode({":event_status": event_status}),
                           ConditionExpression="attribute_exists(pk) AND attribute_exists(sk)")
    except Exception as exc:
        # Ticket remains committed. UNCONFIRMED means operator reconciliation,
        # not permission to automatically send another event on replay.
        logger.warning("sophia_ticket_event_receipt_unknown tenant=%s ticket=%s errorType=%s",
                       tenant_id, ticket_id, type(exc).__name__)
    return {"ticket_id": ticket_id, "ticket": tickets_store.serialize_ticket(item), "already_confirmed": False}
