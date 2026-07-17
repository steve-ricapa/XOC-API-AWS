from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local dependency during bootstrap
    def load_dotenv() -> bool:
        return False

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import Engine
    from sqlalchemy.exc import SQLAlchemyError
except ImportError:  # pragma: no cover - optional until DB mode is used
    Engine = Any  # type: ignore
    SQLAlchemyError = Exception
    create_engine = None
    text = None

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# QUERY CONFIGURATION
# Estas queries son referencias iniciales y deben ajustarse al esquema real de la base de datos.
# Todas las consultas deben mantenerse en modo solo lectura y comenzar con SELECT.
QUERY_CONFIGURATION = {
    "tenant_info": "SELECT id, name, tenant_id FROM tenants WHERE id = :tenant_id LIMIT 1",
    "report_metadata": "SELECT :tenant_id AS tenant_id, :start_date AS period_start, :end_date AS period_end",
    "tools": "SELECT tool_name FROM tenant_tools WHERE tenant_id = :tenant_id ORDER BY tool_name",
    "severity_previous": "SELECT severity, total FROM severity_summary_weekly WHERE tenant_id = :tenant_id AND period_end < :start_date ORDER BY period_end DESC LIMIT 5",
    "severity_current": "SELECT severity, total FROM severity_summary_weekly WHERE tenant_id = :tenant_id AND period_start >= :start_date AND period_end <= :end_date",
    "security_posture": "SELECT global_score, risk_level, trend, critical_open, high_open, sla_at_risk, mttr_hours FROM security_posture_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date LIMIT 1",
    "cases": "SELECT case_id, title, severity, status, domain, asset, created_at, updated_at, sla_target_hours, elapsed_hours, sla_status, owner, summary, next_action FROM xoc_cases WHERE tenant_id = :tenant_id AND created_at <= :end_date ORDER BY severity DESC, updated_at DESC LIMIT 200",
    "sla_summary": "SELECT total_cases, within_sla, at_risk, breached, average_elapsed_hours FROM sla_summary_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date LIMIT 1",
    "mitre_mapping": "SELECT tactic, technique, evidence, domain, severity, status FROM mitre_mapping_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY severity DESC LIMIT 100",
    "top_assets": "SELECT asset, type, domain, risk_score, critical, high, medium, low, recommendation FROM top_assets_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY risk_score DESC LIMIT 100",
    "domain_scores": "SELECT domain, score, risk, trend, comment FROM domain_scores_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY domain",
    "domains": "SELECT name, summary, score, risk FROM domains_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY name",
    "findings": "SELECT id, domain, title, affected_hosts, severity, description, recommendation, evidence, source_tool, status, remediation_priority FROM findings_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY severity DESC LIMIT 300",
    "technical_evidence": "SELECT finding_id, asset, severity, source_tool, evidence, impact, recommendation, status FROM technical_evidence_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY severity DESC LIMIT 100",
    "operational_timeline": "SELECT date, event, category, impact FROM operational_timeline_weekly WHERE tenant_id = :tenant_id AND date >= :start_date AND date <= :end_date ORDER BY date",
    "actions_worked": "SELECT action_text FROM actions_worked_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY created_at",
    "automation_suggestions": "SELECT name, domain, priority, benefit, status FROM automation_suggestions_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY priority DESC",
    "next_actions": "SELECT action, owner, priority, due_date, dependency FROM next_actions_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY priority DESC, due_date",
    "results": "SELECT summary, highlights_json FROM results_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date LIMIT 1",
    "pending_findings": "SELECT finding_text FROM pending_findings_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY finding_text",
    "security_news": "SELECT title, date, source, summary, links_json FROM security_news_weekly WHERE tenant_id = :tenant_id AND period_start = :start_date AND period_end = :end_date ORDER BY date DESC LIMIT 20",
}


def get_database_config() -> dict[str, Any]:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return {"database_url": database_url}
    return {
        "host": os.environ.get("DB_HOST", "").strip(),
        "port": os.environ.get("DB_PORT", "5432").strip(),
        "name": os.environ.get("DB_NAME", "").strip(),
        "user": os.environ.get("DB_USER", "").strip(),
        "password": os.environ.get("DB_PASSWORD", "").strip(),
        "sslmode": os.environ.get("DB_SSLMODE", "require").strip(),
    }


def validate_select_only(query: str) -> str:
    normalized = query.strip().lstrip("(")
    if not normalized.lower().startswith("select"):
        raise ValueError(f"Query rechazada por seguridad, solo se permite SELECT: {query}")
    return query


def create_db_engine() -> Engine:
    if create_engine is None:
        raise RuntimeError("SQLAlchemy no esta instalado. Ejecuta: pip install -r requirements.txt")
    config = get_database_config()
    database_url = config.get("database_url")
    if not database_url:
        required = [config.get("host"), config.get("name"), config.get("user")]
        if not all(required):
            raise RuntimeError("Faltan variables de conexion a la base de datos")
        database_url = (
            f"postgresql+psycopg2://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['name']}"
        )

    connect_args: dict[str, Any] = {}
    if database_url.startswith("postgresql"):
        sslmode = config.get("sslmode")
        if sslmode:
            connect_args["sslmode"] = sslmode
        connect_args["options"] = "-c default_transaction_read_only=on"

    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def test_database_connection() -> None:
    if text is None:
        raise RuntimeError("SQLAlchemy no esta instalado. Ejecuta: pip install -r requirements.txt")
    engine = create_db_engine()
    with engine.connect() as connection:
        validate_select_only("SELECT 1")
        connection.execute(text("SELECT 1"))


def _warn(section: str, exc: Exception) -> None:
    print(f"WARNING: seccion '{section}' no disponible o fallo la query. Se usara fallback. Detalle: {exc}")


def _execute_rows(engine: Engine, section: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    if text is None:
        raise RuntimeError("SQLAlchemy no esta instalado. Ejecuta: pip install -r requirements.txt")
    query = validate_select_only(QUERY_CONFIGURATION[section])
    try:
        with engine.connect() as connection:
            result = connection.execute(text(query), params)
            return [dict(row._mapping) for row in result]
    except SQLAlchemyError as exc:
        _warn(section, exc)
        return []


def _execute_one(engine: Engine, section: str, params: dict[str, Any]) -> dict[str, Any]:
    rows = _execute_rows(engine, section, params)
    return rows[0] if rows else {}


def fetch_tenant_info(engine: Engine, tenant_id: str) -> dict[str, Any]:
    return _execute_one(engine, "tenant_info", {"tenant_id": tenant_id})


def fetch_report_metadata(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    row = _execute_one(engine, "report_metadata", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})
    return {
        "id": "report-demo-001",
        "title": f"Reporte Operativo Semanal XOC | {tenant_id}",
        "service": "Servicio de Monitoreo Proactivo XOC",
        "generated_at": row.get("generated_at") or "",
        "period": f"Del {start_date} al {end_date}",
        "prepared_by": "TXDXSECURE",
        "scope": row.get("scope") or "Alcance pendiente de ajustar al esquema real.",
    }


def fetch_tools(engine: Engine, tenant_id: str) -> list[str]:
    rows = _execute_rows(engine, "tools", {"tenant_id": tenant_id})
    return [row.get("tool_name") for row in rows if row.get("tool_name")]


def fetch_severity_summary(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    previous_rows = _execute_rows(engine, "severity_previous", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})
    current_rows = _execute_rows(engine, "severity_current", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})

    def map_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
        mapped = {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0}
        for row in rows:
            severity = str(row.get("severity", "")).lower()
            total = int(row.get("total", 0) or 0)
            if severity in mapped:
                mapped[severity] += total
        return mapped

    return {"previous": map_rows(previous_rows), "current": map_rows(current_rows)}


def fetch_security_posture(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    return _execute_one(engine, "security_posture", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_cases(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows = _execute_rows(engine, "cases", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})
    return [
        {
            "id": row.get("case_id") or row.get("id") or "",
            "title": row.get("title") or "",
            "severity": row.get("severity") or "",
            "status": row.get("status") or "",
            "domain": row.get("domain") or "",
            "asset": row.get("asset") or "",
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "sla_target_hours": row.get("sla_target_hours") or 0,
            "elapsed_hours": row.get("elapsed_hours") or 0,
            "sla_status": row.get("sla_status") or "",
            "owner": row.get("owner") or "",
            "summary": row.get("summary") or "",
            "next_action": row.get("next_action") or "",
        }
        for row in rows
    ]


def fetch_sla_summary(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    return _execute_one(engine, "sla_summary", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_mitre_mapping(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "mitre_mapping", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_top_assets(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "top_assets", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_domain_scores(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "domain_scores", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_domains(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "domains", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_findings(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "findings", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_technical_evidence(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "technical_evidence", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_operational_timeline(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "operational_timeline", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_actions_worked(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[str]:
    rows = _execute_rows(engine, "actions_worked", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})
    return [row.get("action_text") for row in rows if row.get("action_text")]


def fetch_automation_suggestions(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "automation_suggestions", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_next_actions(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return _execute_rows(engine, "next_actions", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})


def fetch_results(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    row = _execute_one(engine, "results", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})
    highlights = row.get("highlights_json")
    if isinstance(highlights, str):
        try:
            highlights = json.loads(highlights)
        except json.JSONDecodeError:
            highlights = []
    return {"summary": row.get("summary") or "", "highlights": highlights or []}


def fetch_pending_findings(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[str]:
    rows = _execute_rows(engine, "pending_findings", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})
    return [row.get("finding_text") for row in rows if row.get("finding_text")]


def fetch_security_news(engine: Engine, tenant_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows = _execute_rows(engine, "security_news", {"tenant_id": tenant_id, "start_date": start_date, "end_date": end_date})
    normalized: list[dict[str, Any]] = []
    for row in rows:
        links = row.get("links_json")
        if isinstance(links, str):
            try:
                links = json.loads(links)
            except json.JSONDecodeError:
                links = [links]
        normalized.append({
            "title": row.get("title") or "",
            "date": str(row.get("date") or ""),
            "source": row.get("source") or "",
            "summary": row.get("summary") or "",
            "links": links or [],
        })
    return normalized


def _default_annex() -> list[dict[str, str]]:
    return [
        {"reference": "QUERY CONFIGURATION", "detail": "Las consultas deben ajustarse al esquema real de la BD."},
        {"reference": "Modo solo lectura", "detail": "El loader rechaza cualquier query que no comience con SELECT."},
    ]


def build_report_data_from_database(fallback_data: dict[str, Any] | None = None) -> dict[str, Any]:
    tenant_id = os.environ.get("REPORT_TENANT_ID", "tenant-jockey-salud").strip()
    start_date = os.environ.get("REPORT_PERIOD_START", "2026-06-20").strip()
    end_date = os.environ.get("REPORT_PERIOD_END", "2026-06-26").strip()

    engine = create_db_engine()
    report_data: dict[str, Any] = fallback_data.copy() if fallback_data else {}

    tenant_info = fetch_tenant_info(engine, tenant_id)
    report_data["tenant"] = tenant_info or report_data.get("tenant") or {"id": tenant_id, "name": tenant_id, "tenant_id": tenant_id}
    report_data["report"] = fetch_report_metadata(engine, tenant_id, start_date, end_date) or report_data.get("report", {})
    report_data["report"]["id"] = report_data["report"].get("id") or "report-demo-001"
    report_data["report"]["generated_at"] = report_data["report"].get("generated_at") or ""
    report_data["tools"] = fetch_tools(engine, tenant_id) or report_data.get("tools", [])
    report_data["severity_summary"] = fetch_severity_summary(engine, tenant_id, start_date, end_date) or report_data.get("severity_summary", {})
    report_data["security_posture"] = fetch_security_posture(engine, tenant_id, start_date, end_date) or report_data.get("security_posture", {})
    report_data["cases"] = fetch_cases(engine, tenant_id, start_date, end_date) or report_data.get("cases", [])
    report_data["sla_summary"] = fetch_sla_summary(engine, tenant_id, start_date, end_date) or report_data.get("sla_summary", {})
    report_data["mitre_mapping"] = fetch_mitre_mapping(engine, tenant_id, start_date, end_date) or report_data.get("mitre_mapping", [])
    report_data["top_assets"] = fetch_top_assets(engine, tenant_id, start_date, end_date) or report_data.get("top_assets", [])
    report_data["domain_scores"] = fetch_domain_scores(engine, tenant_id, start_date, end_date) or report_data.get("domain_scores", [])
    report_data["domains"] = fetch_domains(engine, tenant_id, start_date, end_date) or report_data.get("domains", [])
    report_data["findings"] = fetch_findings(engine, tenant_id, start_date, end_date) or report_data.get("findings", [])
    report_data["technical_evidence"] = fetch_technical_evidence(engine, tenant_id, start_date, end_date) or report_data.get("technical_evidence", [])
    report_data["operational_timeline"] = fetch_operational_timeline(engine, tenant_id, start_date, end_date) or report_data.get("operational_timeline", [])
    report_data["actions_worked"] = fetch_actions_worked(engine, tenant_id, start_date, end_date) or report_data.get("actions_worked", [])
    report_data["automation_suggestions"] = fetch_automation_suggestions(engine, tenant_id, start_date, end_date) or report_data.get("automation_suggestions", [])
    report_data["next_actions"] = fetch_next_actions(engine, tenant_id, start_date, end_date) or report_data.get("next_actions", [])
    report_data["results"] = fetch_results(engine, tenant_id, start_date, end_date) or report_data.get("results", {})
    report_data["pending_findings"] = fetch_pending_findings(engine, tenant_id, start_date, end_date) or report_data.get("pending_findings", [])
    report_data["security_news"] = fetch_security_news(engine, tenant_id, start_date, end_date) or report_data.get("security_news", [])
    report_data["annex"] = report_data.get("annex") or _default_annex()
    return report_data
