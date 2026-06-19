from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.models import (
    AgentApiKey,
    Integration,
    IntegrationCapabilityTemplate,
    IntegrationCapabilityTemplateAssignment,
)


def _extract_capabilities(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        for key in ("capabilities", "actions", "items"):
            items = value.get(key)
            if isinstance(items, list):
                return [str(item).strip() for item in items if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def collect_automation_capabilities(session: Session, company_id: int) -> list[str]:
    capabilities: list[str] = []

    integrations = session.execute(select(Integration).where(Integration.company_id == company_id)).scalars().all()
    agent_keys = session.execute(select(AgentApiKey).where(AgentApiKey.company_id == company_id, AgentApiKey.is_active == True)).scalars().all()

    if not integrations and not agent_keys:
        return []

    providers: set[str] = set()
    for integration in integrations:
        if integration.provider:
            providers.add(str(integration.provider).strip().lower())
    for agent_key in agent_keys:
        if agent_key.integration_type:
            providers.add(str(agent_key.integration_type).strip().lower())

    templates = []
    if providers:
        templates = session.execute(
            select(IntegrationCapabilityTemplate).where(
                IntegrationCapabilityTemplate.provider.in_(providers),
                IntegrationCapabilityTemplate.is_active == True,
            )
        ).scalars().all()

    templates_by_provider = {template.provider: template for template in templates}

    template_ids = [template.id for template in templates]
    assignments = []
    if template_ids:
        assignments = session.execute(
            select(IntegrationCapabilityTemplateAssignment).where(
                IntegrationCapabilityTemplateAssignment.template_id.in_(template_ids)
            )
        ).scalars().all()

    assignments_by_template: dict[int, set[int]] = {}
    for assignment in assignments:
        assignments_by_template.setdefault(assignment.template_id, set()).add(assignment.company_id)

    def apply_template(provider: str):
        template = templates_by_provider.get(provider)
        if not template or template.capabilities is None:
            return
        assigned_companies = assignments_by_template.get(template.id)
        if assigned_companies and company_id not in assigned_companies:
            return
        capabilities.extend(_extract_capabilities(template.capabilities))

    handled_providers: set[str] = set()
    for integration in integrations:
        provider = str(integration.provider).strip().lower() if integration.provider else None
        if not provider:
            continue
        handled_providers.add(provider)
        if integration.capabilities is not None:
            capabilities.extend(_extract_capabilities(integration.capabilities))
            continue
        apply_template(provider)

    for provider in providers:
        if provider in handled_providers:
            continue
        apply_template(provider)

    seen = set()
    unique = []
    for item in capabilities:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique
