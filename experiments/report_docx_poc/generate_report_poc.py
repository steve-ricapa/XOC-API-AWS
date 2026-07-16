from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import boto3
import matplotlib
import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Mm, Pt
from docxtpl import DocxTemplate, InlineImage
from jinja2 import Environment, StrictUndefined
from lxml import etree

matplotlib.use("Agg")
import matplotlib.pyplot as plt


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
REFERENCES_DIR = BASE_DIR / "references"
OUTPUT_DIR = BASE_DIR / "output"
REPORTS_ROOT_DIR = OUTPUT_DIR / "reports"
MOCK_DATA_PATH = DATA_DIR / "mock_report_data.json"
TEMPLATE_PATH = TEMPLATES_DIR / "security-report-v1.docx"
CHART_PATH = OUTPUT_DIR / "severity_chart.png"

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


def ensure_directories() -> None:
    for path in (DATA_DIR, TEMPLATES_DIR, REFERENCES_DIR, OUTPUT_DIR, REPORTS_ROOT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def report_output_dir(data: dict) -> Path:
    return REPORTS_ROOT_DIR / data["tenant"]["id"] / data["report"]["id"]


def report_path(data: dict) -> Path:
    return report_output_dir(data) / "v1-generated.docx"


def temp_rendered_path(data: dict) -> Path:
    return report_output_dir(data) / "v1-rendered-intermediate.docx"


def find_source_template_path() -> Path | None:
    candidates = sorted(
        [path for path in BASE_DIR.parent.glob("*.docx") if path.name.lower() != TEMPLATE_PATH.name.lower()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def resolve_writable_report_path(data: dict) -> Path:
    target_path = report_path(data)
    if not target_path.exists():
        return target_path
    try:
        with target_path.open("ab"):
            pass
        return target_path
    except PermissionError:
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
        return target_path.with_name(f"v1-generated-{suffix}.docx")


def build_default_mock_data() -> dict:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    findings = [
        {
            "id": "WEB-001",
            "domain": "Dominio de Web Externo",
            "title": "Versiones desactualizadas en portal publico",
            "affected_hosts": "portal.jockeysalud.example",
            "severity": "Alto",
            "description": "Se identificaron componentes web con versiones expuestas y desactualizadas en el portal publico.",
            "recommendation": "Actualizar componentes y validar encabezados de endurecimiento.",
        },
        {
            "id": "IP-014",
            "domain": "Dominio de IPs Publicas",
            "title": "Servicio administrativo expuesto a internet",
            "affected_hosts": "181.10.10.25",
            "severity": "Critico",
            "description": "Se detecto superficie administrativa accesible desde internet sin controles compensatorios visibles.",
            "recommendation": "Restringir acceso por VPN o listas de control y revisar MFA.",
        },
        {
            "id": "FW-009",
            "domain": "Dominio de FW",
            "title": "Politicas con reglas amplias",
            "affected_hosts": "fw-core-01",
            "severity": "Medio",
            "description": "Existen reglas con origen/destino amplios que requieren afinamiento.",
            "recommendation": "Aplicar principio de minimo privilegio y documentar excepciones.",
        },
        {
            "id": "SRV-021",
            "domain": "Dominio Infraestructura de Computo - Servers",
            "title": "Parches pendientes en servidores Windows",
            "affected_hosts": "srv-app-01, srv-db-02",
            "severity": "Alto",
            "description": "Se detectaron parches de seguridad pendientes en servidores criticos.",
            "recommendation": "Programar ventana de mantenimiento y validar reinicios controlados.",
        },
        {
            "id": "SW-005",
            "domain": "Dominio Infraestructura de Red - Switches",
            "title": "SNMP con configuracion heredada",
            "affected_hosts": "sw-dist-03",
            "severity": "Bajo",
            "description": "Persisten parametros heredados de monitoreo con oportunidad de endurecimiento.",
            "recommendation": "Migrar a configuracion segura y rotar comunidades si aplica.",
        },
    ]
    domains = [
        {
            "name": "Dominio de Web Externo",
            "summary": "Se observaron hallazgos asociados a exposicion de versiones y cabeceras de seguridad en portales visibles desde internet.",
            "findings": ["WEB-001"],
        },
        {
            "name": "Dominio de IPs Publicas",
            "summary": "Se identificaron servicios con exposicion publica que requieren mayor restriccion y revision de accesos remotos.",
            "findings": ["IP-014"],
        },
        {
            "name": "Dominio de FW",
            "summary": "Se recomienda continuar el afinamiento de reglas y validar reglas temporales heredadas.",
            "findings": ["FW-009"],
        },
        {
            "name": "Dominio Infraestructura de Computo - Servers",
            "summary": "El mayor foco operativo se mantiene en la gestion de parches y controles de endurecimiento de servidores.",
            "findings": ["SRV-021"],
        },
        {
            "name": "Dominio Infraestructura de Red - Switches",
            "summary": "Hallazgos de bajo riesgo relacionados con configuraciones heredadas y estandarizacion pendiente.",
            "findings": ["SW-005"],
        },
        {
            "name": "Dominio Infraestructura de Red - WIFI",
            "summary": "No se observaron incidentes criticos en el periodo; se recomienda continuar validaciones de segmentacion.",
            "findings": [],
        },
        {
            "name": "Dominio Infraestructura de Computo - Desktops",
            "summary": "Se mantienen oportunidades de mejora en higiene de endpoints y visibilidad de versiones instaladas.",
            "findings": [],
        },
        {
            "name": "Dominio Infraestructura OT/IoT",
            "summary": "Se recomienda ampliar inventario y establecer una linea base de monitoreo para activos OT/IoT.",
            "findings": [],
        },
    ]
    return {
        "tenant": {"id": "tenant-jockey-salud", "name": "JOCKEY SALUD"},
        "report": {
            "id": "report-demo-001",
            "title": "Minority Report - XOC",
            "service": "Servicio de Monitoreo Proactivo XOC",
            "generated_at": generated_at,
            "period": "Del 20 de junio al 26 de junio del 2026",
            "prepared_by": "TXDXSECURE",
            "executive_summary": (
                "Durante la semana evaluada se mantuvo el monitoreo proactivo sobre superficies publicas, "
                "plataformas criticas e infraestructura priorizada. Se identificaron exposiciones que requieren "
                "seguimiento, destacando activos con servicios administrativos expuestos, pendientes de parchado y "
                "oportunidades de endurecimiento. El objetivo de este reporte es consolidar hallazgos accionables "
                "para revision del analista y coordinacion con los equipos responsables."
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
        "findings": findings,
        "domains": domains,
        "actions_worked": [
            "Validacion de exposicion de servicios administrativos en activos publicos.",
            "Revision de vulnerabilidades altas asociadas a servidores criticos.",
            "Afinamiento de reglas y excepciones en controles perimetrales.",
            "Consolidacion de hallazgos recurrentes para priorizacion operativa.",
        ],
        "security_news": [
            {
                "title": "Nueva campana de phishing dirigida a sector salud",
                "date": "2026-06-24",
                "source": "XOC Threat Intel",
                "summary": "Se observaron campanas con archivos adjuntos y robo de credenciales orientadas a organizaciones de salud.",
                "links": ["https://example.com/news/phishing-health"],
            },
            {
                "title": "Actualizacion critica para plataforma perimetral",
                "date": "2026-06-22",
                "source": "Vendor Advisory",
                "summary": "El fabricante publico una actualizacion para corregir vulnerabilidades explotables de forma remota.",
                "links": ["https://example.com/news/perimeter-advisory"],
            },
        ],
    }


def ensure_mock_data() -> Path:
    if not MOCK_DATA_PATH.exists():
        MOCK_DATA_PATH.write_text(json.dumps(build_default_mock_data(), indent=2, ensure_ascii=False), encoding="utf-8")
    return MOCK_DATA_PATH


def _set_cell_text(cell, text: str, *, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(10)


def create_template_if_missing() -> Path:
    source_template_path = find_source_template_path()
    if source_template_path and source_template_path.exists():
        shutil.copy2(source_template_path, TEMPLATE_PATH)
        return TEMPLATE_PATH

    if TEMPLATE_PATH.exists():
        return TEMPLATE_PATH

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Mm(20)
    section.bottom_margin = Mm(20)
    section.left_margin = Mm(20)
    section.right_margin = Mm(20)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title.add_run("{{ report.title }}")
    title_run.bold = True
    title_run.font.size = Pt(24)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run("{{ report.service }}").font.size = Pt(14)

    tenant_p = doc.add_paragraph()
    tenant_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tenant_p.add_run("Cliente: {{ tenant.name }}").font.size = Pt(12)

    period_p = doc.add_paragraph()
    period_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    period_p.add_run("Periodo: {{ report.period }}").font.size = Pt(12)

    generated_p = doc.add_paragraph()
    generated_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    generated_p.add_run("Generado: {{ report.generated_at }}").font.size = Pt(12)

    prepared_p = doc.add_paragraph()
    prepared_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    prepared_p.add_run("Preparado por: {{ report.prepared_by }}").font.size = Pt(12)

    doc.add_page_break()

    def heading(text: str) -> None:
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(15)

    heading("1. Datos generales")
    doc.add_paragraph("Tenant ID: {{ tenant.id }}")
    doc.add_paragraph("Reporte ID: {{ report.id }}")

    heading("2. Herramientas")
    doc.add_paragraph("{% for tool in tools %}- {{ tool }}{% endfor %}")

    heading("3. Resumen ejecutivo")
    doc.add_paragraph("{{ report.executive_summary }}")

    heading("4. Analisis de severidades")
    doc.add_paragraph(
        "Critico: {{ severity_summary.critical }} | Alto: {{ severity_summary.high }} | Medio: {{ severity_summary.medium }} | "
        "Bajo: {{ severity_summary.low }} | Informativo: {{ severity_summary.informational }}"
    )

    heading("5. Grafico de vulnerabilidades")
    doc.add_paragraph("{{ severity_chart }}")

    heading("6. Tabla de findings")
    doc.add_paragraph("{% for finding in findings %}{{ finding.id }} | {{ finding.domain }} | {{ finding.title }} | {{ finding.severity }}\n{% endfor %}")
    doc.add_paragraph("[[FINDINGS_TABLE]]")

    heading("7. Seguridad por dominio")
    doc.add_paragraph(
        "{% for domain in domains %}{{ loop.index }}. {{ domain.name }}\nResumen: {{ domain.summary }}\nFindings relacionados: {{ domain.findings | join(', ') if domain.findings else 'Sin findings asociados' }}\n\n{% endfor %}"
    )

    heading("8. Acciones trabajadas durante la semana")
    doc.add_paragraph("{% for action in actions_worked %}- {{ action }}\n{% endfor %}")

    heading("9. Resultados obtenidos")
    doc.add_paragraph("{{ report.results }}")

    heading("10. Noticias de seguridad")
    doc.add_paragraph(
        "{% for news in security_news %}- {{ news.date }} | {{ news.source }} | {{ news.title }}\n{{ news.summary }}\nEnlaces: {{ news.links | join(', ') }}\n\n{% endfor %}"
    )

    doc.save(TEMPLATE_PATH)
    return TEMPLATE_PATH


def load_mock_data() -> dict:
    return json.loads(MOCK_DATA_PATH.read_text(encoding="utf-8"))


def build_severity_dataframe(severity_summary: dict) -> pd.DataFrame:
    ordered_rows = [
        ("Crítico", severity_summary["critical"], "#b91c1c"),
        ("Alto", severity_summary["high"], "#ea580c"),
        ("Medio", severity_summary["medium"], "#d97706"),
        ("Bajo", severity_summary["low"], "#2563eb"),
        ("Informativo", severity_summary["informational"], "#64748b"),
    ]
    return pd.DataFrame(ordered_rows, columns=["severity", "count", "color"])


def create_severity_chart(data: dict) -> Path:
    df = build_severity_dataframe(data["severity_summary"])
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(df["severity"], df["count"], color=df["color"])
    axis.set_title("Comparativo de vulnerabilidades por severidad")
    axis.set_ylabel("Cantidad")
    axis.set_xlabel("Severidad")
    axis.grid(axis="y", linestyle="--", alpha=0.35)

    for idx, value in enumerate(df["count"]):
        axis.text(idx, value + max(1, value * 0.02), str(value), ha="center", va="bottom", fontsize=9)

    figure.tight_layout()
    figure.savefig(CHART_PATH, dpi=200)
    plt.close(figure)
    return CHART_PATH


def build_render_context(template: DocxTemplate, data: dict) -> dict:
    context = deepcopy(data)
    context["severity_chart"] = InlineImage(template, str(CHART_PATH), width=Mm(140))
    return context


def render_docx(data: dict) -> Path:
    report_output_dir(data).mkdir(parents=True, exist_ok=True)
    template = DocxTemplate(str(TEMPLATE_PATH))
    env = Environment(undefined=StrictUndefined, autoescape=False)
    context = build_render_context(template, data)
    template.render(context, jinja_env=env)
    tmp_path = temp_rendered_path(data)
    template.save(tmp_path)
    return tmp_path


def _replace_paragraph_text(paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


def _replace_cover_prepared_line(paragraph, tenant_name: str, prepared_by: str) -> None:
    if len(paragraph.runs) >= 5 and "\t" in paragraph.text:
        paragraph.runs[0].text = tenant_name
        paragraph.runs[1].text = ""
        paragraph.runs[2].text = ""
        paragraph.runs[3].text = "\t"
        paragraph.runs[4].text = prepared_by
        for run in paragraph.runs[5:]:
            run.text = ""
        return

    _replace_paragraph_text(paragraph, f"{tenant_name}\t{prepared_by}")


def _clear_table_rows(table) -> None:
    while len(table.rows) > 1:
        table._tbl.remove(table.rows[-1]._tr)


def _add_finding_row(table, record: dict) -> None:
    row = table.add_row().cells
    _set_cell_text(row[0], str(record["id"]))
    _set_cell_text(row[1], str(record["title"]))
    _set_cell_text(row[2], str(record["affected_hosts"]))
    _set_cell_text(row[3], str(record["severity"]))


def _build_service_description(data: dict) -> str:
    return (
        f"{data['report']['service']} implementado por {data['report']['prepared_by']} para garantizar seguridad, "
        f"disponibilidad y rendimiento, mediante capacidades de correlación de eventos, gestión de vulnerabilidades, "
        f"monitoreo de infraestructura y validación operativa sobre activos críticos del cliente {data['tenant']['name']}."
    )


def _build_data_base_paragraph(data: dict) -> str:
    return (
        f"{data['report']['prepared_by']} implementó este servicio integral de monitoreo y gestión de vulnerabilidades "
        f"con base en el análisis técnico del entorno evaluado, permitiendo consolidar la exposición por dominio y priorizar "
        f"los activos con mayor criticidad operativa."
    )


def _build_summary_intro(data: dict) -> tuple[str, str, str]:
    actions_count = len(data["actions_worked"])
    sev = data["severity_summary"]
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
        f"La semana actual concentra {sev['critical']} hallazgos críticos, {sev['high']} altos, {sev['medium']} medios, {sev['low']} bajos y "
        f"{sev['informational']} informativos. El reporte consolida la postura observada y sirve como base editable para seguimiento del servicio."
    )
    return first, second, third


def _build_comparative_text(data: dict) -> str:
    sev = data["severity_summary"]
    return (
        f"En la semana actual se registran {sev['critical']} hallazgos críticos, {sev['high']} altos, {sev['medium']} medios, {sev['low']} bajos y "
        f"{sev['informational']} informativos. El comparativo semanal debe revisarse junto con la tendencia histórica para priorizar mitigaciones y validar "
        "si los controles implementados reducen la exposición operacional."
    )


def _build_histogram_text() -> str:
    return (
        "El histograma evidencia la distribución residual por severidad, concentrando mayor volumen en hallazgos informativos y medios, mientras que las "
        "severidades críticas y altas permanecen como prioridad para remediación, endurecimiento y validación operativa."
    )


def _build_results_summary() -> str:
    return (
        "El análisis de vulnerabilidades permitió consolidar la exposición por dominio, ordenar hallazgos por criticidad y mantener una vista semanal utilizable "
        "por el analista para seguimiento, priorización y coordinación de próximos pasos."
    )


def _build_result_paragraphs(data: dict) -> list[str]:
    return [
        f"Durante el periodo evaluado se mantuvo el seguimiento del servicio {data['report']['service']} sobre los activos priorizados del cliente {data['tenant']['name']}, con foco en hallazgos accionables y visibilidad técnica semanal.",
        "La consolidación por dominios permitió clasificar exposiciones, identificar activos con mayor criticidad y estructurar una base editable para revisión del analista y coordinación con las áreas responsables.",
        "Se mantuvo el análisis sobre vulnerabilidades críticas y altas, así como la revisión de superficies públicas, componentes de red e infraestructura de cómputo.",
        "La información generada facilita la toma de decisiones sobre remediación, endurecimiento y seguimiento operacional de hallazgos recurrentes.",
        "Los hallazgos se organizaron de forma que puedan trazarse por severidad, dominio y recomendación, manteniendo consistencia con el formato corporativo del informe.",
        data["report"]["results"],
    ]


def _build_requirement_text(data: dict) -> str:
    return (
        f"Se requiere revisar y priorizar los hallazgos críticos y altos consolidados en el informe {data['report']['id']}, definiendo responsables, ventanas de intervención y criterios de cierre para el periodo reportado."
    )


def _normalize_domain(domain_name: str) -> str:
    mapping = {
        "Dominio de IPs Públicas": "Dominio de IPs Publicas",
        "Dominio de IPs Publicas": "Dominio de IPs Publicas",
        "Dominio Infraestructura de Computo - Desktops": "Dominio Infraestructura de de Computo - Desktops",
        "Dominio Infraestructura de de Computo - Desktops": "Dominio Infraestructura de de Computo - Desktops",
    }
    return mapping.get(domain_name, domain_name)


def _group_findings_by_domain(data: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for finding in data["findings"]:
        grouped.setdefault(_normalize_domain(finding["domain"]), []).append(finding)
    return grouped


def _replace_known_paragraphs(doc: Document, data: dict) -> None:
    summary_1, summary_2, summary_3 = _build_summary_intro(data)
    result_paragraphs = _build_result_paragraphs(data)
    if len(doc.paragraphs) >= 169:
        paragraph_updates = {
            33: data["report"]["period"],
            40: _build_service_description(data),
            42: data["report"]["period"],
            50: _build_data_base_paragraph(data),
            53: summary_1,
            54: summary_2,
            56: summary_3,
            58: _build_comparative_text(data),
            62: _build_histogram_text(),
            67: _build_results_summary(),
            69: result_paragraphs[0],
            70: result_paragraphs[1],
            71: result_paragraphs[2],
            72: result_paragraphs[3],
            73: result_paragraphs[4],
            74: result_paragraphs[5],
            76: "Se continuará con la validación de mitigaciones priorizadas, actualización de evidencias y seguimiento de hallazgos críticos y altos.",
            79: "Se programará el seguimiento de hallazgos expuestos y revisión de controles compensatorios aplicados durante el siguiente corte.",
            81: _build_requirement_text(data),
            82: "Se recomienda coordinar la revisión prioritaria de activos con exposición crítica y alta, así como la validación técnica de hallazgos que requieran ventana de mantenimiento.",
            85: data["domains"][0]["summary"],
            91: data["domains"][1]["summary"],
            96: data["domains"][2]["summary"],
            102: data["domains"][3]["summary"],
            108: data["domains"][4]["summary"],
            114: data["domains"][5]["summary"],
            120: data["domains"][6]["summary"],
            126: data["domains"][7]["summary"],
            144: result_paragraphs[0],
            145: result_paragraphs[1],
            146: result_paragraphs[2],
            149: "Queda pendiente la revisión analítica y operativa de los hallazgos de mayor criticidad, así como la confirmación de acciones correctivas implementadas durante el siguiente corte semanal.",
        }
        action_start = 133
        action_slots = 8
        news_paragraph_start = 151
        blocks_per_news = 9
    else:
        paragraph_updates = {
            34: _build_service_description(data),
            37: data["report"]["period"],
            47: _build_data_base_paragraph(data),
            50: summary_1,
            52: summary_2,
            54: summary_3,
            59: _build_comparative_text(data),
            81: _build_histogram_text(),
            87: _build_results_summary(),
            90: result_paragraphs[0],
            92: result_paragraphs[1],
            94: result_paragraphs[2],
            96: result_paragraphs[3],
            98: result_paragraphs[4],
            100: result_paragraphs[5],
            102: data["report"]["results"],
            104: "Se continuará con la validación de mitigaciones priorizadas, actualización de evidencias y seguimiento de hallazgos críticos y altos.",
            106: "Asimismo, se mantendrá el monitoreo sobre activos con exposición recurrente y se actualizarán las recomendaciones conforme al avance de remediación.",
            108: _build_requirement_text(data),
            112: data["domains"][0]["summary"],
            116: data["domains"][1]["summary"],
            123: data["domains"][2]["summary"],
            127: data["domains"][3]["summary"],
            130: data["domains"][4]["summary"],
            133: data["domains"][5]["summary"],
            136: data["domains"][6]["summary"],
            139: data["domains"][7]["summary"],
            158: result_paragraphs[0],
            160: result_paragraphs[1],
            162: result_paragraphs[2],
            164: result_paragraphs[3],
            166: result_paragraphs[4],
            168: result_paragraphs[5],
            172: "Queda pendiente la revisión analítica y operativa de los hallazgos de mayor criticidad, así como la confirmación de acciones correctivas implementadas durante el siguiente corte semanal.",
        }
        action_start = 142
        action_slots = 12
        news_paragraph_start = 175
        blocks_per_news = 10

    for index, text in paragraph_updates.items():
        _replace_paragraph_text(doc.paragraphs[index], text)

    if len(doc.paragraphs) >= 169:
        _replace_cover_prepared_line(doc.paragraphs[31], data["tenant"]["name"], data["report"]["prepared_by"])

    for offset in range(action_slots):
        action_text = data["actions_worked"][offset] if offset < len(data["actions_worked"]) else ""
        _replace_paragraph_text(doc.paragraphs[action_start + offset], action_text)

    for news_idx in range(2):
        news = data["security_news"][news_idx] if news_idx < len(data["security_news"]) else None
        base = news_paragraph_start + (news_idx * blocks_per_news)
        if news is None:
            for paragraph_idx in range(base, min(base + blocks_per_news, len(doc.paragraphs))):
                _replace_paragraph_text(doc.paragraphs[paragraph_idx], "")
            continue
        _replace_paragraph_text(doc.paragraphs[base], news["title"])
        _replace_paragraph_text(doc.paragraphs[base + 1], f"Fuente: {news['source']}")
        _replace_paragraph_text(doc.paragraphs[base + 2], news["summary"])
        _replace_paragraph_text(doc.paragraphs[base + 3], f"Fecha de referencia: {news['date']}.")
        _replace_paragraph_text(doc.paragraphs[base + 4], f"Enlaces: {', '.join(news['links'])}")
        _replace_paragraph_text(doc.paragraphs[base + 5], "Se recomienda evaluar impacto, exposición local y controles compensatorios vigentes antes de definir la remediación.")
        _replace_paragraph_text(doc.paragraphs[base + 6], "El contenido se incluye como insumo contextual para el analista y no reemplaza validaciones internas ni gestión formal de cambios.")
        for paragraph_idx in range(base + 7, min(base + blocks_per_news, len(doc.paragraphs))):
            _replace_paragraph_text(doc.paragraphs[paragraph_idx], "")


def _replace_headers_and_footers(doc: Document, data: dict) -> None:
    tenant_name = data["tenant"]["name"]
    prepared_by = data["report"]["prepared_by"]
    footer_text = f"{prepared_by}                                              Minority Report XOC\t{tenant_name}"

    for section in doc.sections:
        for paragraph in section.footer.paragraphs:
            if paragraph.text.strip():
                _replace_paragraph_text(paragraph, footer_text)


def _populate_domain_tables(doc: Document, data: dict) -> None:
    findings_by_domain = _group_findings_by_domain(data)
    if len(doc.tables) >= 10:
        table_domain_pairs = [
            (1, "Dominio de Web Externo"),
            (2, "Dominio de IPs Públicas"),
            (3, "Dominio de FW"),
            (4, "Dominio Infraestructura de Computo - Servers"),
            (5, "Dominio Infraestructura de Red - Switches"),
            (6, "Dominio Infraestructura de Red - WIFI"),
            (7, "Dominio Infraestructura de Computo - Desktops"),
            (8, "Dominio Infraestructura de Computo - Desktops"),
            (9, "Dominio Infraestructura OT/IoT"),
        ]
    else:
        table_domain_pairs = list(enumerate(DOMAIN_TABLE_ORDER, start=1))

    populated_desktop_split = False
    for table_idx, domain_name in table_domain_pairs:
        table = doc.tables[table_idx]
        _clear_table_rows(table)
        domain_findings = findings_by_domain.get(_normalize_domain(domain_name), [])
        if len(doc.tables) >= 10 and domain_name == "Dominio Infraestructura de Computo - Desktops":
            if populated_desktop_split:
                continue
            populated_desktop_split = True
        if not domain_findings:
            _add_finding_row(
                table,
                {
                    "id": "N/A",
                    "title": "Sin hallazgos relevantes en este corte",
                    "affected_hosts": "0",
                    "severity": "Informativo",
                },
            )
            continue
        for finding in domain_findings:
            _add_finding_row(table, finding)


def generate_from_real_template(data: dict, final_report_path: Path) -> Path:
    output_dir = report_output_dir(data)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TEMPLATE_PATH, final_report_path)
    doc = Document(final_report_path)
    _replace_known_paragraphs(doc, data)
    _replace_headers_and_footers(doc, data)
    _populate_domain_tables(doc, data)
    doc.save(final_report_path)
    return final_report_path


def _insert_table_after(paragraph, rows: int, cols: int):
    table = paragraph._parent.add_table(rows=rows, cols=cols, width=Inches(7.2))
    paragraph._p.addnext(table._tbl)
    return table


def insert_findings_table(data: dict, final_report_path: Path) -> None:
    tmp_path = temp_rendered_path(data)
    doc = Document(tmp_path)
    findings_df = pd.DataFrame(data["findings"])
    findings_df = findings_df[["id", "domain", "title", "affected_hosts", "severity"]]
    findings_df.columns = ["ID", "Dominio", "Vulnerabilidad", "Hosts", "Severidad"]

    target_paragraph = None
    for paragraph in doc.paragraphs:
        if "[[FINDINGS_TABLE]]" in paragraph.text:
            target_paragraph = paragraph
            break

    if target_paragraph is None:
        raise RuntimeError("No se encontro el marcador [[FINDINGS_TABLE]] en la plantilla renderizada")

    table = _insert_table_after(target_paragraph, rows=1, cols=len(findings_df.columns))
    table.style = "Table Grid"

    for col_idx, column_name in enumerate(findings_df.columns):
        _set_cell_text(table.rows[0].cells[col_idx], column_name, bold=True)

    for record in findings_df.to_dict(orient="records"):
        row_cells = table.add_row().cells
        for col_idx, column_name in enumerate(findings_df.columns):
            _set_cell_text(row_cells[col_idx], str(record[column_name]))

    target_paragraph.text = ""
    doc.save(final_report_path)
    if tmp_path.exists():
        tmp_path.unlink()


def validate_docx_file(docx_path: Path) -> tuple[bool, str]:
    with zipfile.ZipFile(docx_path, "r") as archive:
        if "word/document.xml" not in archive.namelist():
            return False, "No existe word/document.xml en el DOCX generado"
        document_xml = archive.read("word/document.xml")

    etree.fromstring(document_xml)
    return True, "Validacion ZIP/XML OK"


def replace_residual_template_text(docx_path: Path, data: dict) -> None:
    tenant_name = data["tenant"]["name"]
    xml_files_prefix = "word/"
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    fd, tmp_name = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(docx_path, "r") as source_zip, zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
            for item in source_zip.infolist():
                payload = source_zip.read(item.filename)
                if item.filename.startswith(xml_files_prefix) and item.filename.endswith(".xml"):
                    try:
                        root = etree.fromstring(payload)
                    except etree.XMLSyntaxError:
                        target_zip.writestr(item, payload)
                        continue

                    changed = False
                    for text_node in root.xpath(".//w:t", namespaces=ns):
                        text = text_node.text or ""
                        if text == "JOCKEY":
                            text_node.text = tenant_name
                            changed = True
                        elif text == "SALUD":
                            text_node.text = ""
                            changed = True
                        elif text in {"Jockey Salud", "JOCKEY SALUD", "Jockey Salud."}:
                            suffix = "." if text.endswith(".") else ""
                            text_node.text = f"{tenant_name}{suffix}"
                            changed = True

                    if changed:
                        payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

                target_zip.writestr(item, payload)

        shutil.move(str(tmp_path), str(docx_path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def upload_to_s3_if_enabled(report_path: Path, data: dict) -> str | None:
    if os.environ.get("UPLOAD_TO_S3", "false").strip().lower() != "true":
        return None

    bucket_name = os.environ.get("AWS_BUCKET_NAME", "").strip()
    if not bucket_name:
        raise RuntimeError("UPLOAD_TO_S3=true pero AWS_BUCKET_NAME no esta definido")

    client = boto3.client("s3")
    object_key = f"reports/{data['tenant']['id']}/{data['report']['id']}/v1-generated.docx"
    client.upload_file(str(report_path), bucket_name, object_key)
    return f"s3://{bucket_name}/{object_key}"


def main() -> None:
    ensure_directories()
    ensure_mock_data()
    create_template_if_missing()
    data = load_mock_data()
    report_output_dir(data).mkdir(parents=True, exist_ok=True)
    generated_report_path = resolve_writable_report_path(data)
    create_severity_chart(data)
    source_template_path = find_source_template_path()
    if source_template_path and source_template_path.exists():
        generated_report_path = generate_from_real_template(data, generated_report_path)
    else:
        render_docx(data)
        insert_findings_table(data, generated_report_path)
    replace_residual_template_text(generated_report_path, data)
    is_valid, message = validate_docx_file(generated_report_path)
    if not is_valid:
        raise RuntimeError(message)
    s3_uri = upload_to_s3_if_enabled(generated_report_path, data)

    print(f"Template DOCX: {TEMPLATE_PATH}")
    print(f"Mock data JSON: {MOCK_DATA_PATH}")
    print(f"Severity chart PNG: {CHART_PATH}")
    print(f"Generated report DOCX: {generated_report_path}")
    print(message)
    if s3_uri:
        print(f"Uploaded to S3: {s3_uri}")


if __name__ == "__main__":
    main()
