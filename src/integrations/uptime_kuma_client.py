from src.integrations.summary_store import build_uptime_kuma_summary


def get_summary(session, tenant_id: int) -> dict:
    return build_uptime_kuma_summary(session, tenant_id)
