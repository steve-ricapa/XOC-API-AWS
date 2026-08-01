from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from src.shared.config import get_secret_string


MINORITY_KEYS = {
    "title",
    "client_name",
    "prepared_by",
    "period",
    "service_name",
    "tools",
    "data_base",
    "executive_summary",
    "vulnerability_comparison",
    "histogram_summary",
    "results_and_next_actions",
    "results_obtained",
    "next_actions",
    "requirements",
    "security_domains",
    "weekly_actions",
    "reinforced_security",
    "pending_findings",
    "security_news",
    "limitations",
    "image_citations",
}

MINORITY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(MINORITY_KEYS),
    "properties": {
        "title": {"type": "string"},
        "client_name": {"type": "string"},
        "prepared_by": {"type": "string"},
        "period": {"type": "string"},
        "service_name": {"type": "string"},
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
        "data_base": {"type": "string"},
        "executive_summary": {"type": "string"},
        "vulnerability_comparison": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "severity_rows"],
            "properties": {
                "summary": {"type": "string"},
                "severity_rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["severity", "previous", "current"],
                        "properties": {
                            "severity": {"type": "string"},
                            "previous": {"type": "string"},
                            "current": {"type": "string"},
                        },
                    },
                },
            },
        },
        "histogram_summary": {"type": "string"},
        "results_and_next_actions": {"type": "string"},
        "results_obtained": {"type": "string"},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "security_domains": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "summary", "findings"],
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "vulnerability", "affected_hosts", "severity"],
                            "properties": {
                                "id": {"type": "string"},
                                "vulnerability": {"type": "string"},
                                "affected_hosts": {"type": "string"},
                                "severity": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        "weekly_actions": {"type": "array", "items": {"type": "string"}},
        "reinforced_security": {"type": "string"},
        "pending_findings": {"type": "array", "items": {"type": "string"}},
        "security_news": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "date", "source", "links", "summary", "recommendation"],
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "source": {"type": "string"},
                    "links": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
        "image_citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "description", "used_in_sections"],
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "used_in_sections": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

PROMPT_BASE = """Eres un analista senior XOC y debes generar el contenido de un Minority Report para cliente.
El reporte es ejecutivo-tecnico, claro, formal y orientado a valor para el cliente.

Reglas obligatorias:
- Usa unicamente la evidencia entregada: datos estructurados del tenant, findings, tickets, dominios y cualquier contexto adicional del analista.
- No inventes fechas, IPs, activos, hallazgos, severidades, acciones, resultados, herramientas ni noticias.
- Si algo no se puede confirmar, agregalo en limitations.
- No generes DOCX.
- Devuelve SOLO JSON valido, sin markdown ni bloques de codigo.
- Mantén el estilo de Minority Report: ejecutivo, ordenado, con dominios de seguridad y seguimiento semanal.
"""


MINORITY_PLAN_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["report_type", "client_name", "period", "sections", "figures", "tables", "limitations"],
    "properties": {
        "report_type": {"type": "string"},
        "client_name": {"type": "string"},
        "period": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "include", "reason", "data_sources", "expected_content"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "include": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "data_sources": {"type": "array", "items": {"type": "string"}},
                    "expected_content": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "figures": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "description", "place_after_section"],
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "place_after_section": {"type": "string"},
                },
            },
        },
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "source", "columns", "place_after_section"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "source": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "place_after_section": {"type": "string"},
                },
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
}


PLANNER_PROMPT_BASE = """Eres un planner senior XOC para Minority Report.
Tu tarea NO es redactar el reporte final. Tu tarea es planificar la estructura del documento usando la plantilla y la evidencia entregada.

Reglas:
- Devuelve SOLO JSON válido.
- No generes DOCX.
- No redactes párrafos finales.
- No inventes datos.
- Mantén el plan compacto: máximo 13 secciones, 5 tablas y 5 figuras.
- Usa frases cortas. Cada reason y expected_content debe ser breve.
- No copies daily_records completos al plan; solo referencia data_sources.
- Decide qué secciones deben incluirse según la evidencia disponible.
- Si template_rules incluye allowed_top_level_sections, respeta esa lista y no agregues secciones top-level fuera de ella.
- Si una sección ideal no tiene data suficiente, puede marcarse include=false y explicar el motivo.
- Mantén la estructura visual esperada del Minority Report corporativo.
- Indica qué tablas y figuras deben usarse y dónde colocarlas.
- En Seguridad por Dominio planifica tablas con columnas exactas: ID, Vulnerabilidades, Host Afectados, Severidad.
- Para Severidad usa solo BAJO, MEDIO o ALTO.
- Planifica Figura 1 y Figura 2 en 2.1 Análisis Comparativo de Vulnerabilidades Semanales.
- Planifica Figura 3 en 2.2 Histograma de la seguridad.

Secciones ideales del Minority Report completo:
1. Datos generales
  1.1 Servicio de Monitoreo
  1.2 Periodo
  1.3 Herramientas
  1.4 Datos Base
2. Resumen ejecutivo del dominio
  2.1 Análisis Comparativo de Vulnerabilidades Semanales
  2.2 Histograma de la seguridad
  2.3 Resultados obtenidos y próximas acciones
  2.4 Resultados obtenidos
  2.5 Próximas acciones
    2.5.1 Requerimiento
3. Seguridad por Dominio
4. Reporte de acciones trabajadas durante la semana
5. Resultados obtenidos
  5.1 Seguridad Reforzada
  5.2 Hallazgos pendientes
6. Noticias de seguridad

Variantes:
- report for client: solo top-level Datos generales, Resumen ejecutivo del dominio y Seguridad por Dominio.
- report for admin client: incluye además Reporte de acciones trabajadas durante la semana, Resultados obtenidos y Noticias de seguridad.
"""


BUILDER_PROMPT_BASE = """Eres un builder senior XOC para Minority Report.
Tu tarea es generar el JSON final del reporte usando un plan estructural previamente generado y la evidencia entregada.

Reglas:
- Devuelve SOLO JSON válido.
- No generes DOCX.
- Respeta el plan recibido.
- Usa únicamente la evidencia entregada.
- No inventes fechas, IPs, activos, hallazgos, severidades, acciones, resultados, herramientas ni noticias.
- Si falta información para una parte del plan, indícalo en limitations.
- Redacta en tono ejecutivo-técnico, claro y formal.
- Cita las imágenes como Figura 1, Figura 2, etc. cuando correspondan.
- Respeta report_variant/template_rules: no redactes secciones top-level fuera de allowed_top_level_sections.
- Aplica section_instructions/admin_reference sin inventar evidencia.
- En Seguridad por Dominio usa severidad normalizada BAJO, MEDIO o ALTO.
- Figura 1 y Figura 2 pertenecen a 2.1; Figura 3 pertenece a 2.2.
- El resultado debe cumplir exactamente el schema final de Minority Report.
"""


@dataclass(frozen=True)
class MinorityFoundrySettings:
    use_azure_foundry: bool
    project_endpoint: str
    openai_endpoint: str
    model_deployment: str
    api_key: str
    max_output_tokens: int
    use_json_schema: bool


def _load_secret_payload() -> dict[str, Any]:
    secret_id = (os.environ.get("MINORITY_FOUNDRY_SECRET_ARN") or "").strip()
    if not secret_id:
        return {}
    secret_string = get_secret_string(secret_id)
    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _env_or_secret(env_key: str, secret_payload: dict[str, Any], secret_key: str | None = None) -> str:
    value = (os.environ.get(env_key) or "").strip()
    if value:
        return value
    lookup_key = secret_key or env_key
    return str(secret_payload.get(lookup_key) or "").strip()


def get_minority_foundry_settings() -> MinorityFoundrySettings:
    secret_payload = _load_secret_payload()
    use_azure_foundry = (os.environ.get("USE_AZURE_FOUNDRY", "true").strip().lower() not in {"0", "false", "no"})
    return MinorityFoundrySettings(
        use_azure_foundry=use_azure_foundry,
        project_endpoint=_env_or_secret("AZURE_FOUNDRY_PROJECT_ENDPOINT", secret_payload),
        openai_endpoint=_env_or_secret("AZURE_FOUNDRY_OPENAI_ENDPOINT", secret_payload),
        model_deployment=_env_or_secret("AZURE_FOUNDRY_MODEL_DEPLOYMENT", secret_payload) or "gpt-5-mini",
        api_key=_env_or_secret("AZURE_FOUNDRY_API_KEY", secret_payload),
        max_output_tokens=int(os.environ.get("MINORITY_MAX_OUTPUT_TOKENS", "9000")),
        use_json_schema=(os.environ.get("MINORITY_JSON_SCHEMA", "true").strip().lower() not in {"0", "false", "no"}),
    )


def _strip_json_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    return raw.strip()


def _extract_json_object(raw: str) -> str:
    start = raw.find("{")
    if start == -1:
        return raw
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return raw


def _repair_common_json_issues(raw: str) -> str:
    repaired = _extract_json_object(raw)
    repaired = repaired.replace("\ufeff", "").replace("“", '"').replace("”", '"')
    repaired = re.sub(r"//.*?$", "", repaired, flags=re.MULTILINE)
    repaired = re.sub(r"/\*.*?\*/", "", repaired, flags=re.DOTALL)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired.strip()


def _loads_json_with_repair(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as first_exc:
        repaired = _repair_common_json_issues(raw)
        if repaired != raw:
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                raise first_exc
        else:
            raise first_exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Foundry did not return a JSON object")
    return parsed


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value] if value not in (None, "") else []


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    missing = MINORITY_KEYS - set(payload)
    extra = set(payload) - MINORITY_KEYS
    if missing or extra:
        raise RuntimeError(
            "Unexpected Minority Report JSON. "
            f"Missing: {', '.join(sorted(missing)) or 'none'}. "
            f"Extra: {', '.join(sorted(extra)) or 'none'}."
        )

    normalized = {key: payload.get(key) for key in MINORITY_KEYS}
    for key in (
        "title",
        "client_name",
        "prepared_by",
        "period",
        "service_name",
        "data_base",
        "executive_summary",
        "histogram_summary",
        "results_and_next_actions",
        "results_obtained",
        "reinforced_security",
    ):
        normalized[key] = _clean_string(normalized[key])

    normalized["tools"] = [
        {"name": _clean_string(item.get("name")), "description": _clean_string(item.get("description"))}
        for item in _as_list(normalized["tools"])
        if isinstance(item, dict) and (_clean_string(item.get("name")) or _clean_string(item.get("description")))
    ]

    comparison = normalized["vulnerability_comparison"] if isinstance(normalized["vulnerability_comparison"], dict) else {}
    normalized["vulnerability_comparison"] = {
        "summary": _clean_string(comparison.get("summary")),
        "severity_rows": [
            {
                "severity": _clean_string(row.get("severity")),
                "previous": _clean_string(row.get("previous")),
                "current": _clean_string(row.get("current")),
            }
            for row in _as_list(comparison.get("severity_rows"))
            if isinstance(row, dict) and _clean_string(row.get("severity"))
        ],
    }

    for key in ("next_actions", "requirements", "weekly_actions", "pending_findings", "limitations"):
        normalized[key] = [_clean_string(item) for item in _as_list(normalized[key]) if _clean_string(item)]

    domains = []
    for domain in _as_list(normalized["security_domains"]):
        if not isinstance(domain, dict):
            continue
        findings = []
        for finding in _as_list(domain.get("findings")):
            if not isinstance(finding, dict):
                continue
            findings.append(
                {
                    "id": _clean_string(finding.get("id")),
                    "vulnerability": _clean_string(finding.get("vulnerability")),
                    "affected_hosts": _clean_string(finding.get("affected_hosts")),
                    "severity": _clean_string(finding.get("severity")),
                }
            )
        name = _clean_string(domain.get("name"))
        summary = _clean_string(domain.get("summary"))
        if name or summary or findings:
            domains.append({"name": name, "summary": summary, "findings": findings})
    normalized["security_domains"] = domains

    news_items = []
    for news in _as_list(normalized["security_news"]):
        if not isinstance(news, dict):
            continue
        title = _clean_string(news.get("title"))
        if not title:
            continue
        news_items.append(
            {
                "title": title,
                "date": _clean_string(news.get("date")),
                "source": _clean_string(news.get("source")),
                "links": [_clean_string(link) for link in _as_list(news.get("links")) if _clean_string(link)],
                "summary": _clean_string(news.get("summary")),
                "recommendation": _clean_string(news.get("recommendation")),
            }
        )
    normalized["security_news"] = news_items
    normalized["image_citations"] = []
    return normalized


def parse_and_validate_json(raw: str) -> dict[str, Any]:
    clean = _strip_json_fence(raw or "")
    if not clean:
        raise RuntimeError("Foundry returned no visible JSON text")
    try:
        parsed = _loads_json_with_repair(clean)
    except json.JSONDecodeError as exc:
        sample = re.sub(r"\s+", " ", clean[:220]).strip()
        raise RuntimeError(
            "Foundry devolvió una respuesta que no es JSON válido "
            f"({exc.msg}, línea {exc.lineno}, columna {exc.colno}). Inicio seguro: {sample!r}"
        ) from exc
    return _normalize_payload(parsed)


def build_prompt(*, client_name: str, period: str, analyst_text: str, structured_data: dict[str, Any], reference_markdown: str = "") -> str:
    return (
        f"{PROMPT_BASE}\n\n"
        f"Cliente objetivo: {client_name or 'No especificado'}\n"
        f"Periodo objetivo: {period or 'No especificado'}\n\n"
        "Texto del analista:\n"
        f"{analyst_text.strip() or 'No se proporciono texto adicional del analista.'}\n\n"
        "Datos estructurados del tenant:\n"
        f"{json.dumps(structured_data, ensure_ascii=False, indent=2)}\n\n"
        "Referencia de formato Minority Report. Usala solo como guia estructural:\n"
        f"{reference_markdown[:18000] if reference_markdown else 'No se proporciono referencia adicional.'}"
    )


def _resolve_base_url(settings: MinorityFoundrySettings) -> str:
    endpoint = (settings.openai_endpoint or settings.project_endpoint).rstrip("/")
    if not endpoint:
        raise RuntimeError("Azure Foundry endpoint not configured for minority report")
    if endpoint.endswith("/openai/v1"):
        return f"{endpoint}/"
    elif "/api/projects/" in endpoint:
        resource_root = endpoint.split("/api/projects/", 1)[0].rstrip("/")
        return f"{resource_root}/openai/v1/"
    else:
        return f"{endpoint}/openai/v1/"


def _foundry_request(settings: MinorityFoundrySettings, request: dict[str, Any]) -> dict[str, Any]:
    if not settings.api_key:
        raise RuntimeError("Azure Foundry API key is not configured for minority report")
    base_url = _resolve_base_url(settings)
    response = requests.post(
        f"{base_url}responses",
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
        json=request,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def _extract_response_text(response: Any) -> str:
    if isinstance(response, dict):
        output_text = str(response.get("output_text") or "").strip()
    else:
        output_text = str(getattr(response, "output_text", "") or "").strip()
    if output_text:
        return output_text
    pieces: list[str] = []
    output_items = response.get("output", []) if isinstance(response, dict) else (getattr(response, "output", []) or [])
    for item in output_items or []:
        content_items = item.get("content", []) if isinstance(item, dict) else (getattr(item, "content", []) or [])
        for content in content_items:
            if isinstance(content, dict):
                if content.get("type") == "output_text":
                    text_value = content.get("text")
                    if isinstance(text_value, str):
                        pieces.append(text_value)
                    elif isinstance(text_value, dict):
                        value = text_value.get("value")
                        if value:
                            pieces.append(str(value))
                value = content.get("text") or content.get("value")
                if isinstance(value, str):
                    pieces.append(value)
    text = "\n".join(piece.strip() for piece in pieces if piece.strip())
    if text:
        return text
    raise RuntimeError("Foundry responded without visible text")


def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_type": _clean_string(plan.get("report_type")),
        "client_name": _clean_string(plan.get("client_name")),
        "period": _clean_string(plan.get("period")),
        "sections": [
            {
                "id": _clean_string(item.get("id")),
                "title": _clean_string(item.get("title")),
                "include": bool(item.get("include")),
                "reason": _clean_string(item.get("reason")),
                "data_sources": [_clean_string(value) for value in _as_list(item.get("data_sources")) if _clean_string(value)],
                "expected_content": [_clean_string(value) for value in _as_list(item.get("expected_content")) if _clean_string(value)],
            }
            for item in _as_list(plan.get("sections"))
            if isinstance(item, dict) and _clean_string(item.get("title"))
        ],
        "figures": [
            {
                "label": _clean_string(item.get("label")),
                "description": _clean_string(item.get("description")),
                "place_after_section": _clean_string(item.get("place_after_section")),
            }
            for item in _as_list(plan.get("figures"))
            if isinstance(item, dict) and _clean_string(item.get("label"))
        ],
        "tables": [
            {
                "id": _clean_string(item.get("id")),
                "title": _clean_string(item.get("title")),
                "source": _clean_string(item.get("source")),
                "columns": [_clean_string(value) for value in _as_list(item.get("columns")) if _clean_string(value)],
                "place_after_section": _clean_string(item.get("place_after_section")),
            }
            for item in _as_list(plan.get("tables"))
            if isinstance(item, dict) and _clean_string(item.get("title"))
        ],
        "limitations": [_clean_string(value) for value in _as_list(plan.get("limitations")) if _clean_string(value)],
    }


def parse_and_validate_plan_json(raw: str) -> dict[str, Any]:
    clean = _strip_json_fence(raw or "")
    if not clean:
        raise RuntimeError("Foundry Planner returned no visible JSON text")
    try:
        parsed = _loads_json_with_repair(clean)
    except json.JSONDecodeError as exc:
        sample = re.sub(r"\s+", " ", clean[:220]).strip()
        hint = ""
        if exc.msg in {"Unterminated string starting at", "Expecting value"}:
            hint = " Posible respuesta truncada: suba MINORITY_PLANNER_MAX_OUTPUT_TOKENS o reduzca el snapshot enviado al Planner."
        raise RuntimeError(
            "Foundry Planner devolvió una respuesta que no es JSON válido "
            f"({exc.msg}, línea {exc.lineno}, columna {exc.colno}). Inicio seguro: {sample!r}.{hint}"
        ) from exc
    return _normalize_plan(parsed)


def _planner_structured_snapshot(structured_data: dict[str, Any]) -> dict[str, Any]:
    metrics = structured_data.get("aggregated_metrics") if isinstance(structured_data.get("aggregated_metrics"), dict) else {}
    return {
        "source": structured_data.get("source"),
        "client_name": structured_data.get("client_name") or structured_data.get("tenant_name"),
        "period": structured_data.get("period"),
        "document_code": structured_data.get("document_code"),
        "report_variant": structured_data.get("report_variant"),
        "template_variant": structured_data.get("template_variant"),
        "admin_reference": structured_data.get("admin_reference"),
        "section_instructions": structured_data.get("section_instructions"),
        "template_rules": structured_data.get("template_rules"),
        "tools": structured_data.get("tools"),
        "severity_summary": structured_data.get("severity_summary"),
        "previous_severity_summary": structured_data.get("previous_severity_summary"),
        "aggregated_metrics": {
            "data_base": metrics.get("data_base"),
            "vulnerability_comparison": metrics.get("vulnerability_comparison"),
            "histogram_summary": metrics.get("histogram_summary"),
            "results_obtained": metrics.get("results_obtained"),
            "pending_findings_count": len(_as_list(metrics.get("pending_findings"))),
            "security_domains_count": len(_as_list(metrics.get("security_domains"))),
            "weekly_actions_count": len(_as_list(metrics.get("weekly_actions"))),
        },
        "chart_evidence": structured_data.get("chart_evidence"),
        "scan_snapshot": structured_data.get("scan_snapshot"),
    }


def build_planner_prompt(*, client_name: str, period: str, analyst_text: str, structured_data: dict[str, Any], reference_markdown: str = "") -> str:
    structured = _planner_structured_snapshot(structured_data or {})
    return (
        f"{PLANNER_PROMPT_BASE}\n\n"
        f"Cliente objetivo: {client_name or 'No especificado'}\n"
        f"Periodo objetivo: {period or 'No especificado'}\n\n"
        "Instruccion del backend/analista:\n"
        f"{analyst_text.strip() or 'No se proporciono instruccion adicional.'}\n\n"
        "Snapshot estructurado de BD/evidencia:\n"
        f"{json.dumps(structured, ensure_ascii=False, indent=2)}\n\n"
        "Referencia de formato Minority Report. Usala solo para planificar estructura/estilo; no copies datos del cliente ejemplo:\n"
        f"{reference_markdown[:12000] if reference_markdown else 'No se proporciono referencia adicional.'}"
    )


def build_builder_prompt(
    *,
    client_name: str,
    period: str,
    analyst_text: str,
    structured_data: dict[str, Any],
    plan: dict[str, Any],
    reference_markdown: str = "",
) -> str:
    return (
        f"{BUILDER_PROMPT_BASE}\n\n"
        f"Cliente objetivo: {client_name or 'No especificado'}\n"
        f"Periodo objetivo: {period or 'No especificado'}\n\n"
        "Plan estructural aprobado por Planner:\n"
        f"{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
        "Instruccion del backend/analista:\n"
        f"{analyst_text.strip() or 'No se proporciono instruccion adicional.'}\n\n"
        "Snapshot estructurado completo de BD/evidencia:\n"
        f"{json.dumps(structured_data or {}, ensure_ascii=False, indent=2)}\n\n"
        "Referencia de formato Minority Report. Usala solo como guia de tono/estructura; no copies datos del cliente ejemplo:\n"
        f"{reference_markdown[:12000] if reference_markdown else 'No se proporciono referencia adicional.'}"
    )


def _azure_json_response(settings: MinorityFoundrySettings, *, prompt: str, schema: dict[str, Any], schema_name: str, max_output_tokens: int) -> str:
    request: dict[str, Any] = {
        "model": settings.model_deployment,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "max_output_tokens": max_output_tokens,
    }
    if settings.use_json_schema:
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": True,
            }
        }
    try:
        response = _foundry_request(settings, request)
    except Exception:
        if "text" not in request:
            raise
        request.pop("text", None)
        response = _foundry_request(settings, request)
    return _extract_response_text(response)


def generate_minority_payload(*, client_name: str, period: str, analyst_text: str, structured_data: dict[str, Any], reference_markdown: str = "") -> dict[str, Any]:
    settings = get_minority_foundry_settings()
    if not settings.use_azure_foundry:
        raise RuntimeError("Minority report generation requires Azure Foundry and mock fallback is disabled")

    prompt = build_prompt(
        client_name=client_name,
        period=period,
        analyst_text=analyst_text,
        structured_data=structured_data,
        reference_markdown=reference_markdown,
    )
    request: dict[str, Any] = {
        "model": settings.model_deployment,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "max_output_tokens": settings.max_output_tokens,
    }
    if settings.use_json_schema:
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": "xoc_minority_report_payload",
                "schema": MINORITY_JSON_SCHEMA,
                "strict": True,
            }
        }
    try:
        response = _foundry_request(settings, request)
    except Exception:
        if "text" not in request:
            raise
        request.pop("text", None)
        response = _foundry_request(settings, request)
    return parse_and_validate_json(_extract_response_text(response))


def generate_minority_payload_planner_builder(
    *,
    client_name: str,
    period: str,
    analyst_text: str,
    structured_data: dict[str, Any],
    reference_markdown: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    settings = get_minority_foundry_settings()
    if not settings.use_azure_foundry:
        raise RuntimeError("Minority report generation requires Azure Foundry and mock fallback is disabled")

    planner_prompt = build_planner_prompt(
        client_name=client_name,
        period=period,
        analyst_text=analyst_text,
        structured_data=structured_data,
        reference_markdown=reference_markdown,
    )
    raw_plan = _azure_json_response(
        settings,
        prompt=planner_prompt,
        schema=MINORITY_PLAN_JSON_SCHEMA,
        schema_name="xoc_minority_report_plan",
        max_output_tokens=int(os.environ.get("MINORITY_PLANNER_MAX_OUTPUT_TOKENS", "4000")),
    )
    plan = parse_and_validate_plan_json(raw_plan)

    builder_prompt = build_builder_prompt(
        client_name=client_name,
        period=period,
        analyst_text=analyst_text,
        structured_data=structured_data,
        reference_markdown=reference_markdown,
        plan=plan,
    )
    raw_payload = _azure_json_response(
        settings,
        prompt=builder_prompt,
        schema=MINORITY_JSON_SCHEMA,
        schema_name="xoc_minority_report_payload",
        max_output_tokens=settings.max_output_tokens,
    )
    return parse_and_validate_json(raw_payload), plan
