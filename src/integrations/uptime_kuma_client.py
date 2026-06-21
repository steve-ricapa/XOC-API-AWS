from src.integrations.summary_store import build_uptime_kuma_summary


def get_summary(session, company_id: int) -> dict:
    return build_uptime_kuma_summary(session, company_id)
