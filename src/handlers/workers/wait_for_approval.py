import logging
from datetime import datetime, timedelta, timezone

from src.shared.errors import ValidationError
from src.shared.risk_config import approval_requirement, DEFAULT_RISK_LEVEL
from src.shared.tickets_store import get_tenant_ticket_or_none, update_ticket_fields
from src.notifications.tickets import publish_ticket_status_notification

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_DAYS = 7


def _build_plan_options(plans) -> list:
    """Convierte la lista de planes de Victor en opciones de seleccion.

    Cada opcion contiene el plan completo con sus pasos para que el frontend lo
    muestre y para que el workflow pueda ejecutar el plan elegido.
    Si no hay planes se devuelve una lista vacia (comportamiento anterior).
    """
    if not isinstance(plans, list):
        return []

    options = []
    for idx, plan in enumerate(plans):
        if not isinstance(plan, dict):
            continue
        steps = plan.get("plan", [])
        if not isinstance(steps, list):
            steps = []
        if len(steps) == 0:
            continue
        option_id = plan.get("plan_id") or f"plan-{idx + 1}"
        options.append({
            "option_id": option_id,
            "title": plan.get("title") or f"Plan {idx + 1}",
            "summary": plan.get("plan_summary") or "",
            "risk_level": plan.get("risk_level") or "",
            "total_steps": plan.get("total_steps") or len(steps),
            "is_recommended": idx == 0,
            "plan": steps,
        })
    return options


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    max_risk_level = (event.get("maxRiskLevel") or DEFAULT_RISK_LEVEL).lower()
    plans = event.get("plans") or []

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    tenant_id = int(tenant_id)

    task_token = event.get("taskToken") or event.get("task_token")
    if not task_token:
        raise ValidationError("taskToken not found in event")

    requirement = approval_requirement({"risk_level": max_risk_level})

    item = get_tenant_ticket_or_none(tenant_id, ticket_id)
    if item:
        now = datetime.now(timezone.utc)
        requested_at = now.isoformat()
        approval_deadline = (now + timedelta(days=APPROVAL_TIMEOUT_DAYS)).isoformat()
        pending_decision = item.get("pending_decision") or {}
        if not isinstance(pending_decision, dict):
            pending_decision = {}
        options = _build_plan_options(plans)
        recommended_option_id = options[0]["option_id"] if options else None
        pending_decision.update({
            "decision_id": "plan-selection",
            "question": "Selecciona un plan de resolucion o escribe el tuyo.",
            "options": options,
            "recommended_option_id": recommended_option_id,
            "max_risk_level": max_risk_level,
            "required_approver_role": requirement["required_approver_role"],
            "approver_label": requirement["approver_label"],
            "publicly_approvable": requirement["publicly_approvable"],
            "task_token": task_token,
            "requested_at": requested_at,
            "approval_deadline": approval_deadline,
        })
        update_ticket_fields(tenant_id, ticket_id, {
            "status": "PREAPROBADO",
            "execution_status": "AWAITING_APPROVAL",
            "pending_decision": pending_decision,
        })
        publish_ticket_status_notification(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            status="PREAPROBADO",
            attempt_count=event.get("attemptCount"),
        )
        logger.info(
            "Updated pending_decision for ticket %s: risk=%s role=%s options=%d",
            ticket_id,
            max_risk_level,
            requirement["required_approver_role"],
            len(options),
        )

    return {
        "taskToken": task_token,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
    }
