from aws_lambda_powertools.utilities.typing import LambdaContext
from sqlalchemy import select

from src.persistence.db import session_scope
from src.persistence.models import User
from src.shared.context import get_current_user, log_audit, require_admin
from src.shared.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.shared.handlers import handle_errors
from src.shared.http import get_method, get_path_parameter, parse_json_body
from src.shared.logging import logger
from src.shared.responses import json_response


def _get_tenant_user_or_404(session, current_user: User, user_id: int) -> User:
    user = session.get(User, user_id)
    if not user or user.company_id != current_user.company_id:
        raise NotFoundError("User not found")
    return user


def _get_users(event: dict) -> dict:
    with session_scope() as session:
        current_user = get_current_user(session, event)
        users = session.scalars(select(User).where(User.company_id == current_user.company_id)).all()
        return json_response(200, {"users": [user.to_dict() for user in users]})


def _get_user(event: dict, user_id: int) -> dict:
    with session_scope() as session:
        current_user = get_current_user(session, event)
        user = _get_tenant_user_or_404(session, current_user, user_id)
        return json_response(200, user.to_dict())


def _create_user(event: dict) -> dict:
    data = parse_json_body(event)
    if not data:
        raise ValidationError("Request body is required")

    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    role = data.get("role", "USER")

    if not all([username, email, password]):
        raise ValidationError("username, email and password are required")
    if role not in ["ADMIN", "USER"]:
        raise ValidationError("role must be ADMIN or USER")

    with session_scope() as session:
        current_user = get_current_user(session, event)
        require_admin(current_user)

        existing_username = session.scalar(select(User).where(User.username == username))
        if existing_username:
            raise ConflictError("Username already exists")
        existing_email = session.scalar(select(User).where(User.email == email))
        if existing_email:
            raise ConflictError("Email already exists")

        user = User(company_id=current_user.company_id, username=username, email=email, role=role)
        user.set_password(password)
        session.add(user)
        session.flush()
        log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="USER", entity_id=user.id, payload={"username": username, "role": role})
        return json_response(201, {"message": "User created successfully", "user": user.to_dict()})


def _update_user(event: dict, user_id: int) -> dict:
    data = parse_json_body(event)
    if not data:
        raise ValidationError("Request body is required")

    with session_scope() as session:
        current_user = get_current_user(session, event)
        user = _get_tenant_user_or_404(session, current_user, user_id)
        if current_user.role != "ADMIN" and current_user.id != user_id:
            raise ForbiddenError("Access denied")

        if "email" in data:
            existing_email = session.scalar(select(User).where(User.email == data["email"], User.id != user_id))
            if existing_email:
                raise ConflictError("Email already exists")
            user.email = data["email"]
        if "password" in data:
            user.set_password(data["password"])
        if "role" in data and current_user.role == "ADMIN":
            if data["role"] not in ["ADMIN", "USER"]:
                raise ValidationError("role must be ADMIN or USER")
            user.role = data["role"]

        log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="USER", entity_id=user.id, payload={"email": user.email, "role": user.role})
        return json_response(200, {"message": "User updated successfully", "user": user.to_dict()})


def _delete_user(event: dict, user_id: int) -> dict:
    with session_scope() as session:
        current_user = get_current_user(session, event)
        require_admin(current_user)
        user = _get_tenant_user_or_404(session, current_user, user_id)
        if user.id == current_user.id:
            raise ValidationError("Cannot delete yourself")

        log_audit(session, actor_user_id=current_user.id, action="DELETE", entity_type="USER", entity_id=user.id, payload={"username": user.username})
        session.delete(user)
        return json_response(200, {"message": "User deleted successfully"})


@logger.inject_lambda_context(log_event=True)
@handle_errors
def handler(event: dict, context: LambdaContext) -> dict:
    method = get_method(event)
    user_id_raw = get_path_parameter(event, "user_id")

    if method == "GET" and user_id_raw is None:
        return _get_users(event)
    if method == "POST" and user_id_raw is None:
        return _create_user(event)
    if user_id_raw is None:
        return json_response(404, {"error": "Route not found", "code": "not_found"})

    user_id = int(user_id_raw)
    if method == "GET":
        return _get_user(event, user_id)
    if method == "PUT":
        return _update_user(event, user_id)
    if method == "DELETE":
        return _delete_user(event, user_id)
    return json_response(404, {"error": "Route not found", "code": "not_found"})
