import requests

from src.persistence.models import Integration
from src.shared.encryption import decrypt_credentials


def get_wazuh_integration(session, company_id: int) -> Integration | None:
    return session.query(Integration).filter_by(company_id=company_id, provider="wazuh").first()


def authenticate_wazuh(url: str, username: str, password: str) -> str | None:
    try:
        response = requests.post(f"{url}/security/user/authenticate", auth=(username, password), verify=False, timeout=10)
        if response.status_code == 200:
            return response.json().get("data", {}).get("token")
    except Exception:
        return None
    return None


def get_summary(session, company_id: int) -> dict:
    integration = get_wazuh_integration(session, company_id)
    if not integration or not integration.credentials_encrypted:
        return {"configured": False, "message": "Wazuh integration not configured for this company"}
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if not credentials:
        return {"configured": False, "message": "Failed to decrypt Wazuh credentials"}
    url = credentials.get("url")
    username = credentials.get("username")
    password = credentials.get("password")
    if not all([url, username, password]):
        return {"configured": False, "message": "Incomplete Wazuh credentials"}
    token = authenticate_wazuh(url, username, password)
    if not token:
        return {"configured": True, "error": "Authentication failed"}
    return {"configured": True, "alerts": {"total": 0, "critical": 0, "high": 0, "medium": 0, "recent": []}, "agents": {"total": 0, "active": 0, "disconnected": 0, "never_connected": 0}, "manager_status": "unknown"}
