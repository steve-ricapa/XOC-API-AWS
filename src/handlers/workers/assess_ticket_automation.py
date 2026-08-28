import logging
import os
from datetime import timedelta

import requests

from src.shared.auth import create_access_token
from src.shared.config import get_settings
from src.shared.errors import ValidationError
from src.shared.risk_config import approval_requirement, DEFAULT_RISK_LEVEL
from src.notifications.tickets import publish_ticket_status_notification

logger = logging.getLogger(__name__)


def _build_service_token(tenant_id: int) -> str:
    claims = {
        "scopes": ["agent:invoke"],
        "tenant_id": tenant_id,
        "agent_type": "VICTOR",
    }
    return create_access_token(
        identity=f"agent-runtime-{tenant_id}-VICTOR",
        additional_claims=claims,
        expires_delta=timedelta(minutes=15),
    )


def _resolve_victor_endpoint(tenant_id: int) -> tuple[str | None, str, str]:
    """Resuelve el endpoint de Victor a usar para un tenant.

    Orden de prioridad:
      1. URL global configurada por env (AGENTS_FUNCTION_BASE_URL_VICTOR): Victor on-premise
         dedicado. Devuelve source "global".
      2. Runtime settings del tenant en RDS (function_base_url + function_route_victor):
         Victor on-premise por tenant. Devuelve source "on_premise".
      3. Nada configurado -> (None, route, "fallback").

    La llamada a RDS es opcional y con tolerancia a fallos: si no hay secretos de
    BD o el lookup falla, se degrada al comportamiento sin endpoint.
    """
    settings = get_settings()
    default_route = (settings.agents_function_route_victor or "/api/agents/VictorDurableAgent/run").strip()

    base_url = (settings.agents_function_base_url_victor or "").strip()
    if base_url:
        return base_url, default_route, "global"

    try:
        from src.persistence.db import session_scope
        from src.persistence.models import TenantRuntimeSettings

        with session_scope() as session:
            runtime = session.query(TenantRuntimeSettings).filter(
                TenantRuntimeSettings.tenant_id == int(tenant_id),
                TenantRuntimeSettings.is_active == True,
            ).first()
            if runtime and runtime.function_base_url:
                route = (runtime.function_route_victor or default_route).strip()
                return (runtime.function_base_url or "").strip(), route, "on_premise"
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("Could not resolve on-premise Victor runtime settings for tenant %s: %s", tenant_id, exc)

    return None, default_route, "fallback"


def _payload_for(phase: str, subject: str, description: str, ticket_id: str, tenant_id: int, plan_from_event, similar_case_plan=None, similar_case_solution=None) -> dict:
    payload = {
        "message": f"[{phase}] Ticket: {subject}. {description}",
        "subject": subject,
        "description": description,
        "phase": phase,
        "ticket_id": ticket_id,
        "tenant_id": int(tenant_id),
    }
    if plan_from_event is not None:
        payload["plan"] = plan_from_event
    if similar_case_plan is not None:
        payload["similar_case_plan"] = similar_case_plan
    if similar_case_solution is not None:
        payload["similar_case_solution"] = similar_case_solution
    return payload


def _plan_has_steps(plan) -> bool:
    """True si el plan trae al menos un paso accionable.

    Acepta {"steps": [...]}, [ {...} ], {"plan": {"steps": [...]}, ...}.
    """
    steps = []
    if isinstance(plan, dict):
        nested = plan.get("plan")
        if isinstance(nested, dict):
            steps = nested.get("steps", [])
        elif isinstance(nested, list):
            steps = nested
        else:
            steps = plan.get("steps", [])
    elif isinstance(plan, list):
        steps = plan
    return isinstance(steps, list) and len(steps) > 0


def _fallback_response(phase: str, ticket_id: str, tenant_id: int, subject: str, description: str, source: str) -> dict:
    if phase == "assessment":
        return {
            "canResolve": False,
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "subject": subject,
            "description": description,
            "victorSource": source,
        }
    requirement = approval_requirement(None)
    return {
        "plan": {"steps": [], "source": "fallback"},
        "planSource": "fallback",
        "maxRiskLevel": DEFAULT_RISK_LEVEL,
        "approval": requirement,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
        "victorSource": source,
    }


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    subject = event.get("subject", "")
    description = event.get("description", "")
    phase = event.get("phase", "assessment")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    base_url, victor_route, endpoint_source = _resolve_victor_endpoint(int(tenant_id))

    if not base_url:
        logger.warning("Victor endpoint not configured for tenant %s (source=%s), returning default response", tenant_id, endpoint_source)
        return _fallback_response(phase, ticket_id, tenant_id, subject, description, endpoint_source)

    full_url = f"{base_url}{victor_route}"
    token = _build_service_token(int(tenant_id))
    timeout_seconds = int(os.environ.get("VICTOR_TIMEOUT_SECONDS", "300"))

    plan_from_event = event.get("plan")
    similar_case_plan = event.get("similarCasePlan")
    similar_case_solution = event.get("similarCaseSolution")
    payload = _payload_for(phase, subject, description, ticket_id, tenant_id, plan_from_event, similar_case_plan, similar_case_solution)

    try:
        response = requests.post(
            full_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        logger.error("Victor timed out for ticket %s (source=%s)", ticket_id, endpoint_source)
        if phase == "assessment":
            return {
                "canResolve": False,
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
                "victorSource": endpoint_source,
            }
        raise ValidationError("Victor timed out during plan generation")
    except requests.exceptions.RequestException as exc:
        logger.error("Error calling Victor: %s (source=%s)", exc, endpoint_source)
        if phase == "assessment":
            return {
                "canResolve": False,
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
                "victorSource": endpoint_source,
            }
        raise ValidationError(f"Victor returned an error: {exc}")

    if phase == "assessment":
        can_resolve = bool(data.get("can_resolve", data.get("canResolve", False)))
        return {
            "canResolve": can_resolve,
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "subject": subject,
            "description": description,
            "victorSource": endpoint_source,
        }

    if phase == "execute":
        if not _plan_has_steps(plan_from_event):
            logger.error("Execute phase reached with empty plan for ticket %s", ticket_id)
            raise ValidationError("Cannot execute: no action plan steps present for this ticket")
        all_success = bool(data.get("all_success", False))
        if all_success:
            step_results = data.get("step_results", [])
            summary_parts = []
            for r in (step_results if isinstance(step_results, list) else []):
                if isinstance(r, dict):
                    tool = r.get("tool", r.get("action", "?"))
                    ok = "OK" if r.get("success", True) else "FAIL"
                    detail = (r.get("output") or r.get("result") or "")[:100]
                    summary_parts.append(f"{tool}: {ok}" + (f" ({detail})" if detail else ""))
                else:
                    summary_parts.append(str(r)[:100])
            execution_summary = "; ".join(summary_parts)[:500] or str(data)[:500]
            try:
                from src.shared.tickets_store import update_ticket_fields
                update_ticket_fields(int(tenant_id), ticket_id, {
                    "status": "RESUELTO",
                    "execution_status": "EXECUTED",
                    "execution_summary": execution_summary,
                })
                publish_ticket_status_notification(
                    tenant_id=int(tenant_id),
                    ticket_id=ticket_id,
                    status="RESUELTO",
                )
                logger.info("Ticket %s marked as RESUELTO after successful execution", ticket_id)
            except Exception as exc:
                logger.error("Failed to update ticket %s to RESUELTO: %s", ticket_id, exc)
        return {
            "executionResult": data,
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "victorSource": endpoint_source,
        }

    plan = data.get("plan", data)
    if not _plan_has_steps(plan):
        logger.error("Victor returned an empty plan for ticket %s (ticket will not be executable)", ticket_id)
        raise ValidationError("Victor returned an empty plan - no steps to execute")
    requirement = approval_requirement(plan)
    try:
        from src.shared.tickets_store import update_ticket_fields
        update_ticket_fields(int(tenant_id), ticket_id, {"action_plan": plan})
    except Exception as exc:
        logger.warning("Failed to save action_plan for ticket %s: %s", ticket_id, exc)
    return {
        "plan": plan,
        "planSource": "victor_azure" if endpoint_source == "global" else "victor_on_premise",
        "maxRiskLevel": requirement["max_risk_level"],
        "approval": requirement,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
        "victorSource": endpoint_source,
    }
