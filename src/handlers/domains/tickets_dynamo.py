import json
import boto3
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.shared.config import get_settings
from src.shared.dependencies import require_access_claims
from src.shared.errors import AppError, ForbiddenError, NotFoundError, ValidationError
from src.shared.logging import logger
from src.shared.risk_config import is_role_sufficient
from src.notifications.tickets import publish_ticket_status_notification
from src.shared.tickets_store import (
    build_new_ticket_item,
    build_secondary_index_fields,
    get_tenant_ticket_or_404,
    get_ticket_by_id_or_none,
    list_tenant_tickets,
    now_iso,
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


def _resolve_ticket_for_action(claims: dict, ticket_id: str) -> dict:
    role = (claims.get("role") or "").upper()
    if role in PLATFORM_OPERATOR_ROLES:
        item = get_ticket_by_id_or_none(ticket_id)
        if not item:
            raise NotFoundError("Ticket not found")
        return item
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    return get_tenant_ticket_or_404(tenant_id, ticket_id)


def _resume_workflow(item: dict, approved: bool) -> None:
    pending_decision = item.get("pending_decision") or {}
    if not isinstance(pending_decision, dict):
        pending_decision = {}
    task_token = pending_decision.get("task_token") or pending_decision.get("taskToken")
    if not task_token:
        raise ValidationError("No pending approval found for this ticket")
    output = json.dumps({"approved": bool(approved)})
    try:
        stepfunctions.send_task_success(taskToken=task_token, output=output)
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
    _resume_workflow(item, approved=True)
    now = now_iso()
    secondary = build_secondary_index_fields(tenant_id, ticket_id, "APROBADO", item.get("created_at") or now)
    table.update_item(
        Key=ticket_key(tenant_id, ticket_id),
        UpdateExpression="SET #status = :status, #approved_by_user_id = :uid, #approved_at = :at, #updated_at = :now, #execution_status = :exec_status, #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
        ExpressionAttributeValues={
            ":status": "APROBADO",
            ":uid": int(user_id) if user_id else None,
            ":at": now,
            ":now": now,
            ":exec_status": "EXECUTING",
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
    table.update_item(
        Key=ticket_key(tenant_id, ticket_id),
        UpdateExpression="SET #status = :status, #rejected_by_user_id = :uid, #rejected_at = :at, #updated_at = :now, #execution_status = :exec_status, #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
        ExpressionAttributeValues={
            ":status": "RECHAZADO",
            ":uid": int(user_id) if user_id else None,
            ":at": now,
            ":now": now,
            ":exec_status": "REJECTED",
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
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    item = _get_ticket_or_404(tenant_id, ticket_id)
    pending_decision = item.get("pending_decision")
    if not pending_decision or not isinstance(pending_decision, dict):
        raise ValidationError("No pending decision found for this ticket")
    required_role = pending_decision.get("required_approver_role", "USER")
    user_role = (claims.get("role") or "").upper()
    if not is_role_sufficient(user_role, required_role):
        raise ForbiddenError("Insufficient role to approve this decision")
    options = pending_decision.get("options", [])
    selected_option_id = payload.get("selected_option_id")
    if not selected_option_id:
        raise ValidationError("selected_option_id is required")
    option_ids = [o.get("option_id") for o in options if isinstance(o, dict) and o.get("option_id")]
    if selected_option_id not in option_ids:
        raise ValidationError("selected_option_id is not a valid option")
    now = now_iso()
    table.update_item(
        Key=ticket_key(tenant_id, ticket_id),
        UpdateExpression="SET #pending_decision = :null, #execution_status = :run, #updated_at = :now",
        ExpressionAttributeValues={":null": None, ":run": "RUNNING", ":now": now},
        ExpressionAttributeNames={
            "#pending_decision": "pending_decision",
            "#execution_status": "execution_status",
            "#updated_at": "updated_at",
        },
    )
    updated = _get_ticket_or_404(tenant_id, ticket_id)
    _emit_event("ticket.decision_selected", tenant_id, ticket_id, {"selected_option_id": selected_option_id})
    return {"message": "Decision selected successfully", "ticket": serialize_ticket(updated)}


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
