from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.context import log_audit, require_admin
from src.shared.dependencies import get_current_user
from src.shared.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.shared.schemas import CreateUserRequest, CreateUserResponse, UpdateUserRequest, UpdateUserResponse, UserResponse, UsersListResponse


router = APIRouter(prefix="/api/users", tags=["users"])


def _get_tenant_user_or_404(session: Session, current_user: User, user_id: int) -> User:
    user = session.get(User, user_id)
    if not user or user.company_id != current_user.company_id:
        raise NotFoundError("User not found")
    return user


@router.get("", response_model=UsersListResponse)
def get_users(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> UsersListResponse:
    users = session.scalars(select(User).where(User.company_id == current_user.company_id)).all()
    return UsersListResponse(users=[UserResponse(**user.to_dict()) for user in users])


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> UserResponse:
    user = _get_tenant_user_or_404(session, current_user, user_id)
    return UserResponse(**user.to_dict())


@router.post("", response_model=CreateUserResponse, status_code=201)
def create_user(payload: CreateUserRequest, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> CreateUserResponse:
    require_admin(current_user)

    if payload.role not in ["ADMIN", "USER"]:
        raise ValidationError("role must be ADMIN or USER")

    existing_username = session.scalar(select(User).where(User.username == payload.username))
    if existing_username:
        raise ConflictError("Username already exists")
    existing_email = session.scalar(select(User).where(User.email == payload.email))
    if existing_email:
        raise ConflictError("Email already exists")

    user = User(company_id=current_user.company_id, username=payload.username, email=payload.email, role=payload.role)
    user.set_password(payload.password)
    session.add(user)
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="USER", entity_id=user.id, payload={"username": payload.username, "role": payload.role})
    session.commit()
    return CreateUserResponse(message="User created successfully", user=UserResponse(**user.to_dict()))


@router.put("/{user_id}", response_model=UpdateUserResponse)
def update_user(user_id: int, payload: UpdateUserRequest, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> UpdateUserResponse:
    user = _get_tenant_user_or_404(session, current_user, user_id)
    if current_user.role != "ADMIN" and current_user.id != user_id:
        raise ForbiddenError("Access denied")

    if payload.email is not None:
        existing_email = session.scalar(select(User).where(User.email == payload.email, User.id != user_id))
        if existing_email:
            raise ConflictError("Email already exists")
        user.email = payload.email
    if payload.password is not None:
        user.set_password(payload.password)
    if payload.role is not None and current_user.role == "ADMIN":
        if payload.role not in ["ADMIN", "USER"]:
            raise ValidationError("role must be ADMIN or USER")
        user.role = payload.role

    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="USER", entity_id=user.id, payload={"email": user.email, "role": user.role})
    session.commit()
    return UpdateUserResponse(message="User updated successfully", user=UserResponse(**user.to_dict()))


@router.delete("/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict[str, str]:
    require_admin(current_user)
    user = _get_tenant_user_or_404(session, current_user, user_id)
    if user.id == current_user.id:
        raise ValidationError("Cannot delete yourself")

    log_audit(session, actor_user_id=current_user.id, action="DELETE", entity_type="USER", entity_id=user.id, payload={"username": user.username})
    session.delete(user)
    session.commit()
    return {"message": "User deleted successfully"}
