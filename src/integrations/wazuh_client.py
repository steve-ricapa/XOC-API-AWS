from src.integrations.summary_store import build_wazuh_summary


def get_summary(session, company_id: int) -> dict:
    return build_wazuh_summary(session, company_id)
