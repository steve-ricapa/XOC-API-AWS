from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from src.reports.store import get_report_or_404
from src.reports.storage import upload_artifact
from src.shared.logging import logger


def handler(event: dict, context) -> dict:
    report_id = event.get("reportId")
    tenant_id = event.get("tenantId")
    report_type = event.get("reportType", "")

    if not report_id or not tenant_id:
        raise ValueError("reportId and tenantId are required")

    tenant_id = int(tenant_id)

    item = get_report_or_404(tenant_id, report_id)
    filters = item.get("filters") or {}

    collected = _collect_from_sources(tenant_id, report_id, report_type, filters)

    artifact_key = upload_artifact(tenant_id, report_id, "collected-data.json", collected)
    logger.info("Collected data uploaded to %s for report %s", artifact_key, report_id)

    return {
        "reportId": report_id,
        "tenantId": tenant_id,
        "reportType": report_type,
        "collectedDataKey": artifact_key,
        "tenantName": collected.get("tenant", {}).get("name", f"Tenant-{tenant_id}"),
        "severitySummary": collected.get("severity_summary", {}),
        "findings": collected.get("findings", []),
        "domains": collected.get("domains", []),
    }


def _collect_from_sources(tenant_id: int, report_id: str, report_type: str, filters: dict) -> dict:
    return _build_minimal_report_context(tenant_id, report_id, report_type, filters)


def _build_minimal_report_context(tenant_id: int, report_id: str, report_type: str, filters: dict) -> dict:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    period = _build_period_string(filters)

    tenant_name = f"Tenant-{tenant_id}"

    return {
        "tenant": {
            "id": str(tenant_id),
            "name": tenant_name,
        },
        "report": {
            "id": report_id,
            "title": "Minority Report - XOC",
            "service": "Servicio de Monitoreo Proactivo XOC",
            "generated_at": generated_at,
            "period": period,
            "prepared_by": "TXDXSECURE",
            "executive_summary": (
                "Durante la semana evaluada se mantuvo el monitoreo proactivo sobre superficies publicas, "
                "plataformas criticas e infraestructura priorizada."
            ),
            "results": (
                "Se obtuvo visibilidad consolidada de exposiciones criticas y altas, se priorizaron actividades de "
                "mitigacion y se mantuvo evidencia estructurada para seguimiento semanal."
            ),
        },
        "tools": ["MonEvents", "MonVulE", "MonVulC", "MonApps", "MonNet", "MonInfra"],
        "severity_summary": {
            "critical": 2,
            "high": 7,
            "medium": 33,
            "low": 8,
            "informational": 147,
        },
        "findings": _build_demo_findings(),
        "domains": _build_demo_domains(),
        "actions_worked": [
            "Validacion de exposicion de servicios administrativos en activos publicos.",
            "Revision de vulnerabilidades altas asociadas a servidores criticos.",
            "Afinamiento de reglas y excepciones en controles perimetrales.",
            "Consolidacion de hallazgos recurrentes para priorizacion operativa.",
        ],
        "security_news": _build_demo_news(),
    }


def _build_period_string(filters: dict) -> str:
    date_from = filters.get("date_from", "")
    date_to = filters.get("date_to", "")
    if date_from and date_to:
        return f"Del {date_from} al {date_to}"
    return "Ultima semana evaluada"


def _build_demo_findings() -> list[dict]:
    return [
        {"id": "WEB-001", "domain": "Dominio de Web Externo", "title": "Versiones desactualizadas en portal publico", "affected_hosts": "portal.jockeysalud.example", "severity": "Alto", "description": "Componentes web con versiones expuestas.", "recommendation": "Actualizar componentes."},
        {"id": "IP-014", "domain": "Dominio de IPs Públicas", "title": "Servicio administrativo expuesto a internet", "affected_hosts": "181.10.10.25", "severity": "Critico", "description": "Superficie administrativa accesible desde internet.", "recommendation": "Restringir acceso por VPN."},
        {"id": "FW-009", "domain": "Dominio de FW", "title": "Politicas con reglas amplias", "affected_hosts": "fw-core-01", "severity": "Medio", "description": "Reglas con origen/destino amplios.", "recommendation": "Aplicar minimo privilegio."},
        {"id": "SRV-021", "domain": "Dominio Infraestructura de Computo - Servers", "title": "Parches pendientes en servidores Windows", "affected_hosts": "srv-app-01, srv-db-02", "severity": "Alto", "description": "Parches de seguridad pendientes.", "recommendation": "Programar ventana de mantenimiento."},
        {"id": "SW-005", "domain": "Dominio Infraestructura de Red - Switches", "title": "SNMP con configuracion heredada", "affected_hosts": "sw-dist-03", "severity": "Bajo", "description": "Parametros heredados de monitoreo.", "recommendation": "Migrar a configuracion segura."},
    ]


def _build_demo_domains() -> list[dict]:
    return [
        {"name": "Dominio de Web Externo", "summary": "Hallazgos asociados a exposicion de versiones y cabeceras de seguridad.", "findings": ["WEB-001"]},
        {"name": "Dominio de IPs Públicas", "summary": "Servicios con exposicion publica que requieren restriccion.", "findings": ["IP-014"]},
        {"name": "Dominio de FW", "summary": "Afinamiento de reglas y validacion de reglas temporales.", "findings": ["FW-009"]},
        {"name": "Dominio Infraestructura de Computo - Servers", "summary": "Gestion de parches y controles de endurecimiento.", "findings": ["SRV-021"]},
        {"name": "Dominio Infraestructura de Red - Switches", "summary": "Configuraciones heredadas y estandarizacion pendiente.", "findings": ["SW-005"]},
        {"name": "Dominio Infraestructura de Red - WIFI", "summary": "No se observaron incidentes criticos.", "findings": []},
        {"name": "Dominio Infraestructura de Computo - Desktops", "summary": "Oportunidades de mejora en higiene de endpoints.", "findings": []},
        {"name": "Dominio Infraestructura OT/IoT", "summary": "Ampliar inventario y establecer linea base de monitoreo.", "findings": []},
    ]


def _build_demo_news() -> list[dict]:
    return [
        {"title": "Nueva campana de phishing dirigida a sector salud", "date": "2026-06-24", "source": "XOC Threat Intel", "summary": "Campanas con archivos adjuntos y robo de credenciales.", "links": ["https://example.com/news/phishing-health"]},
        {"title": "Actualizacion critica para plataforma perimetral", "date": "2026-06-22", "source": "Vendor Advisory", "summary": "Actualizacion para corregir vulnerabilidades explotables.", "links": ["https://example.com/news/perimeter-advisory"]},
    ]
