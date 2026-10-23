import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from src.shared.auth import create_access_token
from src.shared.config import get_settings
from src.shared.errors import ValidationError
from src.shared.risk_config import approval_requirement, DEFAULT_RISK_LEVEL
from src.notifications.tickets import publish_ticket_status_notification
from src.shared.ticket_automation_sync import sync_unresolvable_assessment

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
      1. Runtime settings del tenant en RDS (function_base_url + function_route_victor):
         Victor on-premise dedicado del tenant (laboratorio/instalaciones aisladas).
         Devuelve source "on_premise".
      2. URL global configurada por env (AGENTS_FUNCTION_BASE_URL_VICTOR): Victor
         on-premise compartido. Devuelve source "global".
      3. Nada configurado -> (None, route, "fallback").

    La llamada a RDS es opcional y con tolerancia a fallos: si no hay secretos de
    BD o el lookup falla, se degrada al comportamiento global/fallback.
    """
    settings = get_settings()
    default_route = (settings.agents_function_route_victor or "/api/agents/VictorDurableAgent/run").strip()

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

    base_url = (settings.agents_function_base_url_victor or "").strip()
    if base_url:
        return base_url, default_route, "global"

    return None, default_route, "fallback"


def _payload_for(
    phase: str,
    subject: str,
    description: str,
    ticket_id: str,
    tenant_id: int,
    plan_from_event,
    similar_case_plan=None,
    similar_case_solution=None,
    generate_plans: bool = False,
    custom_instructions=None,
    custom_plan_steps=None,
) -> dict:
    payload = {
        "message": f"[{phase}] Ticket: {subject}. {description}",
        "subject": subject,
        "description": description,
        "phase": phase,
        "ticket_id": ticket_id,
        "tenant_id": int(tenant_id),
    }
    if phase == "plan" and generate_plans:
        payload["generate_plans"] = True
    if plan_from_event is not None:
        payload["plan"] = plan_from_event
    if similar_case_plan is not None:
        payload["similar_case_plan"] = similar_case_plan
    if similar_case_solution is not None:
        payload["similar_case_solution"] = similar_case_solution
    if custom_instructions is not None:
        payload["custom_instructions"] = custom_instructions
    if custom_plan_steps is not None:
        payload["custom_plan_steps"] = custom_plan_steps
    return payload


def _sanitize_plan_steps(steps) -> list:
    """Normaliza los pasos devueltos por Victor antes de evaluarlos/ejecutarlos.

    Algunos planes (sobre todo los generados a partir de instrucciones libres)
    traen `command` como lista de argumentos o como numero. El front y el
    ejecutor esperan un string; se normaliza aqui para evitar errores aguas
    abajo sin descartar pasos.
    """
    if not isinstance(steps, list):
        return []
    normalized = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        command = step.get("command")
        if isinstance(command, list) and command and all(isinstance(part, str) for part in command):
            joined = " ".join(part.strip() for part in command if part.strip())
            step = {**step, "command": joined}
        elif isinstance(command, (int, float)) and not isinstance(command, bool):
            step = {**step, "command": str(command)}
        normalized.append(step)
    return normalized


def _normalize_plans(data) -> list:
    """Convierte la respuesta de Victor en una lista de planes.

    Acepta:
      - {"plans": [ {...plan...} ]}          (nuevo formato multi-plan)
      - {"plan": [...]} / {"plan": {...}}    (formato legacy, un solo plan)
      - {"plan": [...], "plan_summary": ...}
    Cada plan normalizado queda como:
      {"plan_id", "title", "plan_summary", "total_steps", "risk_level", "plan": [...]}
    """
    if not isinstance(data, dict):
        data = {}

    plans = data.get("plans")
    if isinstance(plans, list):
        # Cuando Victor responde con el formato multi-plan, esa lista es
        # autoritativa: si no contiene ningun plan ejecutable se devuelve vacio
        # (y la fase plan falla) en vez de caer al plan legacy, que podria ser
        # un residuo de un ciclo anterior y provocaria un falso RESUELTO.
        normalized = []
        for idx, raw in enumerate(plans):
            if not isinstance(raw, dict):
                continue
            inner = raw.get("plan", raw)
            steps = inner.get("steps", []) if isinstance(inner, dict) else inner if isinstance(inner, list) else []
            if not isinstance(steps, list):
                steps = []
            steps = _sanitize_plan_steps(steps)
            if len(steps) == 0:
                continue
            normalized.append({
                "plan_id": raw.get("plan_id") or f"plan-{idx + 1}",
                "title": raw.get("title") or f"Plan {idx + 1}",
                "plan_summary": raw.get("plan_summary") or raw.get("summary") or "",
                "analysis": raw.get("analysis") or raw.get("plan_analysis") or "",
                "total_steps": raw.get("total_steps") or len(steps),
                "risk_level": raw.get("risk_level") or "",
                "plan": steps,
            })
        return normalized

    single = data.get("plan", data)
    steps = single.get("steps", []) if isinstance(single, dict) else single if isinstance(single, list) else []
    if not isinstance(steps, list):
        steps = []
    steps = _sanitize_plan_steps(steps)
    if len(steps) == 0:
        return []
    return [{
        "plan_id": "plan-1",
        "title": "Plan de remediacion",
        "plan_summary": data.get("plan_summary") or data.get("summary") or "",
        "analysis": data.get("analysis") or "",
        "total_steps": data.get("total_steps") or len(steps),
        "risk_level": data.get("risk_level") or "",
        "plan": steps,
    }]


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
        "plans": [],
        "plansCount": 0,
        "planSource": "fallback",
        "maxRiskLevel": DEFAULT_RISK_LEVEL,
        "approval": requirement,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
        "victorSource": source,
    }


_MAX_STEP_OUTPUT = 8000

_OK_STATUSES = {"ok", "success", "succeeded", "completed", "done"}


def _step_is_ok(status) -> bool:
    return str(status or "").strip().lower() in _OK_STATUSES


def _clip_output(value, limit: int = _MAX_STEP_OUTPUT) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[salida truncada: {len(text) - limit} caracteres omitidos]"


def _build_execution_logs(
    ticket_id: str,
    step_results: list,
    all_success: bool,
    summary: str,
    duration_seconds: float | None = None,
) -> dict:
    """Construye el bloque `execution_logs` que consume el frontend.

    Formato (ExecutionLogsPayload):
      run_id, correlation_id, iterations, duration_seconds,
      steps_ok, steps_total,
      final_result{status,message,stdout,stderr,artifact_path},
      timeline[{ts,index,step,label,status,command,returncode,stdout,stderr,detail}],
      decision_history

    Cada paso conserva su comando y su salida completa (hasta 8 KB) para que el
    frontend pueda mostrar el detalle real en vez de un resumen concatenado.
    """
    now = datetime.now(timezone.utc).isoformat()
    timeline = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    ok_count = 0

    for idx, raw_step in enumerate(step_results):
        if not isinstance(raw_step, dict):
            continue
        status = raw_step.get("status") or ("ok" if raw_step.get("success", True) else "failed")
        if _step_is_ok(status):
            ok_count += 1
        label = (
            raw_step.get("description")
            or raw_step.get("tool")
            or raw_step.get("command")
            or f"Paso {idx + 1}"
        )
        command = _clip_output(raw_step.get("command"), 2000)
        stdout = _clip_output(
            raw_step.get("stdout")
            or raw_step.get("output")
            or raw_step.get("result")
        )
        stderr = _clip_output(raw_step.get("stderr"))
        returncode = raw_step.get("returncode")
        warnings = [str(w) for w in (raw_step.get("warnings") or [])]
        output = stdout or stderr
        detail_parts = [part for part in (
            command,
            f"rc={returncode}" if returncode is not None else "",
            output,
        ) if part]
        timeline.append({
            "ts": raw_step.get("ts") or now,
            "index": idx + 1,
            "step": f"{idx + 1}. {label}",
            "label": label,
            "status": str(status),
            "command": command,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "warnings": warnings,
            "detail": "\n".join(detail_parts) or "Sin salida registrada",
        })
        if command:
            stdout_parts.append(f"$ {command}")
        if stdout:
            stdout_parts.append(stdout)
        if stderr:
            stderr_parts.append(f"[stderr] {stderr}")

    total = len(timeline)
    if not summary:
        summary = (
            f"{ok_count}/{total} pasos completados"
            if all_success
            else f"{ok_count}/{total} pasos completados (con fallos)"
        )

    return {
        "run_id": f"{ticket_id}-{int(datetime.now(timezone.utc).timestamp())}",
        "correlation_id": ticket_id,
        "iterations": total,
        "duration_seconds": round(duration_seconds, 2) if duration_seconds is not None else None,
        "steps_ok": ok_count,
        "steps_total": total,
        "final_result": {
            "status": "completed" if all_success else "failed",
            "message": summary,
            "stdout": "\n".join(stdout_parts),
            "stderr": "\n".join(stderr_parts),
            "artifact_path": None,
        },
        "timeline": timeline,
        "decision_history": [],
    }


def _persist_execution_failure(tenant_id, ticket_id: str, reason: str) -> None:
    """Deja el ticket en FALLIDO con logs cuando la ejecucion no puede completarse.

    Evita el estado atascado APROBADO/EXECUTING cuando Victor no responde o el
    plan no tiene pasos ejecutables. Si el error ya se persistio con detalle,
    conserva ese detalle en lugar de reemplazarlo por el mensaje del state
    machine.
    """
    try:
        from src.shared.tickets_store import get_tenant_ticket_or_none, update_ticket_fields
        existing = get_tenant_ticket_or_none(int(tenant_id), ticket_id)
        if (
            existing
            and existing.get("execution_status") == "FAILED"
            and existing.get("execution_summary")
        ):
            logger.info("Ticket %s already has a persisted execution failure; keeping detail", ticket_id)
            return
        logs = _build_execution_logs(ticket_id, [], False, reason)
        logs["final_result"]["stderr"] = reason
        update_ticket_fields(int(tenant_id), ticket_id, {
            "status": "FALLIDO",
            "execution_status": "FAILED",
            "execution_summary": reason,
            "execution_logs": logs,
        })
    except Exception as exc:
        logger.warning("Failed to persist execution failure for ticket %s: %s", ticket_id, exc)


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    subject = event.get("subject", "")
    description = event.get("description", "")
    phase = event.get("phase", "assessment")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    if phase == "mark_failed":
        reason = event.get("reason") or "La ejecucion del plan no pudo completarse"
        _persist_execution_failure(tenant_id, ticket_id, reason)
        return {
            "status": "failed",
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "reason": reason,
        }

    base_url, victor_route, endpoint_source = _resolve_victor_endpoint(int(tenant_id))

    if not base_url:
        logger.warning("Victor endpoint not configured for tenant %s (source=%s), returning default response", tenant_id, endpoint_source)
        if phase == "assessment":
            sync_unresolvable_assessment(int(tenant_id), ticket_id, "VICTOR_NOT_CONFIGURED")
        return _fallback_response(phase, ticket_id, tenant_id, subject, description, endpoint_source)

    full_url = f"{base_url}{victor_route}"
    token = _build_service_token(int(tenant_id))
    # Keep headroom below the 300 s Lambda budget so a hung Victor can be
    # recorded as FAILED instead of leaving the ticket stuck in EXECUTING.
    timeout_seconds = int(os.environ.get("VICTOR_TIMEOUT_SECONDS", "240"))

    plan_from_event = event.get("plan")
    similar_case_plan = event.get("similarCasePlan")
    similar_case_solution = event.get("similarCaseSolution")
    custom_instructions = event.get("customInstructions")
    custom_plan_steps = event.get("customPlanSteps")
    generate_plans = bool(event.get("generatePlans", phase == "plan"))
    payload = _payload_for(
        phase,
        subject,
        description,
        ticket_id,
        tenant_id,
        plan_from_event,
        similar_case_plan,
        similar_case_solution,
        generate_plans=generate_plans,
        custom_instructions=custom_instructions,
        custom_plan_steps=custom_plan_steps,
    )

    try:
        request_started = time.monotonic()
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
            sync_unresolvable_assessment(int(tenant_id), ticket_id, "VICTOR_TIMEOUT")
            return {
                "canResolve": False,
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
                "victorSource": endpoint_source,
            }
        if phase == "execute":
            _persist_execution_failure(
                tenant_id, ticket_id,
                "Victor no respondio a tiempo durante la ejecucion del plan",
            )
        raise ValidationError("Victor timed out during plan generation")
    except requests.exceptions.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        logger.error("Error calling Victor errorType=%s statusCode=%s (source=%s)",
                     type(exc).__name__, status_code, endpoint_source)
        if phase == "assessment":
            reason_code = "VICTOR_ACCESS_DENIED" if status_code in (401, 403) else "VICTOR_UNAVAILABLE"
            sync_unresolvable_assessment(int(tenant_id), ticket_id, reason_code)
            return {
                "canResolve": False,
                "ticketId": ticket_id,
                "tenantId": tenant_id,
                "subject": subject,
                "description": description,
                "victorSource": endpoint_source,
            }
        if phase == "execute":
            _persist_execution_failure(
                tenant_id, ticket_id,
                f"Victor devolvio un error durante la ejecucion (HTTP {status_code or 'sin codigo'})",
            )
        raise ValidationError(f"Victor returned an error: {exc}")
    except ValueError as exc:
        logger.error("Victor returned an invalid JSON response for ticket %s: %s", ticket_id, exc)
        if phase == "execute":
            _persist_execution_failure(
                tenant_id, ticket_id,
                "Victor devolvio una respuesta invalida durante la ejecucion",
            )
        raise ValidationError("Victor returned an invalid response")

    if phase == "assessment":
        can_resolve = bool(data.get("can_resolve", data.get("canResolve", False)))
        if not can_resolve:
            sync_unresolvable_assessment(int(tenant_id), ticket_id, "CANNOT_RESOLVE")
        return {
            "canResolve": can_resolve,
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "subject": subject,
            "description": description,
            "victorSource": endpoint_source,
        }

    if phase == "execute":
        execute_plan = plan_from_event
        if not _plan_has_steps(execute_plan):
            execute_plan = event.get("recommendedPlan")
        if not _plan_has_steps(execute_plan):
            logger.error("Execute phase reached with empty plan for ticket %s", ticket_id)
            _persist_execution_failure(tenant_id, ticket_id, "No hay pasos ejecutables en el plan aprobado")
            raise ValidationError("Cannot execute: no action plan steps present for this ticket")
        plan_from_event = execute_plan
        all_success = bool(data.get("all_success", False))
        step_results = data.get("step_results", [])
        step_results = step_results if isinstance(step_results, list) else []
        ok_count = sum(
            1 for r in step_results
            if isinstance(r, dict)
            and _step_is_ok(r.get("status") or ("ok" if r.get("success", True) else "failed"))
        )
        total_steps = len(step_results)
        execution_summary = f"{ok_count}/{total_steps} pasos completados"
        if not all_success:
            failed_step = next(
                (
                    r for r in step_results
                    if isinstance(r, dict)
                    and not _step_is_ok(r.get("status") or ("ok" if r.get("success", True) else "failed"))
                ),
                None,
            )
            if failed_step:
                failed_label = (
                    failed_step.get("description")
                    or failed_step.get("command")
                    or "paso sin descripcion"
                )
                execution_summary = f"{execution_summary}: fallo en '{failed_label}'"
        execution_logs = _build_execution_logs(
            ticket_id=str(ticket_id),
            step_results=step_results,
            all_success=all_success,
            summary=execution_summary,
            duration_seconds=time.monotonic() - request_started,
        )
        if all_success:
            try:
                from src.shared.tickets_store import update_ticket_fields
                update_ticket_fields(int(tenant_id), ticket_id, {
                    "status": "RESUELTO",
                    "execution_status": "EXECUTED",
                    "execution_summary": execution_summary,
                    "execution_logs": execution_logs,
                })
                publish_ticket_status_notification(
                    tenant_id=int(tenant_id),
                    ticket_id=ticket_id,
                    status="RESUELTO",
                )
                logger.info("Ticket %s marked as RESUELTO after successful execution", ticket_id)
            except Exception as exc:
                logger.error("Failed to update ticket %s to RESUELTO: %s", ticket_id, exc)
        else:
            try:
                from src.shared.tickets_store import update_ticket_fields
                update_ticket_fields(int(tenant_id), ticket_id, {
                    "status": "FALLIDO",
                    "execution_status": "FAILED",
                    "execution_summary": execution_summary,
                    "execution_logs": execution_logs,
                })
            except Exception as exc:
                logger.warning("Failed to persist execution logs for ticket %s: %s", ticket_id, exc)
        return {
            "executionResult": data,
            "executionSummary": execution_summary,
            "executionLogs": execution_logs,
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "victorSource": endpoint_source,
        }

    plans = _normalize_plans(data)
    if not plans:
        logger.error("Victor returned no valid plans for ticket %s (ticket will not be executable)", ticket_id)
        raise ValidationError("Victor returned an empty plan - no steps to execute")

    recommended = plans[0]
    recommended_plan = {"steps": recommended["plan"]}
    if recommended.get("plan_summary"):
        recommended_plan["summary"] = recommended["plan_summary"]
    if recommended.get("risk_level"):
        recommended_plan["risk_level"] = recommended["risk_level"]

    requirement = approval_requirement(recommended_plan)
    persist = bool(event.get("persist", True))
    if persist:
        try:
            from src.shared.tickets_store import update_ticket_fields
            update_ticket_fields(int(tenant_id), ticket_id, {
                "action_plan": recommended_plan,
                "action_plans": plans,
            })
        except Exception as exc:
            logger.warning("Failed to save action_plan for ticket %s: %s", ticket_id, exc)

    return {
        "plan": recommended_plan,
        "plans": plans,
        "plansCount": len(plans),
        "analysis": recommended.get("analysis") or "",
        "planSource": "victor_azure" if endpoint_source == "global" else "victor_on_premise",
        "maxRiskLevel": requirement["max_risk_level"],
        "approval": requirement,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
        "victorSource": endpoint_source,
    }
