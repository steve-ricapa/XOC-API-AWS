from typing import Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.errors import ForbiddenError, UnauthorizedError


def get_request_event(request: Request) -> dict[str, Any]:
    return request.scope.get("aws.event") or {}


def get_request_claims(request: Request) -> dict[str, Any]:
    event = get_request_event(request)
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    if isinstance(authorizer.get("lambda"), dict):
        return authorizer["lambda"]
    return authorizer if isinstance(authorizer, dict) else {}


def require_access_claims(claims: dict[str, Any] = Depends(get_request_claims)) -> dict[str, Any]:
    token_type = claims.get("tokenType") or claims.get("type")
    if token_type and token_type != "access":
        raise ForbiddenError("Access token required")
    return claims


def require_refresh_claims(claims: dict[str, Any] = Depends(get_request_claims)) -> dict[str, Any]:
    token_type = claims.get("tokenType") or claims.get("type")
    if token_type != "refresh":
        raise ForbiddenError("Refresh token required")
    return claims


def get_current_tenant_id(claims: dict[str, Any] = Depends(require_access_claims)) -> int:
    tenant_id = claims.get("tenantId") or claims.get("tenant_id")
    if not tenant_id:
        raise UnauthorizedError("Tenant not found in request context")
    return int(tenant_id)


def get_request_tenant_context(claims: dict[str, Any] = Depends(require_access_claims)) -> tuple[int, str]:
    scopes = claims.get("scopes") or []
    if isinstance(scopes, str):
        scopes = [scopes]
    if "agent:invoke" in scopes:
        tenant_id = claims.get("tenantId") or claims.get("tenant_id")
        if not tenant_id:
            raise UnauthorizedError("tenant_id not found in token")
        return int(tenant_id), "agent"

    tenant_id = claims.get("tenantId") or claims.get("tenant_id")
    if not tenant_id:
        raise UnauthorizedError("Tenant not found in request context")
    return int(tenant_id), "user"


def get_current_user_id(claims: dict[str, Any] = Depends(get_request_claims)) -> int:
    user_id = claims.get("userId") or claims.get("sub") or claims.get("principalId")
    if not user_id:
        raise UnauthorizedError("User not found")
    return int(user_id)


def get_current_user(
    claims: dict[str, Any] = Depends(require_access_claims),
    session: Session = Depends(get_db_session),
) -> User:
    user_id = claims.get("userId") or claims.get("sub") or claims.get("principalId")
    if not user_id:
        raise UnauthorizedError("User not found")
    user = session.get(User, int(user_id))
    if not user:
        raise UnauthorizedError("User not found")
    acting_tenant_id = claims.get("actingTenantId") or claims.get("acting_tenant_id")
    delegation_value = claims.get("delegation")
    delegation_active = delegation_value in (True, "true", "True", "1", 1)
    setattr(user, "actor_tenant_id", user.tenant_id)
    setattr(user, "delegation_active", delegation_active)
    if delegation_active and acting_tenant_id:
        setattr(user, "effective_tenant_id", int(acting_tenant_id))
    else:
        setattr(user, "effective_tenant_id", user.tenant_id)
    return user


def get_current_user_optional(
    claims: dict[str, Any] = Depends(require_access_claims),
    session: Session = Depends(get_db_session),
) -> User | None:
    scopes = claims.get("scopes") or []
    if isinstance(scopes, str):
        scopes = [scopes]
    if "agent:invoke" in scopes:
        return None

    user_id = claims.get("userId") or claims.get("sub") or claims.get("principalId")
    if not user_id:
        raise UnauthorizedError("User not found")
    user = session.get(User, int(user_id))
    if not user:
        raise UnauthorizedError("User not found")
    return user
