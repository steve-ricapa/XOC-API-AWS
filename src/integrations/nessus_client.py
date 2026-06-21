from src.integrations.summary_store import build_vulnerability_summary


def get_summary(session, company_id: int) -> dict:
    return build_vulnerability_summary(session, company_id, "nessus")
