from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import Ticket, User
from src.shared.context import log_audit
from src.shared.dependencies import get_current_user, get_current_user_optional, get_request_claims, get_request_company_context
from src.shared.errors import ForbiddenError, NotFoundError, ValidationError
from src.shared.schemas import (
    AgentCreateTicketRequest,
    AgentCreateTicketResponse,
    CreateTicketRequest,
    CreateTicketResponse,
    DeleteTicketResponse,
    SelectDecisionRequest,
    TicketResponse,
    TicketsListResponse,
    UpdateTicketRequest,
    UpdateTicketResponse,
)


router = APIRouter(prefix="/api/tickets", tags=["tickets"])

VALID_TICKET_STATUSES = [
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
]


def _serialize_ticket(ticket: Ticket, session: Session, include_creator: bool = True) -> TicketResponse:
    creator = None
    if include_creator and ticket.created_by_user_id:
        creator = session.get(User, ticket.created_by_user_id)
    return TicketResponse(**ticket.to_dict(include_creator=include_creator, creator=creator))


def _get_ticket_or_404(session: Session, company_id: int, ticket_id: int) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if not ticket or ticket.company_id != company_id:
        raise NotFoundError("Ticket not found")
    return ticket


def _validate_pending_decision_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValidationError("pending_decision must be an object")

    decision_id = payload.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise ValidationError("pending_decision.decision_id is required and must be a string")

    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValidationError("pending_decision.question is required and must be a string")

    options = payload.get("options")
    if not isinstance(options, list) or len(options) < 2:
        raise ValidationError("pending_decision.options must be an array with at least 2 options")

    seen_option_ids = set()
    for option in options:
        if not isinstance(option, dict):
            raise ValidationError("Each pending_decision option must be an object")
        option_id = option.get("option_id")
        title = option.get("title")
        if not isinstance(option_id, str) or not option_id.strip():
            raise ValidationError("Each pending_decision option requires option_id as a string")
        if not isinstance(title, str) or not title.strip():
            raise ValidationError("Each pending_decision option requires title as a string")
        normalized_option_id = option_id.strip()
        if normalized_option_id in seen_option_ids:
            raise ValidationError("pending_decision option_id values must be unique")
        seen_option_ids.add(normalized_option_id)

    recommended_option_id = payload.get("recommended_option_id")
    if recommended_option_id is not None:
        if not isinstance(recommended_option_id, str) or not recommended_option_id.strip():
            raise ValidationError("pending_decision.recommended_option_id must be a string when provided")
        if recommended_option_id.strip() not in seen_option_ids:
            raise ValidationError("pending_decision.recommended_option_id must exist in options")

    selected_option_id = payload.get("selected_option_id")
    if selected_option_id is not None:
        if not isinstance(selected_option_id, str) or not selected_option_id.strip():
            raise ValidationError("pending_decision.selected_option_id must be a string when provided")
        if selected_option_id.strip() not in seen_option_ids:
            raise ValidationError("pending_decision.selected_option_id must exist in options")

    decision_status = payload.get("status")
    if decision_status is not None:
        valid_statuses = {"PENDING", "SELECTED", "EXPIRED", "CANCELLED"}
        if not isinstance(decision_status, str) or decision_status.strip() not in valid_statuses:
            raise ValidationError(f'pending_decision.status must be one of: {", ".join(sorted(valid_statuses))}')


@router.get("", response_model=TicketsListResponse)
def get_tickets(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    status: str | None = None,
) -> TicketsListResponse:
    query = select(Ticket).where(Ticket.company_id == current_user.company_id)
    if status:
        query = query.where(Ticket.status == status)
    query = query.order_by(Ticket.created_at.desc())
    tickets = session.scalars(query).all()
    return TicketsListResponse(tickets=[_serialize_ticket(ticket, session) for ticket in tickets])


@router.get("/{ticket_id}", response_model=TicketResponse)
def get_ticket(
    ticket_id: int,
    company_context: tuple[int, str] = Depends(get_request_company_context),
    session: Session = Depends(get_db_session),
) -> TicketResponse:
    company_id, _actor = company_context
    ticket = _get_ticket_or_404(session, company_id, ticket_id)
    return _serialize_ticket(ticket, session)


@router.post("", response_model=CreateTicketResponse, status_code=201)
def create_ticket(
    payload: CreateTicketRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> CreateTicketResponse:
    if not payload.subject:
        raise ValidationError("subject is required")

    ticket = Ticket(
        company_id=current_user.company_id,
        created_by_user_id=current_user.id,
        subject=payload.subject,
        description=payload.description,
        status="PENDING",
    )
    session.add(ticket)
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="TICKET", entity_id=ticket.id, payload={"subject": payload.subject, "status": "PENDING"})
    session.commit()
    return CreateTicketResponse(message="Ticket created successfully", ticket=_serialize_ticket(ticket, session))


@router.put("/{ticket_id}", response_model=UpdateTicketResponse)
def update_ticket(
    ticket_id: int,
    payload: UpdateTicketRequest,
    company_context: tuple[int, str] = Depends(get_request_company_context),
    claims: dict = Depends(get_request_claims),
    current_user: User | None = Depends(get_current_user_optional),
    session: Session = Depends(get_db_session),
) -> UpdateTicketResponse:
    company_id, actor = company_context
    ticket = _get_ticket_or_404(session, company_id, ticket_id)

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ValidationError("Request body is required")

    if "subject" in data:
        ticket.subject = data["subject"]
    if "description" in data:
        ticket.description = data["description"]

    if "status" in data:
        next_status = data["status"]
        if next_status not in VALID_TICKET_STATUSES:
            raise ValidationError(f'status must be one of: {", ".join(VALID_TICKET_STATUSES)}')
        if next_status == "PENDIENTE_EJECUCION" and not data.get("capability_level"):
            raise ValidationError("capability_level is required when status=PENDIENTE_EJECUCION")
        if next_status in ("RESUELTO", "FALLIDO", "DERIVED"):
            execution_summary = data.get("execution_summary") or ticket.execution_summary
            execution_logs = data.get("execution_logs") if "execution_logs" in data else ticket.execution_logs
            if not execution_summary:
                raise ValidationError("execution_summary is required when status is RESUELTO, FALLIDO, or DERIVED")
            if not execution_logs:
                raise ValidationError("execution_logs is required when status is RESUELTO, FALLIDO, or DERIVED")
        ticket.status = next_status
        if next_status == "EXECUTED":
            ticket.executed_at = datetime.utcnow()

    pending_decision_payload = ticket.pending_decision
    if "pending_decision" in data:
        pending_decision_payload = data.get("pending_decision")
        if pending_decision_payload is not None:
            _validate_pending_decision_payload(pending_decision_payload)
        ticket.pending_decision = pending_decision_payload

    if "execution_status" in data:
        execution_status = data.get("execution_status")
        if execution_status is not None and not isinstance(execution_status, str):
            raise ValidationError("execution_status must be a string")
        if execution_status == "WAITING_DECISION" and not pending_decision_payload:
            raise ValidationError("pending_decision is required when execution_status=WAITING_DECISION")
        ticket.execution_status = execution_status

    if "execution_logs" in data:
        execution_logs = data.get("execution_logs")
        if execution_logs is not None and not isinstance(execution_logs, (dict, list)):
            raise ValidationError("execution_logs must be an object or array")
        ticket.execution_logs = execution_logs

    if "execution_summary" in data:
        execution_summary = data.get("execution_summary")
        if execution_summary is not None and not isinstance(execution_summary, str):
            raise ValidationError("execution_summary must be a string")
        ticket.execution_summary = execution_summary

    if "capability_level" in data:
        capability_level = data.get("capability_level")
        if capability_level is not None and not isinstance(capability_level, str):
            raise ValidationError("capability_level must be a string")
        ticket.capability_level = capability_level

    if "capability_policy_snapshot" in data:
        policy_snapshot = data.get("capability_policy_snapshot")
        if policy_snapshot is not None and not isinstance(policy_snapshot, dict):
            raise ValidationError("capability_policy_snapshot must be an object")
        ticket.capability_policy_snapshot = policy_snapshot

    if "decision_timeout_minutes" in data:
        timeout_minutes = data.get("decision_timeout_minutes")
        if timeout_minutes is not None and not isinstance(timeout_minutes, int):
            raise ValidationError("decision_timeout_minutes must be an integer")
        ticket.decision_timeout_minutes = timeout_minutes

    if "on_decision_timeout" in data:
        on_decision_timeout = data.get("on_decision_timeout")
        if on_decision_timeout is not None and not isinstance(on_decision_timeout, str):
            raise ValidationError("on_decision_timeout must be a string")
        ticket.on_decision_timeout = on_decision_timeout

    if "action_plan" in data or "action_plan_version" in data:
        if actor != "agent":
            raise ForbiddenError("Only agent tokens can update action plans")
        if "action_plan" in data:
            action_plan = data.get("action_plan")
            if action_plan is not None and not isinstance(action_plan, dict):
                raise ValidationError("action_plan must be an object")
            ticket.action_plan = action_plan
        if "action_plan_version" in data:
            plan_version = data.get("action_plan_version")
            if plan_version is not None and not isinstance(plan_version, str):
                raise ValidationError("action_plan_version must be a string")
            if isinstance(plan_version, str) and plan_version.strip():
                ticket.action_plan_version = plan_version

    actor_user_id = current_user.id if actor == "user" and current_user is not None else None
    log_audit(session, actor_user_id=actor_user_id, action="UPDATE", entity_type="TICKET", entity_id=ticket.id, payload={"subject": ticket.subject, "status": ticket.status})
    session.commit()
    return UpdateTicketResponse(message="Ticket updated successfully", ticket=_serialize_ticket(ticket, session))


@router.delete("/{ticket_id}", response_model=DeleteTicketResponse)
def delete_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> DeleteTicketResponse:
    ticket = _get_ticket_or_404(session, current_user.company_id, ticket_id)
    log_audit(session, actor_user_id=current_user.id, action="DELETE", entity_type="TICKET", entity_id=ticket.id, payload={"subject": ticket.subject})
    session.delete(ticket)
    session.commit()
    return DeleteTicketResponse(message="Ticket deleted successfully")


@router.patch("/{ticket_id}/approve", response_model=UpdateTicketResponse)
def approve_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> UpdateTicketResponse:
    ticket = _get_ticket_or_404(session, current_user.company_id, ticket_id)
    if ticket.status != "PREAPROBADO":
        raise ValidationError("Only PREAPROBADO tickets can be approved")
    ticket.status = "APROBADO"
    ticket.approved_by_user_id = current_user.id
    ticket.approved_at = datetime.utcnow()
    log_audit(session, actor_user_id=current_user.id, action="APPROVE", entity_type="TICKET", entity_id=ticket.id, payload={"status": ticket.status})
    session.commit()
    return UpdateTicketResponse(message="Ticket approved successfully", ticket=_serialize_ticket(ticket, session))


@router.patch("/{ticket_id}/reject", response_model=UpdateTicketResponse)
def reject_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> UpdateTicketResponse:
    ticket = _get_ticket_or_404(session, current_user.company_id, ticket_id)
    if ticket.status != "PREAPROBADO":
        raise ValidationError("Only PREAPROBADO tickets can be rejected")
    ticket.status = "RECHAZADO"
    ticket.rejected_by_user_id = current_user.id
    ticket.rejected_at = datetime.utcnow()
    log_audit(session, actor_user_id=current_user.id, action="REJECT", entity_type="TICKET", entity_id=ticket.id, payload={"status": ticket.status})
    session.commit()
    return UpdateTicketResponse(message="Ticket rejected successfully", ticket=_serialize_ticket(ticket, session))


@router.patch("/{ticket_id}/decision/select", response_model=UpdateTicketResponse)
def select_ticket_decision(
    ticket_id: int,
    payload: SelectDecisionRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> UpdateTicketResponse:
    ticket = _get_ticket_or_404(session, current_user.company_id, ticket_id)
    pending_decision = ticket.pending_decision
    if not pending_decision or not isinstance(pending_decision, dict):
        raise ValidationError("No pending decision found for this ticket")

    options = pending_decision.get("options")
    if not isinstance(options, list) or not options:
        raise ValidationError("pending_decision options are invalid")

    selected_option_id = payload.selected_option_id.strip()
    if not selected_option_id:
        raise ValidationError("selected_option_id is required")

    expected_decision_id = str(pending_decision.get("decision_id") or "").strip()
    if payload.decision_id is not None:
        if not payload.decision_id.strip():
            raise ValidationError("decision_id must be a non-empty string when provided")
        if payload.decision_id.strip() != expected_decision_id:
            raise ValidationError("decision_id does not match current pending_decision")

    option_ids = []
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("option_id")
        if isinstance(option_id, str) and option_id.strip():
            option_ids.append(option_id.strip())

    if selected_option_id not in option_ids:
        raise ValidationError("selected_option_id is not a valid option in pending_decision")

    selected_decision = dict(pending_decision)
    selected_decision["status"] = "SELECTED"
    selected_decision["selected_option_id"] = selected_option_id
    selected_decision["selected_by_user_id"] = current_user.id
    selected_decision["selected_at"] = datetime.utcnow().isoformat()
    if payload.selection_note is not None:
        selected_decision["selection_note"] = payload.selection_note

    execution_logs = ticket.execution_logs
    if execution_logs is None:
        execution_logs = {}
    elif not isinstance(execution_logs, dict):
        execution_logs = {"raw_execution_logs": execution_logs}

    decision_history = execution_logs.get("decision_history")
    if not isinstance(decision_history, list):
        decision_history = []
    decision_history.append(selected_decision)
    execution_logs["decision_history"] = decision_history

    ticket.execution_logs = execution_logs
    ticket.pending_decision = None
    if ticket.execution_status == "WAITING_DECISION" or not ticket.execution_status:
        ticket.execution_status = "RUNNING"

    log_audit(session, actor_user_id=current_user.id, action="SELECT_DECISION", entity_type="TICKET", entity_id=ticket.id, payload={"decision_id": expected_decision_id, "selected_option_id": selected_option_id})
    session.commit()
    return UpdateTicketResponse(message="Decision selected successfully", ticket=_serialize_ticket(ticket, session))


@router.post("/agent-create", response_model=AgentCreateTicketResponse, status_code=201)
def agent_create_ticket(
    payload: AgentCreateTicketRequest,
    claims: dict = Depends(get_request_claims),
    company_context: tuple[int, str] = Depends(get_request_company_context),
    session: Session = Depends(get_db_session),
) -> AgentCreateTicketResponse:
    scopes = claims.get("scopes") or []
    if isinstance(scopes, str):
        scopes = [scopes]
    if "agent:invoke" not in scopes:
        raise ForbiddenError("Agent scope required")

    company_id, _actor = company_context
    if not payload.subject:
        raise ValidationError("subject is required")
    if payload.status not in ["PENDING", "DERIVED"]:
        raise ValidationError('status must be one of: PENDING, DERIVED')

    ticket = Ticket(
        company_id=company_id,
        created_by_user_id=payload.userId,
        subject=payload.subject,
        description=payload.description,
        status=payload.status or "PENDING",
    )
    session.add(ticket)
    session.flush()
    log_audit(session, actor_user_id=None, action="CREATE", entity_type="TICKET", entity_id=ticket.id, payload={"subject": payload.subject, "status": ticket.status, "severity": payload.severity or "medium", "agent_created": True})
    session.commit()
    return AgentCreateTicketResponse(success=True, ticket_id=ticket.id, ticket=_serialize_ticket(ticket, session, include_creator=False))
