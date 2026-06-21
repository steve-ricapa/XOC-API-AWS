from src.integrations.summary_store import build_zabbix_detailed_metrics, build_zabbix_summary


def get_summary(session, company_id: int) -> dict:
    return build_zabbix_summary(session, company_id)


def get_detailed_metrics(session, company_id: int) -> dict:
    return build_zabbix_detailed_metrics(session, company_id)
