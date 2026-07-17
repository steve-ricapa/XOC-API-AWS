from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.integrations.summary_store import build_dashboard_summary
from src.integrations import nessus_client, uptime_kuma_client, wazuh_client, zabbix_client
from src.persistence.db import get_db_session
from src.persistence.models import Integration, User
from src.shared.context import effective_tenant_id_of, log_audit, require_admin, require_tenant_read_access
from src.shared.dependencies import get_current_user
from src.shared.encryption import decrypt_credentials, encrypt_credentials
from src.shared.errors import NotFoundError, ValidationError


router = APIRouter(prefix="/integrations", tags=["integrations"])

VALID_PROVIDERS = ["palo_alto", "splunk", "wazuh", "meraki", "zabbix", "nessus", "uptime_kuma"]


def _get_integration_or_404(session: Session, tenant_id: int, integration_id: int) -> Integration:
    integration = session.get(Integration, integration_id)
    if not integration or integration.tenant_id != tenant_id:
        raise NotFoundError("Integration not found")
    return integration


@router.get("")
def get_integrations(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    integrations = session.query(Integration).filter_by(tenant_id=effective_tenant_id_of(current_user)).all()
    return {"integrations": [integration.to_dict() for integration in integrations]}


@router.get("/zabbix/summary")
def get_zabbix_summary(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    return zabbix_client.get_summary(session, effective_tenant_id_of(current_user))


@router.get("/zabbix/detailed")
def get_zabbix_detailed(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    return zabbix_client.get_detailed_metrics(session, effective_tenant_id_of(current_user))


@router.get("/wazuh/summary")
def get_wazuh_summary(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    return wazuh_client.get_summary(session, effective_tenant_id_of(current_user))


@router.get("/nessus/summary")
def get_nessus_summary(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    return nessus_client.get_summary(session, effective_tenant_id_of(current_user))


@router.get("/uptime_kuma/summary")
def get_uptime_kuma_summary(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    return uptime_kuma_client.get_summary(session, effective_tenant_id_of(current_user))


@router.get("/dashboard/summary")
def get_dashboard_summary(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    return build_dashboard_summary(session, effective_tenant_id_of(current_user))


@router.get("/{integration_id}")
def get_integration(integration_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    integration = _get_integration_or_404(session, effective_tenant_id_of(current_user), integration_id)
    return integration.to_dict()


@router.post("")
def create_integration(payload: dict, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    if not payload:
        raise ValidationError("Request body is required")
    provider = payload.get("provider")
    credentials = payload.get("credentials")
    extra_json = payload.get("extra_json", {})
    if not provider:
        raise ValidationError("provider is required")
    if not credentials:
        raise ValidationError("credentials is required")
    if provider not in VALID_PROVIDERS:
        raise ValidationError(f'provider must be one of: {", ".join(VALID_PROVIDERS)}')
    credentials_encrypted = encrypt_credentials(credentials)
    integration = Integration(tenant_id=tenant_id, provider=provider, credentials_encrypted=credentials_encrypted, extra_json=extra_json)
    session.add(integration)
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="INTEGRATION", entity_id=integration.id, payload={"provider": provider})
    session.commit()
    return {"message": "Integration created successfully", "integration": integration.to_dict()}


@router.put("/{integration_id}")
def update_integration(integration_id: int, payload: dict, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    integration = _get_integration_or_404(session, effective_tenant_id_of(current_user), integration_id)
    if not payload:
        raise ValidationError("Request body is required")
    if "credentials" in payload:
        integration.credentials_encrypted = encrypt_credentials(payload["credentials"])
    if "extra_json" in payload:
        integration.extra_json = payload["extra_json"]
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="INTEGRATION", entity_id=integration.id, payload={"provider": integration.provider})
    session.commit()
    return {"message": "Integration updated successfully", "integration": integration.to_dict()}


@router.delete("/{integration_id}")
def delete_integration(integration_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    integration = _get_integration_or_404(session, effective_tenant_id_of(current_user), integration_id)
    log_audit(session, actor_user_id=current_user.id, action="DELETE", entity_type="INTEGRATION", entity_id=integration.id, payload={"provider": integration.provider})
    session.delete(integration)
    session.commit()
    return {"message": "Integration deleted successfully"}


@router.get("/{integration_id}/credentials")
def get_integration_credentials(integration_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    integration = _get_integration_or_404(session, effective_tenant_id_of(current_user), integration_id)
    if not integration.credentials_encrypted:
        raise ValidationError("No credentials configured for this integration")
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if credentials is None:
        raise ValidationError("Failed to decrypt credentials")
    return {"credentials": credentials}
