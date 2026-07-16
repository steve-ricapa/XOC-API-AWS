from __future__ import annotations

import json
from datetime import datetime, timezone


DOMAIN_TABLE_ORDER = [
    "Dominio de Web Externo",
    "Dominio de IPs Públicas",
    "Dominio de FW",
    "Dominio Infraestructura de Computo - Servers",
    "Dominio Infraestructura de Red - Switches",
    "Dominio Infraestructura de Red - WIFI",
    "Dominio Infraestructura de Computo - Desktops",
    "Dominio Infraestructura OT/IoT",
]


DEFAULT_SEVERITY_ORDER = ["Crítico", "Alto", "Medio", "Bajo", "Informativo"]


def build_service_description(data: dict) -> str:
    return (
        f"{data['report']['service']} implementado por {data['report']['prepared_by']} para garantizar seguridad, "
        f"disponibilidad y rendimiento, mediante capacidades de correlación de eventos, gestión de vulnerabilidades, "
        f"monitoreo de infraestructura y validación operativa sobre activos críticos del cliente {data['tenant']['name']}."
    )


def build_summary_intro(data: dict) -> tuple[str, str, str]:
    actions_count = len(data.get("actions_worked", []))
    sev = data.get("severity_summary", {})
    first = (
        f"Durante el periodo evaluado se mantuvo el monitoreo proactivo sobre la superficie tecnológica priorizada de {data['tenant']['name']}, "
        f"con seguimiento de hallazgos relevantes, validaciones operativas y consolidación de insumos para remediación. Se gestionaron {actions_count} "
        f"acciones principales durante la semana con foco en reducción de exposición y mejora de controles."
    )
    second = (
        "El análisis permitió identificar hallazgos de distintas severidades sobre dominios externos, infraestructura crítica y componentes de red, "
        "manteniendo trazabilidad para revisión del analista y coordinación con los equipos responsables."
    )
    third = (
        f"La semana actual concentra {sev.get('critical', 0)} hallazgos críticos, {sev.get('high', 0)} altos, "
        f"{sev.get('medium', 0)} medios, {sev.get('low', 0)} bajos y "
        f"{sev.get('informational', 0)} informativos. El reporte consolida la postura observada y sirve como base editable para seguimiento del servicio."
    )
    return first, second, third


def build_result_paragraphs(data: dict) -> list[str]:
    return [
        f"Durante el periodo evaluado se mantuvo el seguimiento del servicio {data['report']['service']} sobre los activos priorizados del cliente {data['tenant']['name']}, con foco en hallazgos accionables y visibilidad técnica semanal.",
        "La consolidación por dominios permitió clasificar exposiciones, identificar activos con mayor criticidad y estructurar una base editable para revisión del analista y coordinación con las áreas responsables.",
        "Se mantuvo el análisis sobre vulnerabilidades críticas y altas, así como la revisión de superficies públicas, componentes de red e infraestructura de cómputo.",
        "La información generada facilita la toma de decisiones sobre remediación, endurecimiento y seguimiento operacional de hallazgos recurrentes.",
        "Los hallazgos se organizaron de forma que puedan trazarse por severidad, dominio y recomendación, manteniendo consistencia con el formato corporativo del informe.",
        data["report"].get("results", ""),
    ]


def build_minimal_report_context(data: dict) -> dict:
    context = dict(data)
    context.setdefault("actions_worked", [])
    context.setdefault("security_news", [])
    context.setdefault("severity_summary", {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0,
    })
    return context
