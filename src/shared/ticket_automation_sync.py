"""Persist assessment outcomes without changing the automation state machine.

Only an untouched PENDING ticket can leave assessment here. Synchronization and
push are best effort: failures must not change canResolve or trigger execution.
"""
from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from src.notifications.tickets import publish_ticket_status_notification

logger = logging.getLogger(__name__)

ASSESSMENT_MESSAGES = {
    "CANNOT_RESOLVE": "VICTOR no puede resolver este ticket automáticamente. Requiere revisión manual.",
    "VICTOR_NOT_CONFIGURED": "No hay un endpoint de VICTOR disponible para evaluar el ticket. Requiere revisión manual.",
    "VICTOR_TIMEOUT": "La evaluación de VICTOR agotó el tiempo de espera. Requiere revisión manual.",
    "VICTOR_ACCESS_DENIED": "No se pudo completar la evaluación por un rechazo de acceso de VICTOR. Requiere revisión manual.",
    "VICTOR_UNAVAILABLE": "No se pudo completar la evaluación de VICTOR por un error de comunicación o respuesta. Requiere revisión manual.",
}


def _conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _assessment_guard() -> str:
    # Initial tickets contain DynamoDB NULLs; attribute_not_exists alone is not
    # enough. Also protect plans/approval data written before their status update.
    return (
        "attribute_exists(pk) AND attribute_exists(sk) AND #tenant = :tenant "
        "AND #status = :pending AND attribute_not_exists(#assessment) "
        "AND (attribute_not_exists(#run) OR attribute_type(#run, :nullType) OR #run = :started) "
        "AND (attribute_not_exists(action_plan) OR attribute_type(action_plan, :nullType)) "
        "AND (attribute_not_exists(action_plans) OR attribute_type(action_plans, :nullType)) "
        "AND (attribute_not_exists(pending_decision) OR attribute_type(pending_decision, :nullType))"
    )


def sync_unresolvable_assessment(tenant_id: int, ticket_id: str, reason_code: str) -> bool:
    """Record the existing false assessment, once, scoped to its tenant.

    DERIVED is an existing ticket status. It means manual review is required,
    NOT that a human/team was assigned or that any action plan was executed.
    Never persist raw provider responses, request bodies or credentials here.
    """
    try:
        from src.shared.tickets_store import now_iso, status_index_pk, table, ticket_key

        message = ASSESSMENT_MESSAGES[reason_code]
        outcome = "CANNOT_RESOLVE" if reason_code == "CANNOT_RESOLVE" else "ASSESSMENT_FAILED"
        completed_at = now_iso()
        table.update_item(
            Key=ticket_key(int(tenant_id), ticket_id),
            UpdateExpression=(
                "SET #status = :derived, #run = :outcome, #assessment = :assessment, "
                "execution_summary = :message, updated_at = :now, gsi1pk = :statusIndex"
            ),
            ConditionExpression=_assessment_guard(),
            ExpressionAttributeNames={
                "#tenant": "tenant_id", "#status": "status",
                "#run": "execution_status", "#assessment": "automation_assessment",
            },
            ExpressionAttributeValues={
                ":tenant": int(tenant_id), ":pending": "PENDING", ":started": "STARTED",
                ":nullType": "NULL", ":derived": "DERIVED", ":outcome": outcome,
                ":message": message, ":now": completed_at,
                ":statusIndex": status_index_pk(int(tenant_id), "DERIVED"),
                ":assessment": {
                    "outcome": outcome, "reason_code": reason_code,
                    "message": message, "completed_at": completed_at,
                },
            },
        )
    except ClientError as exc:
        if _conditional_failure(exc):
            # Duplicate, missing/deleted ticket, wrong tenant or already advanced.
            return False
        logger.warning("ticket_assessment_sync_failed tenant=%s ticket=%s errorType=%s",
                       tenant_id, ticket_id, type(exc).__name__)
        return False
    except Exception as exc:
        logger.warning("ticket_assessment_sync_failed tenant=%s ticket=%s errorType=%s",
                       tenant_id, ticket_id, type(exc).__name__)
        return False

    logger.info("ticket_assessment_synced tenant=%s ticket=%s outcome=%s reason=%s",
                tenant_id, ticket_id, outcome, reason_code)
    try:
        # Existing creator-only pipeline and dedupe key; never a tenant broadcast.
        publish_ticket_status_notification(tenant_id=int(tenant_id), ticket_id=ticket_id, status="DERIVED")
    except Exception as exc:
        logger.warning("ticket_assessment_notification_failed tenant=%s ticket=%s errorType=%s",
                       tenant_id, ticket_id, type(exc).__name__)
    return True


def record_automation_started(tenant_id: int, ticket_id: str, execution_arn: str) -> None:
    """Attach the ARN, but never regress an assessment/approval/terminal result.

    The child execution may finish before start_execution returns to its caller.
    ARN and STARTED are deliberately separate conditional updates for that race.
    The caller already treats persistence failures as non-fatal.
    """
    from src.shared.tickets_store import now_iso, table, ticket_key

    key = ticket_key(tenant_id, ticket_id)
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET execution_arn = :arn",
            ConditionExpression=(
                "attribute_exists(pk) AND attribute_exists(sk) AND tenant_id = :tenant "
                "AND (attribute_not_exists(execution_arn) OR attribute_type(execution_arn, :nullType) "
                "OR execution_arn = :arn)"
            ),
            ExpressionAttributeValues={":arn": execution_arn, ":tenant": tenant_id, ":nullType": "NULL"},
        )
        table.update_item(
            Key=key,
            UpdateExpression="SET #run = :started, updated_at = :now",
            ConditionExpression=(
                _assessment_guard() + " AND execution_arn = :arn "
                "AND (attribute_not_exists(#run) OR attribute_type(#run, :nullType))"
            ),
            ExpressionAttributeNames={
                "#tenant": "tenant_id", "#status": "status",
                "#run": "execution_status", "#assessment": "automation_assessment",
            },
            ExpressionAttributeValues={
                ":tenant": tenant_id, ":pending": "PENDING", ":started": "STARTED",
                ":nullType": "NULL", ":now": now_iso(), ":arn": execution_arn,
            },
        )
    except ClientError as exc:
        if not _conditional_failure(exc):
            raise
