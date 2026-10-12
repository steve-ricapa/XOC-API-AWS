"""IAM/SigV4 MCP client for the single configured AgentCore gateway.

No MCP subprocess, SDK agent, dynamic URL, retry, redirect, or fallback. Gateway
sessions/streaming are not enabled: one stateless tools/call with a bounded JSON
response. Model input is nested under arguments, never merged into authority.
"""
import json
import logging
import math
import os
import re
import time

import boto3
import requests
from urllib3.util import Timeout
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

from src.shared import agentcore_context as trusted
from src.tool_gateway.audit import log_tool_decision
from src.tool_gateway.policy import evaluate_tool_request
from src.tool_gateway.registry import get_tool_definition
from src.tool_gateway.schemas import ToolDecision, ToolResult

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
MAX_RESPONSE = 131072
PROTOCOL = "2026-07-28"  # AWS-documented stateless revision; no initialize/session


def settings():
    arn = os.environ["SOPHIA_AGENTCORE_GATEWAY_ARN"]
    match = re.fullmatch(r"arn:aws:bedrock-agentcore:([a-z]+-[a-z]+-\d):([0-9]{12}):gateway/([a-z0-9-]+)", arn)
    if not match:
        raise ValueError("invalid_configuration")
    region, account, gateway = match.groups()
    endpoint = f"https://{gateway}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"
    if os.environ["SOPHIA_AGENTCORE_GATEWAY_URL"] != endpoint:
        raise ValueError("invalid_configuration")
    key = os.environ["SOPHIA_AGENTCORE_CONTEXT_KEY_ARN"]
    if not re.fullmatch(rf"arn:aws:kms:{re.escape(region)}:{account}:key/[a-f0-9-]{{36}}", key):
        raise ValueError("invalid_configuration")
    return arn, endpoint, region, key


class AgentCoreExecution:
    def execute(self, context, request, *, deadline):
        started = time.monotonic()
        code = "agentcore_unavailable"
        try:
            if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
                raise ValueError("invalid_deadline")
            # Leave budget for SOPHIA's second turn inside Chat's existing 18 s.
            end = min(deadline - 2.0, started + 5.0)
            if end - started < 1.0:
                raise TimeoutError()
            decision = evaluate_tool_request(context, request)
            if not decision.allowed:
                audit_id = log_tool_decision(context=context, request=request, decision=decision,
                    definition=get_tool_definition(request.tool_name))
                code = decision.code
                return ToolResult(ToolDecision.DENIED, code=code, audit_id=audit_id)
            trusted.validate_context(context)
            if request.tool_name not in trusted.READ_TOOLS:
                raise ValueError("read_only_required")
            arn, endpoint, region, key = settings()
            session = boto3.Session(region_name=region)
            config = Config(connect_timeout=0.4, read_timeout=0.6, retries={"total_max_attempts": 1})
            kms = session.client("kms", config=config)
            assertion = trusted.issue(kms, key, arn, context, request, end)
            payload = {"jsonrpc": "2.0", "id": context.request_id, "method": "tools/call",
                       "params": {"name": trusted.TARGET_NAME + "___" + trusted.READ_TOOLS[request.tool_name],
                                  "arguments": {"assertion": assertion, "arguments": dict(request.arguments)},
                                  "_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL,
                                            "io.modelcontextprotocol/clientInfo": {"name": "xoc-chat", "version": "ac1"},
                                            "io.modelcontextprotocol/clientCapabilities": {}}}}
            data = trusted.canonical(payload)
            credentials = session.get_credentials().get_frozen_credentials()
            signed = AWSRequest(method="POST", url=endpoint, data=data, headers={
                "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL, "Mcp-Method": "tools/call",
                "Mcp-Name": payload["params"]["name"]})
            SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(signed)
            remaining = end - time.monotonic()
            if remaining < 0.5:
                raise TimeoutError()
            with requests.Session() as http:
                http.trust_env = False
                with http.post(endpoint, data=data, headers=dict(signed.headers), allow_redirects=False,
                               stream=True, timeout=Timeout(total=remaining, connect=min(0.5, remaining / 4))) as response:
                    if (response.status_code != 200
                            or response.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"
                            or response.headers.get("Mcp-Session-Id")):
                        raise ValueError("invalid_gateway_response")
                    body = bytearray()
                    # Check the absolute deadline even on a trickling response;
                    # waiting for a full large chunk would only enforce idle time.
                    for chunk in response.iter_content(1):
                        if time.monotonic() >= end or len(body) + len(chunk) > MAX_RESPONSE:
                            raise TimeoutError()
                        body.extend(chunk)
            if time.monotonic() >= end:
                raise TimeoutError()
            result = decode_result(json.loads(body), context, request)
            code = result.code
            return result
        except Exception:
            # No exception body, signed assertion, headers, arguments or raw rows.
            return ToolResult(ToolDecision.DENIED, code=code)
        finally:
            logger.info("sophia_agentcore transport=agentcore code=%s duration_ms=%d",
                        code, int((time.monotonic() - started) * 1000))


def decode_result(wire, context, request):
    if (type(wire) is not dict or set(wire) != {"jsonrpc", "id", "result"}
            or wire["jsonrpc"] != "2.0" or wire["id"] != context.request_id):
        raise ValueError("invalid_result")
    result = wire["result"]
    if (type(result) is not dict or result.get("isError", False) is not False
            or result.get("resultType") not in (None, "complete")
            or "inputRequests" in result or "requestState" in result):
        raise ValueError("invalid_result")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
        raise ValueError("invalid_result")
    value = json.loads(content[0]["text"])
    if "structuredContent" in result and result["structuredContent"] != value:
        raise ValueError("invalid_result")
    if (type(value) is not dict or set(value) != {"request_id", "tool_name", "status", "code", "data", "audit_id"}
            or value["request_id"] != context.request_id or value["tool_name"] != request.tool_name
            or value["status"] not in {"allowed", "denied"}
            or value["code"] not in {"allowed", "denied", "unavailable"}
            or not isinstance(value["audit_id"], str) or not re.fullmatch(r"[a-f0-9]{24}", value["audit_id"])):
        raise ValueError("invalid_result")
    if value["status"] == "allowed":
        if value["code"] != "allowed" or type(value["data"]) is not dict:
            raise ValueError("invalid_result")
    elif value["data"] is not None:
        raise ValueError("invalid_result")
    return ToolResult(ToolDecision(value["status"]), data=value["data"], code=value["code"], audit_id=value["audit_id"])
