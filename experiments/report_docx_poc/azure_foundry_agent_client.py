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

load_dotenv()

EXPECTED_AI_KEYS = [
    "executive_summary",
    "security_posture_narrative",
    "severity_analysis",
    "cases_summary",
    "sla_summary_narrative",
    "mitre_summary",
    "top_assets_summary",
    "technical_evidence_summary",
    "operational_timeline_summary",
    "automation_recommendations_summary",
    "next_actions_summary",
    "analyst_notes",
    "risk_conclusion",
]


def _validate_sections_payload(sections: dict[str, Any], source: str) -> dict[str, str]:
    if sorted(sections.keys()) != sorted(EXPECTED_AI_KEYS):
        raise RuntimeError(f"{source} devolvio un JSON con claves inesperadas")
    return {key: str(value) for key, value in sections.items()}


def build_compact_report_payload(report_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant": report_data.get("tenant", {}),
        "report": report_data.get("report", {}),
        "severity_summary": report_data.get("severity_summary", {}),
        "severity_delta": report_data.get("severity_delta", {}),
        "security_posture": report_data.get("security_posture", {}),
        "cases": report_data.get("cases", [])[:5],
        "sla_summary": report_data.get("sla_summary", {}),
        "mitre_mapping": report_data.get("mitre_mapping", [])[:5],
        "top_assets": report_data.get("top_assets", [])[:5],
        "domain_scores": report_data.get("domain_scores", []),
        "findings": [item for item in report_data.get("findings", []) if item.get("severity") in {"Critico", "Alto"}][:5],
        "technical_evidence": report_data.get("technical_evidence", [])[:5],
        "operational_timeline": report_data.get("operational_timeline", [])[:5],
        "automation_suggestions": report_data.get("automation_suggestions", [])[:5],
        "next_actions": report_data.get("next_actions", [])[:5],
        "results": report_data.get("results", {}),
    }


def build_agent_prompt(compact_payload: dict[str, Any]) -> str:
    return (
        "Eres un analista senior de ciberseguridad y operaciones XOC.\n"
        "Debes generar secciones narrativas para un informe semanal de seguridad, disponibilidad, performance y experiencia.\n\n"
        "Usa unicamente los datos estructurados proporcionados.\n"
        "No inventes activos, numeros, severidades, fechas, herramientas ni acciones.\n"
        "No prometas remediaciones no ejecutadas.\n"
        "Si falta informacion, indicalo en analyst_notes.\n"
        "Devuelve SOLO JSON valido.\n"
        "No uses markdown.\n"
        "No uses bloques ```json.\n"
        "No agregues texto antes ni despues del JSON.\n\n"
        "El JSON debe tener exactamente estas claves:\n"
        "executive_summary,\n"
        "security_posture_narrative,\n"
        "severity_analysis,\n"
        "cases_summary,\n"
        "sla_summary_narrative,\n"
        "mitre_summary,\n"
        "top_assets_summary,\n"
        "technical_evidence_summary,\n"
        "operational_timeline_summary,\n"
        "automation_recommendations_summary,\n"
        "next_actions_summary,\n"
        "analyst_notes,\n"
        "risk_conclusion.\n\n"
        "Reglas de redaccion:\n"
        "- Espanol profesional.\n"
        "- Tono ejecutivo/tecnico.\n"
        "- Conciso.\n"
        "- Priorizar hallazgos criticos y altos.\n"
        "- Mencionar tendencia semanal si existe.\n"
        "- Mencionar riesgos principales.\n"
        "- Mencionar proximas acciones.\n"
        "- No inventar informacion.\n"
        "- No usar listas excesivas.\n\n"
        f"Datos del informe:\n{json.dumps(compact_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def generate_local_fallback_sections(report_data: dict[str, Any]) -> dict[str, str]:
    posture = report_data.get("security_posture", {})
    sla = report_data.get("sla_summary", {})
    kpis = report_data.get("kpis", {})
    return {
        "executive_summary": f"Durante el periodo {report_data['report']['period']} se consolido una postura de riesgo {posture.get('risk_level', 'medio').lower()} con foco en hallazgos criticos y altos sobre la superficie priorizada.",
        "security_posture_narrative": f"El score global se ubica en {posture.get('global_score', 'N/D')}/100 con tendencia {posture.get('trend', 'estable').lower()} y {posture.get('critical_open', 0)} casos criticos abiertos.",
        "severity_analysis": report_data.get("severity_delta", {}).get("narrative", "No se cuenta con comparativo semanal suficiente."),
        "cases_summary": f"Se gestionaron {len(report_data.get('cases', []))} casos, con {kpis.get('Casos abiertos', 0)} abiertos y {kpis.get('Casos resueltos', 0)} resueltos.",
        "sla_summary_narrative": f"El estado SLA registra {sla.get('within_sla', 0)} casos dentro de plazo, {sla.get('at_risk', 0)} en riesgo y {sla.get('breached', 0)} vencidos.",
        "mitre_summary": "El mapeo MITRE concentra exposiciones sobre acceso inicial, credenciales, persistencia y debilitamiento de controles.",
        "top_assets_summary": "Los activos con mayor riesgo se concentran en infraestructura base, dominios expuestos y componentes de red/OT priorizados.",
        "technical_evidence_summary": "La evidencia tecnica confirma criticidades abiertas y sustenta la priorizacion ejecutiva del corte semanal.",
        "operational_timeline_summary": "La linea de tiempo semanal muestra una secuencia consistente de deteccion, validacion, seguimiento y consolidacion del reporte.",
        "automation_recommendations_summary": "Las automatizaciones sugeridas se orientan a deteccion continua, hardening recurrente y reduccion del MTTR.",
        "next_actions_summary": "Las proximas acciones priorizan remediacion de criticidades abiertas, reduccion de exposicion y normalizacion operativa.",
        "analyst_notes": "Si se requiere mayor profundidad, se recomienda ampliar trazabilidad historica, evidencia por activo y backlog de remediacion por owner.",
        "risk_conclusion": "El riesgo residual permanece elevado mientras convivan hallazgos criticos abiertos, deuda criptografica y activos fuera de soporte.",
    }


def _extract_message_text(message: Any) -> str:
    if hasattr(message, "text_messages") and message.text_messages:
        return "\n".join(item.text.value for item in message.text_messages if hasattr(item, "text") and hasattr(item.text, "value"))
    content = getattr(message, "content", None) or []
    values: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text and hasattr(text, "value"):
            values.append(text.value)
    return "\n".join(values)


def _generate_sections_via_foundry(report_data: dict[str, Any]) -> dict[str, str]:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ["AZURE_FOUNDRY_PROJECT_ENDPOINT"].strip()
    agent_name = os.environ.get("AZURE_FOUNDRY_AGENT_NAME", "Matias").strip()
    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    agents = client.agents
    agent = agents.get(agent_name) if hasattr(agents, "get") else agents.get_agent(agent_name)

    thread = agents.threads.create()
    prompt = build_agent_prompt(build_compact_report_payload(report_data))
    agents.messages.create(thread_id=thread.id, role="user", content=prompt)
    run = agents.runs.create_and_process(thread_id=thread.id, agent_id=agent.id)
    if getattr(run, "status", "").lower() == "failed":
        raise RuntimeError(f"Azure Foundry run failed: {getattr(run, 'last_error', None)}")

    messages = list(agents.messages.list(thread_id=thread.id))
    assistant_messages = [message for message in messages if getattr(message, "role", "") == "assistant"]
    if not assistant_messages:
        raise RuntimeError("Azure Foundry no devolvio mensajes del agente")

    raw_text = _extract_message_text(assistant_messages[-1]).strip()
    sections = json.loads(raw_text)
    return _validate_sections_payload(sections, "Azure Foundry")


def _generate_sections_via_azure_openai(report_data: dict[str, Any]) -> dict[str, str]:
    from openai import OpenAI

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].strip().rstrip("/")
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"].strip()
    api_key = os.environ["AZURE_OPENAI_API_KEY"].strip()
    max_output_tokens = int(os.environ.get("AZURE_FOUNDRY_MAX_OUTPUT_TOKENS", "2500").strip())
    client = OpenAI(base_url=f"{endpoint}/openai/v1", api_key=api_key)
    prompt = build_agent_prompt(build_compact_report_payload(report_data))
    response = None
    for attempt_tokens in (max_output_tokens, max(max_output_tokens * 2, 4000)):
        response = client.responses.create(
            model=deployment,
            input=prompt,
            max_output_tokens=attempt_tokens,
            reasoning={"effort": "low"},
        )

        raw_text = getattr(response, "output_text", "").strip()
        if not raw_text:
            continue

        try:
            sections = json.loads(raw_text)
            return _validate_sections_payload(sections, "Azure OpenAI")
        except json.JSONDecodeError:
            if getattr(response, "status", None) != "incomplete":
                raise

    raise RuntimeError(
        f"Azure OpenAI no devolvio un JSON utilizable. status={getattr(response, 'status', None)} incomplete_details={getattr(response, 'incomplete_details', None)}"
    )


def generate_ai_sections(report_data: dict[str, Any], output_path: Path) -> tuple[dict[str, str], str]:
    use_azure = os.environ.get("USE_AZURE_FOUNDRY_AGENT", "false").strip().lower() == "true"
    if use_azure:
        try:
            if os.environ.get("AZURE_OPENAI_API_KEY", "").strip():
                sections = _generate_sections_via_azure_openai(report_data)
                source = "azure_openai"
            else:
                sections = _generate_sections_via_foundry(report_data)
                source = "azure"
            output_path.write_text(json.dumps(sections, indent=2, ensure_ascii=False), encoding="utf-8")
            return sections, source
        except Exception:
            fallback = generate_local_fallback_sections(report_data)
            output_path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
            return fallback, "fallback"

    fallback = generate_local_fallback_sections(report_data)
    output_path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
    return fallback, "fallback"
