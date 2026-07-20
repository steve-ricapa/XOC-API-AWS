from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    error: str
    code: str


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    service: str
    stage: str
    database: str


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    plan_status: str
    created_at: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int | None = None
    username: str
    email: str
    role: str
    created_at: str | None = None
    tenant: TenantResponse | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    message: str
    user: UserResponse
    access_token: str
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str


class OnboardingTenantRequest(BaseModel):
    name: str
    admin_email: str
    admin_password: str
    admin_username: str | None = None


class OnboardingTenantResponse(BaseModel):
    success: bool
    message: str
    tenant: TenantResponse
    owner_user: UserResponse
    access_token: str
    refresh_token: str


class TenantsListResponse(BaseModel):
    tenants: list[TenantResponse]


class UpdateTenantRequest(BaseModel):
    name: str | None = None


class UpdateTenantResponse(BaseModel):
    message: str
    tenant: TenantResponse


class RuntimeSettingsResponse(BaseModel):
    id: int
    tenant_id: int
    function_base_url: str
    function_route_sophia: str
    function_route_sophia_history: str
    function_route_sophia_delete: str
    function_route_victor: str
    speech_settings: dict[str, Any] | None = None
    extra_json: dict[str, Any] | None = None
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None


class RuntimeSettingsEnvelope(BaseModel):
    runtime_settings: RuntimeSettingsResponse | None


class UpsertRuntimeSettingsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    function_base_url: str | None = Field(default=None, alias="functionBaseUrl")
    function_route_sophia: str | None = Field(default=None, alias="functionRouteSophia")
    function_route_sophia_history: str | None = Field(default=None, alias="functionRouteSophiaHistory")
    function_route_sophia_delete: str | None = Field(default=None, alias="functionRouteSophiaDelete")
    function_route_victor: str | None = Field(default=None, alias="functionRouteVictor")
    is_active: bool | None = Field(default=None, alias="isActive")
    speech_settings: dict[str, Any] | None = None
    extra_json: dict[str, Any] | None = None


class UpsertRuntimeSettingsResponse(BaseModel):
    message: str
    runtime_settings: RuntimeSettingsResponse


class UsersListResponse(BaseModel):
    users: list[UserResponse]


class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str
    role: str = "USER"


class CreateUserResponse(BaseModel):
    message: str
    user: UserResponse


class UpdateUserRequest(BaseModel):
    email: str | None = None
    password: str | None = None
    role: str | None = None


class UpdateUserResponse(BaseModel):
    message: str
    user: UserResponse


class TicketResponse(BaseModel):
    ticket_id: str
    tenant_id: int
    created_by_user_id: int | None = None
    subject: str
    description: str | None = None
    status: str
    executed_at: str | None = None
    created_at: str | None = None
    action_plan: dict[str, Any] | None = None
    action_plan_version: str
    approved_by_user_id: int | None = None
    approved_at: str | None = None
    rejected_by_user_id: int | None = None
    rejected_at: str | None = None
    execution_status: str | None = None
    execution_logs: dict[str, Any] | list[Any] | None = None
    execution_summary: str | None = None
    pending_decision: dict[str, Any] | None = None
    capability_level: str | None = None
    capability_policy_snapshot: dict[str, Any] | None = None
    decision_timeout_minutes: int | None = None
    on_decision_timeout: str | None = None
    creator: UserResponse | None = None


class TicketsListResponse(BaseModel):
    tickets: list[TicketResponse]


class CreateTicketRequest(BaseModel):
    subject: str
    description: str | None = None
    status: str | None = "PENDING"


class CreateTicketResponse(BaseModel):
    message: str
    ticket: TicketResponse


class UpdateTicketRequest(BaseModel):
    subject: str | None = None
    description: str | None = None
    status: str | None = None
    pending_decision: dict[str, Any] | None = None
    execution_status: str | None = None
    execution_logs: dict[str, Any] | list[Any] | None = None
    execution_summary: str | None = None
    capability_level: str | None = None
    capability_policy_snapshot: dict[str, Any] | None = None
    decision_timeout_minutes: int | None = None
    on_decision_timeout: str | None = None
    action_plan: dict[str, Any] | None = None
    action_plan_version: str | None = None


class UpdateTicketResponse(BaseModel):
    message: str
    ticket: TicketResponse


class DeleteTicketResponse(BaseModel):
    message: str


class SelectDecisionRequest(BaseModel):
    selected_option_id: str
    decision_id: str | None = None
    selection_note: str | None = None


class AgentCreateTicketRequest(BaseModel):
    subject: str
    description: str | None = None
    status: str | None = "PENDING"
    userId: int | None = None
    severity: str | None = "medium"
    metadata: dict[str, Any] | None = None


class AgentCreateTicketResponse(BaseModel):
    success: bool
    ticket_id: str
    ticket: TicketResponse
