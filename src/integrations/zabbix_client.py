import requests

from src.persistence.models import Integration
from src.shared.encryption import decrypt_credentials


def get_zabbix_integration(session, company_id: int) -> Integration | None:
    return session.query(Integration).filter_by(company_id=company_id, provider="zabbix").first()


def authenticate_zabbix(url: str, username: str, password: str) -> str | None:
    try:
        response = requests.post(
            f"{url}/api_jsonrpc.php",
            json={
                "jsonrpc": "2.0",
                "method": "user.login",
                "params": {"username": username, "password": password},
                "id": 1,
            },
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if response.status_code == 200:
            result = response.json()
            return result.get("result")
    except Exception:
        return None
    return None


def get_summary(session, company_id: int) -> dict:
    integration = get_zabbix_integration(session, company_id)
    if not integration or not integration.credentials_encrypted:
        return {"configured": False, "message": "Zabbix integration not configured for this company"}
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if not credentials:
        return {"configured": False, "message": "Failed to decrypt Zabbix credentials"}
    url = credentials.get("url")
    username = credentials.get("username")
    password = credentials.get("password")
    if not all([url, username, password]):
        return {"configured": False, "message": "Incomplete Zabbix credentials"}
    auth_token = authenticate_zabbix(url, username, password)
    if not auth_token:
        return {"configured": True, "error": "Authentication failed"}
    return {"configured": True, "alerts": 0, "hosts_monitored": 0, "avg_cpu": 0.0, "avg_ram": 0.0, "recent_alerts": []}


def get_detailed_metrics(session, company_id: int) -> dict:
    integration = get_zabbix_integration(session, company_id)
    if not integration or not integration.credentials_encrypted:
        return {"configured": False, "message": "Zabbix integration not configured for this company"}
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if not credentials:
        return {"configured": False, "message": "Failed to decrypt Zabbix credentials"}
    return {"configured": True, "metrics": {"avg_cpu": 0.0, "avg_ram": 0.0}, "hosts": []}
