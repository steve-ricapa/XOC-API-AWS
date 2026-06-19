from aws_lambda_powertools.utilities.typing import LambdaContext
from sqlalchemy import select

from src.persistence.db import session_scope
from src.persistence.models import Company, User
from src.shared.auth import create_access_token, create_refresh_token, decode_token
from src.shared.context import get_user_by_email, get_user_by_username, log_audit
from src.shared.errors import ConflictError, UnauthorizedError, ValidationError
from src.shared.handlers import handle_errors
from src.shared.http import get_bearer_token, get_method, get_path, parse_json_body
from src.shared.logging import logger
from src.shared.responses import json_response


def _build_user_claims(user: User) -> dict:
    return {
        "company_id": user.company_id,
        "role": user.role,
    }


def _build_username_from_email(session, email: str) -> str:
    local_part = email.split("@", 1)[0].strip().lower()
    normalized = "".join(ch if (ch.isalnum() or ch in ("_", "-", ".")) else "_" for ch in local_part)
    normalized = normalized.strip("._-") or "admin"

    candidate = normalized
    counter = 1
    while get_user_by_username(session, candidate):
        counter += 1
        candidate = f"{normalized}_{counter}"
    return candidate


def _register_disabled() -> dict:
    return json_response(
        410,
        {
            "success": False,
            "error": "REGISTRATION_FLOW_CHANGED",
            "message": "Legacy register endpoint is disabled. Use POST /api/onboarding/tenant to create tenant + admin.",
        },
    )


def _login(event: dict) -> dict:
    data = parse_json_body(event)
    if not data:
        return json_response(400, {"error": "Request body is required"})

    email_raw = data.get("email")
    email = email_raw.strip() if isinstance(email_raw, str) else ""
    password = data.get("password")

    if not email or not password:
        return json_response(400, {"error": "email and password are required"})

    with session_scope() as session:
        user = get_user_by_email(session, email)
        if not user or not user.check_password(password):
            raise UnauthorizedError("Invalid email or password")

        company = session.get(Company, user.company_id)
        access_token = create_access_token(identity=str(user.id), additional_claims=_build_user_claims(user))
        refresh_token = create_refresh_token(identity=str(user.id))
        log_audit(session, actor_user_id=user.id, action="LOGIN", entity_type="USER", entity_id=user.id)

        return json_response(
            200,
            {
                "message": "Login successful",
                "user": user.to_dict(include_company=True, company=company),
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )


def _refresh(event: dict) -> dict:
    token = get_bearer_token(event)
    if not token:
        raise UnauthorizedError("Missing token")

    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise UnauthorizedError("Invalid token type")

    identity = payload.get("sub")
    if not identity:
        raise UnauthorizedError("Invalid token identity")

    with session_scope() as session:
        user = session.get(User, int(identity))
        if not user:
            raise UnauthorizedError("User not found")

        access_token = create_access_token(identity=str(user.id), additional_claims=_build_user_claims(user))
        refresh_token = create_refresh_token(identity=str(user.id))
        return json_response(200, {"access_token": access_token, "refresh_token": refresh_token})


def _create_tenant_with_admin(event: dict) -> dict:
    data = parse_json_body(event)
    company_name_raw = data.get("company_name")
    admin_email_raw = data.get("admin_email")
    admin_password = data.get("admin_password")
    admin_username_raw = data.get("admin_username")

    company_name = company_name_raw.strip() if isinstance(company_name_raw, str) else ""
    admin_email = admin_email_raw.strip() if isinstance(admin_email_raw, str) else ""
    admin_username = admin_username_raw.strip() if isinstance(admin_username_raw, str) else ""

    if not company_name:
        raise ValidationError("company_name is required")
    if not admin_email:
        raise ValidationError("admin_email is required")
    if not isinstance(admin_password, str) or not admin_password:
        raise ValidationError("admin_password is required")

    with session_scope() as session:
        existing_company = session.scalar(select(Company).where(Company.name.ilike(company_name)))
        if existing_company:
            raise ConflictError("Company already exists")

        if get_user_by_email(session, admin_email):
            raise ConflictError("Email already exists")

        if admin_username:
            if get_user_by_username(session, admin_username):
                raise ConflictError("Username already exists")
            username = admin_username
        else:
            username = _build_username_from_email(session, admin_email)

        company = Company(name=company_name, plan_status="ACTIVE")
        session.add(company)
        session.flush()

        admin_user = User(company_id=company.id, username=username, email=admin_email, role="ADMIN")
        admin_user.set_password(admin_password)
        session.add(admin_user)
        session.flush()

        log_audit(session, actor_user_id=admin_user.id, action="CREATE", entity_type="COMPANY", entity_id=company.id, payload={"name": company.name, "onboarding": True})
        log_audit(session, actor_user_id=admin_user.id, action="CREATE", entity_type="USER", entity_id=admin_user.id, payload={"email": admin_user.email, "role": "ADMIN", "onboarding": True})

        access_token = create_access_token(identity=str(admin_user.id), additional_claims=_build_user_claims(admin_user))
        refresh_token = create_refresh_token(identity=str(admin_user.id))

        return json_response(
            201,
            {
                "success": True,
                "message": "Tenant and admin user created successfully",
                "company": company.to_dict(),
                "owner_user": admin_user.to_dict(include_company=False),
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )


@logger.inject_lambda_context(log_event=True)
@handle_errors
def handler(event: dict, context: LambdaContext) -> dict:
    method = get_method(event)
    path = get_path(event)

    if method == "POST" and path == "/api/auth/register":
        return _register_disabled()
    if method == "POST" and path == "/api/auth/login":
        return _login(event)
    if method == "POST" and path == "/api/auth/refresh":
        return _refresh(event)
    if method == "POST" and path == "/api/onboarding/tenant":
        return _create_tenant_with_admin(event)

    return json_response(404, {"error": "Route not found", "code": "not_found"})
