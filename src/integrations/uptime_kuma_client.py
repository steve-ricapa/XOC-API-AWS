from src.persistence.models import Integration
from src.shared.encryption import decrypt_credentials


def get_uptime_kuma_integration(session, company_id: int) -> Integration | None:
    return session.query(Integration).filter_by(company_id=company_id, provider="uptime_kuma").first()


def get_summary(session, company_id: int) -> dict:
    integration = get_uptime_kuma_integration(session, company_id)
    if not integration or not integration.credentials_encrypted:
        return {"configured": False, "message": "Uptime Kuma integration not configured for this company"}
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if not credentials:
        return {"configured": False, "message": "Failed to decrypt Uptime Kuma credentials"}
    if not credentials.get("url"):
        return {"configured": False, "message": "Incomplete Uptime Kuma credentials"}
    return {"configured": True, "services": {"total": 0, "up": 0, "down": 0, "pending": 0}, "uptime_percentage": 0.0, "status": "healthy"}
