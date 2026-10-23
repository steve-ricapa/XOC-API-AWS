import json
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.shared.config import get_settings
from src.shared.dependencies import require_access_claims
from src.shared.errors import AppError, ForbiddenError, NotFoundError, ValidationError
from src.shared.logging import logger
from src.shared.risk_config import approval_requirement, is_role_sufficient
from src.notifications.tickets import publish_ticket_status_notification
from src.shared.tickets_store import (
    build_new_ticket_item,
    build_secondary_index_fields,
    get_tenant_ticket_or_404,
    get_ticket_by_id_or_none,
    list_tenant_tickets,
    now_iso,
    resolve_executable_plan,
    serialize_ticket,
    table,
    ticket_key,
    update_ticket_fields,
)

settings = get_settings()
eventbridge = boto3.client("events")
stepfunctions = boto3.client("stepfunctions")

PLATFORM_OPERATOR_ROLES = {"ADMIN_XOC", "SUPERADMIN"}


def _emit_event(event_name: str, tenant_id: int, ticket_id: str, payload: dict):
    try:
        eventbridge.put_events(
            Entries=[
                {
                    "Source": "xoc.ticket",
                    "DetailType": event_name,
                    "Detail": json.dumps({"tenant_id": tenant_id, "ticket_id": ticket_id, **payload}, default=str),
                    "EventBusName": settings.event_bus_name,
                }
            ]
        )
    except Exception as exc:
        logger.warning("Failed to emit ticket event %s: %s", event_name, exc)


def _get_ticket_or_404(tenant_id: int, ticket_id: str) -> dict:
    return get_tenant_ticket_or_404(tenant_id, ticket_id)


def _plan_steps_for_option(item: dict, option_id: str) -> dict | None:
    """Devuelve el plan (pasos) de una opcion seleccionada en pending_decision.

    Retorna None si la opcion no existe o no trae pasos.
    """
    pending_decision = item.get("pending_decision") or {}
    if not isinstance(pending_decision, dict):
        pending_decision = {}
    options = pending_decision.get("options") or []
    for option in options:
        if not isinstance(option, dict):
            continue
        if option.get("option_id") != option_id:
            continue
        steps = option.get("plan")
        if isinstance(steps, list) and len(steps) > 0:
            return {"steps": steps, "source": "plan-selection"}
    return None


def _assert_can_approve(claims: dict, item: dict) -> None:
    """Valida que el rol del usuario alcance el rol requerido por el contrato.

    El rol requerido viene de pending_decision (escrito por wait_for_approval).
    Si por cualquier motivo no existe, se conserva el comportamiento previo
    (USER puede aprobar) en lugar de bloquear tickets legados.
    """
    pending_decision = item.get("pending_decision") or {}
    if not isinstance(pending_decision, dict):
        pending_decision = {}
    required_role = pending_decision.get("required_approver_role", "USER")
    user_role = (claims.get("role") or "").upper()
    if not is_role_sufficient(user_role, required_role):
        raise ForbiddenError(f"Insufficient role to approve this ticket. Required: {required_role}")


def _governance_violation_message(user_role: str, requirement: dict) -> str:
    """Mensaje en claro cuando la gobernanza bloquea la solucion propuesta."""
    role = user_role or "SIN ROL"
    return (
        f"Tu rol ({role}) no puede aprobar una solucion de riesgo "
        f"'{requirement.get('max_risk_level', 'basic')}'. "
        f"Esta solucion requiere la aprobacion de {requirement.get('approver_label', 'Admin del tenant')} "
        f"({requirement.get('required_approver_role', 'ADMIN')}). "
        "Pide a un administrador que la ejecute o escribe una solucion de menor riesgo."
    )


def _normalize_custom_steps(steps: list) -> list:
    """Normaliza los pasos escritos por el usuario para poder ejecutarlos."""
    normalized = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        command = step.get("command")
        if not command:
            continue
        normalized.append({
            "step_id": step.get("step_id") or f"custom-{idx + 1}",
            "order": step.get("order", idx + 1),
            "action": step.get("action", "shell"),
            "command": command,
            "description": step.get("description", ""),
            "risk_level": step.get("risk_level") or "",
        })
    return normalized


def _evaluate_solution(steps: list, source: str = "custom", analysis: str = "") -> tuple[dict, dict]:
    """Evalua la solucion propuesta y devuelve (plan, requisito de aprobacion).

    La gobernanza se aplica a la solucion resultante (no al riesgo maximo del
    ticket): un plan propio seguro se puede aprobar aunque el ticket tenga
    alternativas de mayor riesgo. El riesgo del plan se recalcula con la
    inferencia por comando, de modo que un `risk_level` declarado no puede
    maquillar un comando destructivo.
    """
    plan = {"steps": steps, "source": source}
    if analysis:
        plan["analysis"] = analysis
    requirement = approval_requirement(plan)
    plan["risk_level"] = requirement["max_risk_level"]
    return plan, requirement


def _analyze_custom_instructions(
    *,
    ticket_id: str,
    tenant_id: int,
    subject: str,
    description: str,
    custom_instructions: str,
) -> tuple[list, str]:
    """Convierte instrucciones libres en pasos ejecutables usando Victor.

    La generacion ocurre dentro del request del API. Cualquier fallo del
    runtime se traduce en un 502 explicito; nunca en un 500 opaco.
    """
    from src.handlers.workers import assess_ticket_automation

    try:
        result = assess_ticket_automation.handler(
            {
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
                "phase": "plan",
                "generatePlans": False,
                "persist": False,
                "customInstructions": custom_instructions,
            },
            None,
        )
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "Custom plan generation failed for ticket %s (tenant=%s): %s",
            ticket_id,
            tenant_id,
            exc,
        )
        raise AppError(
            "No se pudo analizar la solucion con Victor. Intenta de nuevo o escribe los comandos manualmente.",
            status_code=502,
            code="custom_plan_generation_failed",
        )

    plans = result.get("plans") or []
    if not plans:
        raise ValidationError("Victor could not generate a plan from your instructions")
    recommended = plans[0]
    steps = recommended.get("plan", []) or []
    analysis = recommended.get("analysis") or result.get("analysis") or ""
    if not analysis:
        analysis = recommended.get("plan_summary") or ""
    return steps, analysis


def _resolve_ticket_for_action(claims: dict, ticket_id: str) -> dict:
    role = (claims.get("role") or "").upper()
    if role in PLATFORM_OPERATOR_ROLES:
        item = get_ticket_by_id_or_none(ticket_id)
        if not item:
            raise NotFoundError("Ticket not found")
        return item
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    return _get_ticket_or_404(tenant_id, ticket_id)


def _json_default(value):
    """Serializa valores de DynamoDB (Decimal) en el payload de Step Functions."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def _resume_workflow(item: dict, approved: bool, selected_plan=None) -> None:
    pending_decision = item.get("pending_decision") or {}
    if not isinstance(pending_decision, dict):
        pending_decision = {}
    task_token = pending_decision.get("task_token") or pending_decision.get("taskToken")
    if not task_token:
        raise ValidationError("No pending approval found for this ticket")
    output = {"approved": bool(approved)}
    if selected_plan is not None:
        output["selected_plan"] = selected_plan
    try:
        stepfunctions.send_task_success(taskToken=task_token, output=json.dumps(output, default=_json_default))
    except Exception as exc:
        logger.error("Failed to resume workflow for ticket %s: %s", item.get("ticket_id"), exc)
        raise AppError(status_code=502, message="Failed to resume the automation workflow")


router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("")
def list_tickets(claims: dict = Depends(require_access_claims), status: str | None = None, limit: int = 50):
    tenant_id = claims.get("tenantId") or claims.get("tenant_id")
    if not tenant_id:
        raise ValidationError("tenant_id not found in request context")
    tenant_id = int(tenant_id)
    return list_tenant_tickets(tenant_id, status=status, limit=limit)


@router.get("/{ticket_id}")
def get_ticket(ticket_id: str, claims: dict = Depends(require_access_claims)):
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    item = _get_ticket_or_404(tenant_id, ticket_id)
    return serialize_ticket(item)


@router.post("", status_code=201)
def create_ticket(payload: dict, claims: dict = Depends(require_access_claims)):
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    user_id = claims.get("userId") or claims.get("sub")
    if not payload or not payload.get("subject"):
        raise ValidationError("subject is required")
    ticket_id, item = build_new_ticket_item(payload, tenant_id, int(user_id) if user_id else None)
    table.put_item(Item=item)
    _emit_event("ticket.created", tenant_id, ticket_id, {"subject": payload["subject"], "status": item["status"]})
    return {"message": "Ticket created successfully", "ticket": serialize_ticket(item)}


def create_ticket_from_agent(
    tenant_id: int,
    subject: str,
    description: str | None = None,
    status: str | None = "PENDING",
    severity: str | None = "medium",
    metadata: dict | None = None,
    user_id: int | None = None,
) -> dict:
    """Create a ticket from an agent (SOPHIA/VICTOR) and emit ticket.created event."""
    ticket_payload = {
        "subject": subject,
        "description": description or "",
        "status": status or "PENDING",
        "priority": severity or "medium",
        "metadata": {
            "source": "agent",
            **(metadata or {}),
        },
    }
    ticket_id, item = build_new_ticket_item(ticket_payload, tenant_id, user_id)
    table.put_item(Item=item)
    _emit_event("ticket.created", tenant_id, ticket_id, {"subject": subject, "status": item["status"]})
    logger.info("Ticket created from agent: tenant=%s ticket=%s subject=%s", tenant_id, ticket_id, subject)
    return {"ticket_id": ticket_id, "ticket": serialize_ticket(item)}


@router.post("/agent", status_code=201)
def create_ticket_from_agent_endpoint(
    payload: dict,
    claims: dict = Depends(require_access_claims),
):
    """Endpoint for agents (SOPHIA/VICTOR) to create tickets programmatically."""
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    if not tenant_id:
        raise ValidationError("tenant_id not found in request context")
    subject = payload.get("subject")
    if not subject:
        raise ValidationError("subject is required")
    result = create_ticket_from_agent(
        tenant_id=tenant_id,
        subject=subject,
        description=payload.get("description"),
        status=payload.get("status", "PENDING"),
        severity=payload.get("severity", "medium"),
        metadata=payload.get("metadata"),
        user_id=claims.get("userId") or claims.get("sub"),
    )
    return {"message": "Ticket created successfully by agent", **result}


@router.put("/{ticket_id}")
def update_ticket(ticket_id: str, payload: dict, claims: dict = Depends(require_access_claims)):
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    updated = update_ticket_fields(tenant_id, ticket_id, payload)
    event_name = "ticket.status_changed" if "status" in payload else "ticket.updated"
    _emit_event(event_name, tenant_id, ticket_id, {"status": updated.get("status")})
    if "status" in payload:
        publish_ticket_status_notification(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            status=str(updated.get("status") or ""),
        )
    return {"message": "Ticket updated successfully", "ticket": serialize_ticket(updated)}


@router.delete("/{ticket_id}")
def delete_ticket(ticket_id: str, claims: dict = Depends(require_access_claims)):
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    _get_ticket_or_404(tenant_id, ticket_id)
    table.delete_item(Key=ticket_key(tenant_id, ticket_id))
    _emit_event("ticket.deleted", tenant_id, ticket_id, {})
    return {"message": "Ticket deleted successfully"}


@router.patch("/{ticket_id}/approve")
def approve_ticket(ticket_id: str, claims: dict = Depends(require_access_claims)):
    item = _resolve_ticket_for_action(claims, ticket_id)
    tenant_id = int(item["tenant_id"])
    user_id = claims.get("userId") or claims.get("sub")
    if item.get("status") != "PREAPROBADO":
        raise ValidationError("Only PREAPROBADO tickets can be approved")
    _assert_can_approve(claims, item)
    selected_plan = resolve_executable_plan(item)
    if selected_plan is None:
        raise ValidationError("No action plan is available to execute for this ticket")
    _resume_workflow(item, approved=True, selected_plan=selected_plan)
    now = now_iso()
    secondary = build_secondary_index_fields(tenant_id, ticket_id, "APROBADO", item.get("created_at") or now)
    try:
        table.update_item(
            Key=ticket_key(tenant_id, ticket_id),
            UpdateExpression="SET #status = :status, #approved_by_user_id = :uid, #approved_at = :at, #updated_at = :now, #execution_status = :exec_status, #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
            ConditionExpression="#status = :preaprobado",
            ExpressionAttributeValues={
                ":status": "APROBADO",
                ":uid": int(user_id) if user_id else None,
                ":at": now,
                ":now": now,
                ":exec_status": "EXECUTING",
                ":preaprobado": "PREAPROBADO",
                ":gsi1pk": secondary["gsi1pk"],
                ":gsi1sk": secondary["gsi1sk"],
                ":gsi2pk": secondary["gsi2pk"],
                ":gsi2sk": secondary["gsi2sk"],
                ":gsi3pk": secondary["gsi3pk"],
                ":gsi3sk": secondary["gsi3sk"],
            },
            ExpressionAttributeNames={
                "#status": "status",
                "#approved_by_user_id": "approved_by_user_id",
                "#approved_at": "approved_at",
                "#updated_at": "updated_at",
                "#execution_status": "execution_status",
                "#gsi1pk": "gsi1pk",
                "#gsi1sk": "gsi1sk",
                "#gsi2pk": "gsi2pk",
                "#gsi2sk": "gsi2sk",
                "#gsi3pk": "gsi3pk",
                "#gsi3sk": "gsi3sk",
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ValidationError("Ticket approval state changed, please refresh and try again")
        raise
    updated = get_tenant_ticket_or_404(tenant_id, ticket_id)
    _emit_event("ticket.status_changed", tenant_id, ticket_id, {"status": "APROBADO"})
    publish_ticket_status_notification(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        status="APROBADO",
    )
    return {"message": "Ticket approved successfully", "ticket": serialize_ticket(updated)}


@router.patch("/{ticket_id}/reject")
def reject_ticket(ticket_id: str, claims: dict = Depends(require_access_claims)):
    item = _resolve_ticket_for_action(claims, ticket_id)
    tenant_id = int(item["tenant_id"])
    user_id = claims.get("userId") or claims.get("sub")
    if item.get("status") != "PREAPROBADO":
        raise ValidationError("Only PREAPROBADO tickets can be rejected")
    _assert_can_approve(claims, item)
    _resume_workflow(item, approved=False)
    now = now_iso()
    secondary = build_secondary_index_fields(tenant_id, ticket_id, "RECHAZADO", item.get("created_at") or now)
    try:
        table.update_item(
            Key=ticket_key(tenant_id, ticket_id),
            UpdateExpression="SET #status = :status, #rejected_by_user_id = :uid, #rejected_at = :at, #updated_at = :now, #execution_status = :exec_status, #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
            ConditionExpression="#status = :preaprobado",
            ExpressionAttributeValues={
                ":status": "RECHAZADO",
                ":uid": int(user_id) if user_id else None,
                ":at": now,
                ":now": now,
                ":exec_status": "REJECTED",
                ":preaprobado": "PREAPROBADO",
                ":gsi1pk": secondary["gsi1pk"],
                ":gsi1sk": secondary["gsi1sk"],
                ":gsi2pk": secondary["gsi2pk"],
                ":gsi2sk": secondary["gsi2sk"],
                ":gsi3pk": secondary["gsi3pk"],
                ":gsi3sk": secondary["gsi3sk"],
            },
            ExpressionAttributeNames={
                "#status": "status",
                "#rejected_by_user_id": "rejected_by_user_id",
                "#rejected_at": "rejected_at",
                "#updated_at": "updated_at",
                "#execution_status": "execution_status",
                "#gsi1pk": "gsi1pk",
                "#gsi1sk": "gsi1sk",
                "#gsi2pk": "gsi2pk",
                "#gsi2sk": "gsi2sk",
                "#gsi3pk": "gsi3pk",
                "#gsi3sk": "gsi3sk",
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ValidationError("Ticket approval state changed, please refresh and try again")
        raise
    updated = get_tenant_ticket_or_404(tenant_id, ticket_id)
    _emit_event("ticket.status_changed", tenant_id, ticket_id, {"status": "RECHAZADO"})
    publish_ticket_status_notification(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        status="RECHAZADO",
    )
    return {"message": "Ticket rejected successfully", "ticket": serialize_ticket(updated)}


@router.patch("/{ticket_id}/decision/select")
def select_ticket_decision(ticket_id: str, payload: dict, claims: dict = Depends(require_access_claims)):
    item = _resolve_ticket_for_action(claims, ticket_id)
    tenant_id = int(item.get("tenant_id") or 0)
    pending_decision = item.get("pending_decision")
    if not pending_decision or not isinstance(pending_decision, dict):
        raise ValidationError("No pending decision found for this ticket")
    user_role = (claims.get("role") or "").upper()
    options = pending_decision.get("options", [])
    selected_option_id = payload.get("selected_option_id")
    if not selected_option_id:
        raise ValidationError("selected_option_id is required")
    option_ids = [o.get("option_id") for o in options if isinstance(o, dict) and o.get("option_id")]
    if selected_option_id not in option_ids:
        raise ValidationError("selected_option_id is not a valid option")
    chosen_plan = _plan_steps_for_option(item, selected_option_id)
    if not chosen_plan:
        raise ValidationError("The selected option does not have executable steps")
    # Re-evaluate risk on the CHOSEN plan, not the originally recommended one,
    # so a riskier option cannot sneak past the approver role contract.
    requirement = approval_requirement(chosen_plan)
    if not is_role_sufficient(user_role, requirement["required_approver_role"]):
        raise ForbiddenError(
            f"Insufficient role to approve the selected plan. Required: {requirement['required_approver_role']}"
        )
    chosen_plan["risk_level"] = requirement["max_risk_level"]
    update_ticket_fields(tenant_id, ticket_id, {"action_plan": chosen_plan})
    _resume_workflow(item, approved=True, selected_plan=chosen_plan)
    now = now_iso()
    user_id = claims.get("userId") or claims.get("sub")
    created_at = item.get("created_at") or now
    secondary = build_secondary_index_fields(tenant_id, ticket_id, "APROBADO", created_at)
    table.update_item(
        Key=ticket_key(tenant_id, ticket_id),
        UpdateExpression="SET #pending_decision = :null, #status = :aprobado, #execution_status = :executing, #approved_by_user_id = :uid, #approved_at = :at, #updated_at = :now, #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
        ConditionExpression="#status = :preaprobado",
        ExpressionAttributeValues={
            ":null": None,
            ":aprobado": "APROBADO",
            ":executing": "EXECUTING",
            ":preaprobado": "PREAPROBADO",
            ":uid": int(user_id) if user_id else None,
            ":at": now,
            ":now": now,
            ":gsi1pk": secondary["gsi1pk"],
            ":gsi1sk": secondary["gsi1sk"],
            ":gsi2pk": secondary["gsi2pk"],
            ":gsi2sk": secondary["gsi2sk"],
            ":gsi3pk": secondary["gsi3pk"],
            ":gsi3sk": secondary["gsi3sk"],
        },
        ExpressionAttributeNames={
            "#pending_decision": "pending_decision",
            "#status": "status",
            "#execution_status": "execution_status",
            "#approved_by_user_id": "approved_by_user_id",
            "#approved_at": "approved_at",
            "#updated_at": "updated_at",
            "#gsi1pk": "gsi1pk",
            "#gsi1sk": "gsi1sk",
            "#gsi2pk": "gsi2pk",
            "#gsi2sk": "gsi2sk",
            "#gsi3pk": "gsi3pk",
            "#gsi3sk": "gsi3sk",
        },
    )
    updated = _get_ticket_or_404(tenant_id, ticket_id)
    _emit_event("ticket.status_changed", tenant_id, ticket_id, {"status": "APROBADO", "selected_option_id": selected_option_id})
    publish_ticket_status_notification(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        status="APROBADO",
    )
    return {"message": "Decision selected successfully", "ticket": serialize_ticket(updated)}


@router.patch("/{ticket_id}/decision/custom/preview")
def preview_ticket_custom_plan(ticket_id: str, payload: dict, claims: dict = Depends(require_access_claims)):
    """Analiza la solucion propuesta por el usuario y devuelve el plan de la IA.

    No cambia el estado del ticket. Devuelve el plan analizado por Victor (a
    partir de lo que escribio el usuario, no literal), el analisis de la IA, el
    riesgo efectivo, el rol requerido y si el usuario puede ejecutarlo.
    """
    item = _resolve_ticket_for_action(claims, ticket_id)
    tenant_id = int(item.get("tenant_id") or 0)
    pending_decision = item.get("pending_decision")
    if not pending_decision or not isinstance(pending_decision, dict):
        raise ValidationError("No pending decision found for this ticket")

    custom_instructions = payload.get("custom_instructions")
    custom_plan_steps = payload.get("custom_plan_steps")
    if not custom_instructions and not custom_plan_steps:
        raise ValidationError("custom_instructions or custom_plan_steps is required")

    analysis = ""
    if custom_plan_steps:
        if not isinstance(custom_plan_steps, list) or len(custom_plan_steps) == 0:
            raise ValidationError("custom_plan_steps must be a non-empty list of steps")
        steps = _normalize_custom_steps(custom_plan_steps)
    else:
        steps, analysis = _analyze_custom_instructions(
            ticket_id=ticket_id,
            tenant_id=tenant_id,
            subject=item.get("subject", ""),
            description=item.get("description", ""),
            custom_instructions=custom_instructions,
        )

    if not steps:
        raise ValidationError("The proposed solution did not produce any executable step")

    preview_plan, requirement = _evaluate_solution(steps, source="custom", analysis=analysis)
    user_role = (claims.get("role") or "").upper()
    can_execute = is_role_sufficient(user_role, requirement["required_approver_role"])
    return {
        "message": "Custom plan preview generated",
        "analysis": analysis,
        "plan": preview_plan,
        "requirement": requirement,
        "can_execute": can_execute,
        "governance_message": "" if can_execute else _governance_violation_message(user_role, requirement),
        "ticket_id": ticket_id,
    }


@router.patch("/{ticket_id}/decision/custom")
def custom_ticket_plan(ticket_id: str, payload: dict, claims: dict = Depends(require_access_claims)):
    """Registra un plan custom del usuario (texto libre o pasos JSON) y lo ejecuta.

    - `custom_instructions`: texto libre que Victor convierte en un plan ejecutable.
    - `custom_plan_steps`: pasos JSON directos (estructurados) escritos por el usuario.

    La gobernanza se evalua sobre la solucion resultante: si el plan propuesto
    exige un rol que el usuario no tiene, se responde 403 con un mensaje claro
    indicando que rol hace falta.
    """
    item = _resolve_ticket_for_action(claims, ticket_id)
    tenant_id = int(item.get("tenant_id") or 0)
    pending_decision = item.get("pending_decision")
    if not pending_decision or not isinstance(pending_decision, dict):
        raise ValidationError("No pending decision found for this ticket")
    user_role = (claims.get("role") or "").upper()

    custom_instructions = payload.get("custom_instructions")
    custom_plan_steps = payload.get("custom_plan_steps")
    if not custom_instructions and not custom_plan_steps:
        raise ValidationError("custom_instructions or custom_plan_steps is required")

    analysis = ""
    if custom_plan_steps:
        if not isinstance(custom_plan_steps, list) or len(custom_plan_steps) == 0:
            raise ValidationError("custom_plan_steps must be a non-empty list of steps")
        steps = _normalize_custom_steps(custom_plan_steps)
        if not steps:
            raise ValidationError("custom_plan_steps must contain at least one step with a command")
    else:
        steps, analysis = _analyze_custom_instructions(
            ticket_id=ticket_id,
            tenant_id=tenant_id,
            subject=item.get("subject", ""),
            description=item.get("description", ""),
            custom_instructions=custom_instructions,
        )
        if not steps:
            raise ValidationError("Victor could not generate an executable plan from your instructions")

    # Los planes custom evitan la evaluacion original de Victor, asi que el
    # riesgo de los pasos propuestos se recalcula (incluida la inferencia por
    # comando) antes de autorizar la ejecucion.
    selected_plan, requirement = _evaluate_solution(steps, source="custom", analysis=analysis)
    if not is_role_sufficient(user_role, requirement["required_approver_role"]):
        raise ForbiddenError(_governance_violation_message(user_role, requirement))

    update_ticket_fields(tenant_id, ticket_id, {"action_plan": selected_plan})
    _resume_workflow(item, approved=True, selected_plan=selected_plan)
    now = now_iso()
    user_id = claims.get("userId") or claims.get("sub")
    created_at = item.get("created_at") or now
    secondary = build_secondary_index_fields(tenant_id, ticket_id, "APROBADO", created_at)
    table.update_item(
        Key=ticket_key(tenant_id, ticket_id),
        UpdateExpression="SET #pending_decision = :null, #status = :aprobado, #execution_status = :executing, #approved_by_user_id = :uid, #approved_at = :at, #updated_at = :now, #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
        ConditionExpression="#status = :preaprobado",
        ExpressionAttributeValues={
            ":null": None,
            ":aprobado": "APROBADO",
            ":executing": "EXECUTING",
            ":preaprobado": "PREAPROBADO",
            ":uid": int(user_id) if user_id else None,
            ":at": now,
            ":now": now,
            ":gsi1pk": secondary["gsi1pk"],
            ":gsi1sk": secondary["gsi1sk"],
            ":gsi2pk": secondary["gsi2pk"],
            ":gsi2sk": secondary["gsi2sk"],
            ":gsi3pk": secondary["gsi3pk"],
            ":gsi3sk": secondary["gsi3sk"],
        },
        ExpressionAttributeNames={
            "#pending_decision": "pending_decision",
            "#status": "status",
            "#execution_status": "execution_status",
            "#approved_by_user_id": "approved_by_user_id",
            "#approved_at": "approved_at",
            "#updated_at": "updated_at",
            "#gsi1pk": "gsi1pk",
            "#gsi1sk": "gsi1sk",
            "#gsi2pk": "gsi2pk",
            "#gsi2sk": "gsi2sk",
            "#gsi3pk": "gsi3pk",
            "#gsi3sk": "gsi3sk",
        },
    )
    updated = _get_ticket_or_404(tenant_id, ticket_id)
    _emit_event("ticket.status_changed", tenant_id, ticket_id, {"status": "APROBADO", "custom": True})
    publish_ticket_status_notification(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        status="APROBADO",
    )
    return {"message": "Custom plan submitted successfully", "ticket": serialize_ticket(updated)}


app = FastAPI(
    title="XOC Tickets API (DynamoDB)",
    version="1.0.0",
    docs_url="/docs" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-Id", "X-Superadmin-Confirm"],
    expose_headers=["X-Request-Id"],
)


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message, "code": exc.code})


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error in Tickets DynamoDB API")
    return JSONResponse(status_code=500, content={"error": "Internal server error", "code": "internal_error"})


app.include_router(router)

handler = Mangum(app)
