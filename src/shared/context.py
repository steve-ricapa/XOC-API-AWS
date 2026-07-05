from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.models import AuditLog, Tenant, User
from src.shared.errors import ForbiddenError, NotFoundError, UnauthorizedError
from src.shared.http import get_authorizer_context


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
    if user.role != "ADMIN":
        raise ForbiddenError("Admin access required")


def require_superadmin(user: User) -> None:
    if user.role != "SUPERADMIN":
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
