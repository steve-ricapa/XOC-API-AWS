from aws_lambda_powertools.utilities.typing import LambdaContext
from sqlalchemy import select

from src.persistence.db import session_scope
from src.persistence.models import Company, CompanyRuntimeSettings
from src.shared.context import get_company, get_current_user, log_audit, require_admin, require_same_company
from src.shared.errors import ForbiddenError, ValidationError
from src.shared.handlers import handle_errors
from src.shared.http import get_method, get_path_parameter, parse_json_body
from src.shared.logging import logger
from src.shared.responses import json_response


def _serialize_runtime_settings(runtime_settings: CompanyRuntimeSettings | None) -> dict | None:
    if not runtime_settings:
        return None
    return {
        "id": runtime_settings.id,
        "company_id": runtime_settings.company_id,
        "function_base_url": runtime_settings.function_base_url,
        "function_route_sophia": runtime_settings.function_route_sophia,
        "function_route_sophia_history": runtime_settings.function_route_sophia_history,
        "function_route_sophia_delete": runtime_settings.function_route_sophia_delete,
        "function_route_victor": runtime_settings.function_route_victor,
        "speech_settings": runtime_settings.speech_settings,
        "extra_json": runtime_settings.extra_json,
        "is_active": runtime_settings.is_active,
        "created_at": runtime_settings.created_at.isoformat() if runtime_settings.created_at else None,
        "updated_at": runtime_settings.updated_at.isoformat() if runtime_settings.updated_at else None,
    }


def _get_companies(event: dict) -> dict:
    with session_scope() as session:
        user = get_current_user(session, event)
        if user.role == "ADMIN":
            company = session.get(Company, user.company_id)
            companies = [company] if company else []
        else:
            companies = []
        return json_response(200, {"companies": [company.to_dict() for company in companies]})


def _get_company(event: dict, company_id: int) -> dict:
    with session_scope() as session:
        user = get_current_user(session, event)
        require_same_company(user, company_id)
        company = get_company(session, company_id)
        return json_response(200, company.to_dict())


def _update_company(event: dict, company_id: int) -> dict:
    data = parse_json_body(event)
    if not data:
        raise ValidationError("Request body is required")

    with session_scope() as session:
        user = get_current_user(session, event)
        require_admin(user)
        require_same_company(user, company_id)
        company = get_company(session, company_id)
        if "name" in data:
            company.name = data["name"]
        log_audit(session, actor_user_id=user.id, action="UPDATE", entity_type="COMPANY", entity_id=company.id, payload={"name": company.name})
        return json_response(200, {"message": "Company updated successfully", "company": company.to_dict()})


def _get_runtime_settings(event: dict, company_id: int) -> dict:
    with session_scope() as session:
        user = get_current_user(session, event)
        require_admin(user)
        if user.company_id != company_id:
            raise ForbiddenError("Not authorized")
        runtime_settings = session.scalar(select(CompanyRuntimeSettings).where(CompanyRuntimeSettings.company_id == company_id))
        return json_response(200, {"runtime_settings": _serialize_runtime_settings(runtime_settings)})


def _upsert_runtime_settings(event: dict, company_id: int) -> dict:
    data = parse_json_body(event)
    with session_scope() as session:
        user = get_current_user(session, event)
        require_admin(user)
        if user.company_id != company_id:
            raise ForbiddenError("Not authorized")

        function_base_url = (data.get("function_base_url") or data.get("functionBaseUrl") or "").strip()
        function_route_sophia = (data.get("function_route_sophia") or data.get("functionRouteSophia") or "/api/agents/SophiaDurableAgent/run").strip()
        function_route_sophia_history = (data.get("function_route_sophia_history") or data.get("functionRouteSophiaHistory") or "/api/agents/SophiaDurableAgent/history").strip()
        function_route_sophia_delete = (data.get("function_route_sophia_delete") or data.get("functionRouteSophiaDelete") or "/api/agents/SophiaDurableAgent/threads").strip()
        function_route_victor = (data.get("function_route_victor") or data.get("functionRouteVictor") or "/api/agents/VictorDurableAgent/run").strip()
        is_active = data.get("is_active") if data.get("is_active") is not None else data.get("isActive", True)

        if not function_base_url:
            raise ValidationError("function_base_url is required")

        runtime_settings = session.scalar(select(CompanyRuntimeSettings).where(CompanyRuntimeSettings.company_id == company_id))
        created = False
        if not runtime_settings:
            runtime_settings = CompanyRuntimeSettings(
                company_id=company_id,
                function_base_url=function_base_url,
                function_route_sophia=function_route_sophia,
                function_route_sophia_history=function_route_sophia_history,
                function_route_sophia_delete=function_route_sophia_delete,
                function_route_victor=function_route_victor,
                is_active=bool(is_active),
            )
            session.add(runtime_settings)
            created = True

        runtime_settings.function_base_url = function_base_url
        runtime_settings.function_route_sophia = function_route_sophia
        runtime_settings.function_route_sophia_history = function_route_sophia_history
        runtime_settings.function_route_sophia_delete = function_route_sophia_delete
        runtime_settings.function_route_victor = function_route_victor
        runtime_settings.is_active = bool(is_active)
        if isinstance(data.get("speech_settings"), dict):
            runtime_settings.speech_settings = data.get("speech_settings")
        if isinstance(data.get("extra_json"), dict):
            runtime_settings.extra_json = data.get("extra_json")

        session.flush()
        log_audit(session, actor_user_id=user.id, action="CREATE" if created else "UPDATE", entity_type="COMPANY_RUNTIME_SETTINGS", entity_id=runtime_settings.id, payload={"company_id": company_id, "is_active": runtime_settings.is_active})
        return json_response(200, {"message": "Runtime settings saved successfully", "runtime_settings": _serialize_runtime_settings(runtime_settings)})


@logger.inject_lambda_context(log_event=True)
@handle_errors
def handler(event: dict, context: LambdaContext) -> dict:
    method = get_method(event)
    company_id_raw = get_path_parameter(event, "company_id")

    if method == "GET" and company_id_raw is None:
        return _get_companies(event)

    if company_id_raw is None:
        return json_response(404, {"error": "Route not found", "code": "not_found"})

    company_id = int(company_id_raw)
    raw_path = event.get("rawPath") or ""

    if method == "GET" and raw_path == f"/api/companies/{company_id}":
        return _get_company(event, company_id)
    if method == "PUT" and raw_path == f"/api/companies/{company_id}":
        return _update_company(event, company_id)
    if method == "GET" and raw_path == f"/api/companies/{company_id}/runtime-settings":
        return _get_runtime_settings(event, company_id)
    if method == "PUT" and raw_path == f"/api/companies/{company_id}/runtime-settings":
        return _upsert_runtime_settings(event, company_id)

    return json_response(404, {"error": "Route not found", "code": "not_found"})
