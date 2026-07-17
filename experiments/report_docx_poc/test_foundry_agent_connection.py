from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local dependency during bootstrap
    def load_dotenv() -> bool:
        return False


load_dotenv()


def extract_message_text(message) -> str:
    if hasattr(message, "text_messages") and message.text_messages:
        return "\n".join(item.text.value for item in message.text_messages if hasattr(item, "text") and hasattr(item.text, "value"))
    content = getattr(message, "content", None) or []
    values: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text and hasattr(text, "value"):
            values.append(text.value)
    return "\n".join(values)


def _test_via_azure_openai() -> None:
    from openai import OpenAI

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].strip().rstrip("/")
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"].strip()
    api_key = os.environ["AZURE_OPENAI_API_KEY"].strip()

    client = OpenAI(base_url=f"{endpoint}/openai/v1", api_key=api_key)
    response = client.responses.create(
        model=deployment,
        input='Devuelve solo JSON valido: {"status":"ok","message":"Azure OpenAI conectado"}',
        max_output_tokens=120,
        reasoning={"effort": "low"},
    )

    output_text = response.output_text.strip()
    if not output_text:
        raise RuntimeError(f"Azure OpenAI no devolvio texto. status={getattr(response, 'status', None)} incomplete_details={getattr(response, 'incomplete_details', None)}")
    print(output_text)


def _test_via_foundry_agent() -> None:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ["AZURE_FOUNDRY_PROJECT_ENDPOINT"].strip()
    agent_name = os.environ.get("AZURE_FOUNDRY_AGENT_NAME", "Matias").strip()
    agent_version = os.environ.get("AZURE_FOUNDRY_AGENT_VERSION", "1").strip()

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    agents = client.agents
    agent = agents.get(agent_name) if hasattr(agents, "get") else agents.get_agent(agent_name)

    thread = agents.threads.create()
    prompt = 'Devuelve solo JSON valido: {"status":"ok","message":"Azure Foundry Agent conectado"}'
    agents.messages.create(thread_id=thread.id, role="user", content=prompt)
    run = agents.runs.create_and_process(thread_id=thread.id, agent_id=agent.id)
    if getattr(run, "status", "").lower() == "failed":
        raise RuntimeError(f"Fallo la ejecucion del agente {agent_name} v{agent_version}: {getattr(run, 'last_error', None)}")

    messages = list(agents.messages.list(thread_id=thread.id))
    assistant_messages = [message for message in messages if getattr(message, "role", "") == "assistant"]
    if not assistant_messages:
        raise RuntimeError(f"El agente {agent_name} v{agent_version} no devolvio respuesta")
    print(extract_message_text(assistant_messages[-1]).strip())


def main() -> None:
    if os.environ.get("AZURE_OPENAI_API_KEY", "").strip():
        _test_via_azure_openai()
        return

    _test_via_foundry_agent()


if __name__ == "__main__":
    main()
