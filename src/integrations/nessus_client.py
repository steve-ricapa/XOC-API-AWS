from src.persistence.models import Integration
from src.shared.encryption import decrypt_credentials


def get_nessus_integration(session, company_id: int) -> Integration | None:
    return session.query(Integration).filter_by(company_id=company_id, provider="nessus").first()


def get_summary(session, company_id: int) -> dict:
    integration = get_nessus_integration(session, company_id)
    if not integration or not integration.credentials_encrypted:
        return {"configured": False, "message": "Nessus integration not configured for this company"}
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if not credentials:
        return {"configured": False, "message": "Failed to decrypt Nessus credentials"}
    if not all([credentials.get("url"), credentials.get("access_key"), credentials.get("secret_key")]):
        return {"configured": False, "message": "Incomplete Nessus credentials"}
    return {"configured": True, "scans": {"total": 0, "completed": 0, "running": 0, "scans": []}, "vulnerabilities": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, "recent_scans": []}
