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
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return raw
    return raw[start : end + 1]


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
            parsed = json.loads(repaired)
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
    parsed = _loads_json_with_repair(clean)
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
