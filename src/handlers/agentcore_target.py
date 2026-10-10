"""Private Lambda target. No HTTP route, function URL or model authority."""
import logging
import os
import time

import boto3
from botocore.config import Config

from src.shared import agentcore_context as trusted
from src.shared.sophia_tool_chat import model_result
from src.tool_gateway.agentcore_providers import ReadOnlyProviders
from src.tool_gateway.executor import ToolExecutor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Private Lambda uses stdlib logging, not the HTTP app's logging bootstrap.
logging.getLogger("src.tool_gateway.audit").setLevel(logging.INFO)


def handler(event, context):
    started = time.monotonic()
    code = "unavailable"
    try:
        if type(event) is not dict or set(event) != {"assertion", "arguments"}:
            raise ValueError("invalid_envelope")
        metadata = context.client_context.custom
        audience = os.environ["SOPHIA_AGENTCORE_GATEWAY_ARN"]
        if (metadata.get("bedrockAgentCoreGatewayId") != audience.rsplit("/", 1)[-1]
                or metadata.get("bedrockAgentCoreMessageVersion") != "1.0"):
            raise ValueError("invalid_gateway")
        wire_name = metadata.get("bedrockAgentCoreToolName")
        names = {trusted.TARGET_NAME + "___" + name: tool for tool, name in trusted.READ_TOOLS.items()}
        tool = names[wire_name]
        kms = boto3.client("kms", config=Config(connect_timeout=0.2, read_timeout=0.4,
                                               retries={"total_max_attempts": 1}))
        identity, request, deadline = trusted.verify(kms, os.environ["SOPHIA_AGENTCORE_CONTEXT_KEY_ARN"],
            audience, event["assertion"], tool, event["arguments"])
        # A verified identity is still subject to the SAME registry/policy/executor.
        deadline = min(deadline, int(time.time() * 1000 + context.get_remaining_time_in_millis() - 100))
        providers = ReadOnlyProviders(deadline)
        providers.check()
        result = ToolExecutor(handlers=providers.handlers()).execute(identity, request)
        providers.check()
        output = model_result(result, tool, identity)
        code = output["code"]
        return output
    except Exception:
        # Authentication failures must not echo unverified request fields or data.
        return {"status": "denied", "code": "unavailable"}
    finally:
        logger.info("sophia_agentcore_target code=%s duration_ms=%d", code, int((time.monotonic() - started) * 1000))
