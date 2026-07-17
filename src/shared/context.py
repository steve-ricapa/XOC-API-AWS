from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.models import AuditLog, Tenant, User
from src.shared.errors import ForbiddenError, NotFoundError, UnauthorizedError
from src.shared.http import get_authorizer_context


TENANT_ADMIN_ROLES = {"ADMIN"}
TENANT_READ_ROLES = {"ADMIN", "USER"}
PLATFORM_OPERATOR_ROLES = {"ADMIN_XOC", "SUPERADMIN"}


def normalize_role(value: str | None) -> str:
    return (value or "").strip().upper()


def is_superadmin(user: User | None) -> bool:
    return normalize_role(getattr(user, "role", None)) == "SUPERADMIN"


def is_admin_xoc(user: User | None) -> bool:
    return normalize_role(getattr(user, "role", None)) == "ADMIN_XOC"


def is_platform_operator(user: User | None) -> bool:
    return normalize_role(getattr(user, "role", None)) in PLATFORM_OPERATOR_ROLES


def has_delegated_tenant_context(user: User | None) -> bool:
    return bool(getattr(user, "delegation_active", False))


def effective_tenant_id_of(user: User) -> int:
    role = normalize_role(user.role)
    if role in PLATFORM_OPERATOR_ROLES and not has_delegated_tenant_context(user):
        raise ForbiddenError("Delegated tenant context required")
    tenant_id = getattr(user, "effective_tenant_id", None) or getattr(user, "tenant_id", None)
    if not tenant_id:
        raise UnauthorizedError("Tenant not found in request context")
    return int(tenant_id)


def require_platform_operator(user: User) -> None:
    if not is_platform_operator(user):
        raise ForbiddenError("Platform operator access required")


def require_tenant_read_access(user: User) -> None:
    role = normalize_role(user.role)
    if role in TENANT_READ_ROLES:
        return
    if role in PLATFORM_OPERATOR_ROLES and has_delegated_tenant_context(user):
        return
    raise ForbiddenError("Tenant read access required")


def get_current_user(session: Session, event: dict) -> User:
    context = get_authorizer_context(event)
    user_id = context.get("userId") or context.get("sub") or context.get("principalId")
    if not user_id:
        raise UnauthorizedError("User not found")
    user = session.get(User, int(user_id))
    if not user:
        raise UnauthorizedError("User not found")
    return user


def require_admin(user: User) -> None:
    role = normalize_role(user.role)
    if role in TENANT_ADMIN_ROLES:
        return
    if role in PLATFORM_OPERATOR_ROLES and has_delegated_tenant_context(user):
        return
    raise ForbiddenError("Admin access required")


def require_superadmin(user: User) -> None:
    if not is_superadmin(user):
        raise ForbiddenError("Superadmin access required")


def require_same_tenant(user: User, tenant_id: int) -> None:
    if int(user.tenant_id) != int(tenant_id):
        raise NotFoundError("Tenant not found")


def log_audit(session: Session, *, actor_user_id: int | None, action: str, entity_type: str, entity_id: str | int | None = None, payload: dict | list | None = None) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            payload=payload,
        )
    )


def get_tenant(session: Session, tenant_id: int) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if not tenant:
        raise NotFoundError("Tenant not found")
    return tenant


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email.ilike(email)))


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username.ilike(username)))
