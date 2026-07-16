from src.integrations.summary_store import build_wazuh_summary


def get_summary(session, tenant_id: int) -> dict:
    return build_wazuh_summary(session, tenant_id)
