import json
import logging
import os
from datetime import timedelta

import requests

from src.shared.auth import create_access_token
from src.shared.config import get_settings
from src.shared.errors import ValidationError

logger = logging.getLogger(__name__)

RISK_LEVEL_ORDER = {"basic": 1, "controlled": 2, "risky": 3}


def _compute_max_risk_level(plan: dict) -> str:
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    if not steps:
        return "basic"
    max_level = "basic"
    max_order = 1
    for step in steps:
        level = (step.get("risk_level") or "basic").lower()
        order = RISK_LEVEL_ORDER.get(level, 1)
        if order > max_order:
            max_order = order
            max_level = level
    return max_level


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


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    subject = event.get("subject", "")
    description = event.get("description", "")
    phase = event.get("phase", "assessment")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    settings = get_settings()
    base_url = (settings.agents_function_base_url or "").strip()
    victor_route = (settings.agents_function_route_victor or "/api/agents/VictorDurableAgent/run").strip()

    if not base_url:
        logger.warning("AGENTS_FUNCTION_BASE_URL not configured, returning default response")
        if phase == "assessment":
            return {
                "canResolve": False,
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
            }
        return {
            "plan": {"steps": [], "source": "fallback"},
            "planSource": "fallback",
            "maxRiskLevel": "basic",
            "ticketId": ticket_id,
            "tenantId": tenant_id,
        }

    full_url = f"{base_url}{victor_route}"
    token = _build_service_token(int(tenant_id))
    timeout_seconds = int(os.environ.get("VICTOR_TIMEOUT_SECONDS", "300"))

    plan_from_event = event.get("plan")
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
        logger.error("Victor Azure timed out for ticket %s", ticket_id)
        if phase == "assessment":
            return {
                "canResolve": False,
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
            }
        raise ValidationError("Victor Azure timed out during plan generation")
    except requests.exceptions.RequestException as exc:
        logger.error("Error calling Victor Azure: %s", exc)
        if phase == "assessment":
            return {
                "canResolve": False,
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
            }
        raise ValidationError(f"Victor Azure returned an error: {exc}")

    if phase == "assessment":
        can_resolve = bool(data.get("can_resolve", data.get("canResolve", False)))
        return {
            "canResolve": can_resolve,
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "subject": subject,
            "description": description,
        }

    if phase == "execute":
        return {
            "executionResult": data,
            "ticketId": ticket_id,
            "tenantId": tenant_id,
        }

    plan = data.get("plan", data)
    max_risk_level = _compute_max_risk_level(plan)
    return {
        "plan": plan,
        "planSource": "victor_azure",
        "maxRiskLevel": max_risk_level,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
    }
