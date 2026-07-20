from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from src.reports.store import get_document_job_or_404
from src.reports.storage import upload_artifact
from src.shared.logging import logger


def handler(event: dict, context) -> dict:
    document_id = event.get("documentId")
    tenant_id = event.get("tenantId")
    document_type = event.get("documentType", "")

    if not document_id or not tenant_id:
        raise ValueError("documentId and tenantId are required")

    tenant_id = int(tenant_id)

    item = get_document_job_or_404(tenant_id, document_id)
    filters = item.get("filters") or {}
    parameters = item.get("parameters") or {}

    collected = _collect_from_sources(tenant_id, document_id, document_type, filters, parameters)

    artifact_key = upload_artifact(tenant_id, document_id, document_type, "collected-data.json", collected)
    logger.info("Collected data uploaded to %s for document %s", artifact_key, document_id)

    return {
        "documentId": document_id,
        "tenantId": tenant_id,
        "documentType": document_type,
        "collectedDataKey": artifact_key,
        "tenantName": collected.get("tenant", {}).get("name", f"Tenant-{tenant_id}"),
        "severitySummary": collected.get("severity_summary", {}),
        "findings": collected.get("findings", []),
        "domains": collected.get("domains", []),
    }


def _collect_from_sources(tenant_id: int, document_id: str, document_type: str, filters: dict, parameters: dict) -> dict:
    return _build_minimal_document_context(tenant_id, document_id, document_type, filters, parameters)


def _build_minimal_document_context(tenant_id: int, document_id: str, document_type: str, filters: dict, parameters: dict) -> dict:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    period = _build_period_string(filters)

    tenant_name = f"Tenant-{tenant_id}"
    title, service, executive_summary, results = _document_copy(document_type)

    return {
        "tenant": {
            "id": str(tenant_id),
            "name": tenant_name,
        },
        "document": {
            "id": document_id,
            "title": title,
            "service": service,
            "generated_at": generated_at,
            "period": period,
            "prepared_by": "TXDXSECURE",
            "executive_summary": executive_summary,
            "results": results,
        },
        "parameters": parameters,
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


def _document_copy(document_type: str) -> tuple[str, str, str, str]:
    if document_type == "small_report":
        return (
            "Small Report - XOC",
            "Servicio de Generacion Documental XOC",
            "Documento resumido con los hallazgos y acciones mas relevantes del periodo evaluado.",
            "Se consolidaron hallazgos prioritarios y un resumen operativo de seguimiento.",
        )
    if document_type == "informe_soporte":
        return (
            "Informe de Soporte - XOC",
            "Servicio de Soporte Operativo XOC",
            "Documento orientado a registrar actividades de soporte, atenciones ejecutadas y estado del servicio.",
            "Se documentaron acciones de soporte, estado de atenciones y puntos de seguimiento para continuidad operativa.",
        )
    return (
        "Minority Report - XOC",
        "Servicio de Monitoreo Proactivo XOC",
        "Durante la semana evaluada se mantuvo el monitoreo proactivo sobre superficies publicas, plataformas criticas e infraestructura priorizada.",
        "Se obtuvo visibilidad consolidada de exposiciones criticas y altas, se priorizaron actividades de mitigacion y se mantuvo evidencia estructurada para seguimiento semanal.",
    )


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
