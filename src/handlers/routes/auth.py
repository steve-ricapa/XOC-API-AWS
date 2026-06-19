from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import Company, User
from src.shared.auth import create_access_token, create_refresh_token
from src.shared.context import get_user_by_email, get_user_by_username, log_audit
from src.shared.dependencies import get_current_user_id, require_refresh_claims
from src.shared.errors import AppError, ConflictError, UnauthorizedError, ValidationError
from src.shared.schemas import ErrorResponse, LoginRequest, LoginResponse, OnboardingTenantRequest, OnboardingTenantResponse, RefreshResponse, UserResponse, CompanyResponse


router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/register", responses={410: {"model": ErrorResponse}})
def register_disabled() -> None:
    raise AppError(
        "Legacy register endpoint is disabled. Use POST /api/onboarding/tenant to create tenant + admin.",
        status_code=status.HTTP_410_GONE,
        code="REGISTRATION_FLOW_CHANGED",
    )


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, session: Session = Depends(get_db_session)) -> LoginResponse:
    email = payload.email.strip()
    password = payload.password

    if not email or not password:
        raise ValidationError("email and password are required")

    user = get_user_by_email(session, email)
    if not user or not user.check_password(password):
        raise UnauthorizedError("Invalid email or password")

    company = session.get(Company, user.company_id)
    access_token = create_access_token(identity=str(user.id), additional_claims={"company_id": user.company_id, "role": user.role})
    refresh_token = create_refresh_token(identity=str(user.id))
    log_audit(session, actor_user_id=user.id, action="LOGIN", entity_type="USER", entity_id=user.id)
    session.commit()

    return LoginResponse(
        message="Login successful",
        user=UserResponse(**user.to_dict(include_company=True, company=company)),
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/auth/refresh", response_model=RefreshResponse)
def refresh_token(_: dict = Depends(require_refresh_claims), user_id: int = Depends(get_current_user_id), session: Session = Depends(get_db_session)) -> RefreshResponse:
    user = session.get(User, user_id)
    if not user:
        raise UnauthorizedError("User not found")

    access_token = create_access_token(identity=str(user.id), additional_claims={"company_id": user.company_id, "role": user.role})
    refresh_token = create_refresh_token(identity=str(user.id))
    return RefreshResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/onboarding/tenant", response_model=OnboardingTenantResponse, status_code=201)
def create_tenant_with_admin(payload: OnboardingTenantRequest, session: Session = Depends(get_db_session)) -> OnboardingTenantResponse:
    company_name = payload.company_name.strip()
    admin_email = payload.admin_email.strip()
    admin_password = payload.admin_password
    admin_username = payload.admin_username.strip() if isinstance(payload.admin_username, str) else ""

    if not company_name:
        raise ValidationError("company_name is required")
    if not admin_email:
        raise ValidationError("admin_email is required")
    if not admin_password:
        raise ValidationError("admin_password is required")

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
    session.commit()

    access_token = create_access_token(identity=str(admin_user.id), additional_claims={"company_id": admin_user.company_id, "role": admin_user.role})
    refresh_token = create_refresh_token(identity=str(admin_user.id))

    return OnboardingTenantResponse(
        success=True,
        message="Tenant and admin user created successfully",
        company=CompanyResponse(**company.to_dict()),
        owner_user=UserResponse(**admin_user.to_dict(include_company=False)),
        access_token=access_token,
        refresh_token=refresh_token,
    )
