from src.integrations.summary_store import build_vulnerability_summary


def get_summary(session, tenant_id: int) -> dict:
    return build_vulnerability_summary(session, tenant_id, "nessus")
