"""AC-1: short lived, KMS-authenticated authority; never a user JWT.

GenerateMac and VerifyMac are separate IAM permissions. Neither component reads
key material. The configured key ARN and audience are never taken from a token.
"""
from dataclasses import asdict
import base64
import hashlib
import json
import math
import re
import time

from src.tool_gateway.schemas import ToolContext, ToolRequest

VERSION = "xoc-agentcore-context-v1"
MAX_TTL_MS = 5000
READ_TOOLS = {
    "xoc.tickets.list": "tickets-list",
    "xoc.tickets.get": "tickets-get",
    "xoc.documents.list": "documents-list",
    "xoc.notifications.unread_count": "notifications-unread-count",
}
TARGET_NAME = "xoc-read"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode()


def _decode(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid_assertion")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def validate_context(context):
    for value in (context.effective_tenant_id, context.user_id):
        if type(value) is not int or value <= 0:
            raise ValueError("invalid_context")
    if context.tenant_id is not None and (type(context.tenant_id) is not int or context.tenant_id <= 0):
        raise ValueError("invalid_context")
    if context.role not in {"USER", "ADMIN", "ADMIN_XOC"} or type(context.delegation_active) is not bool:
        raise ValueError("invalid_context")
    if context.role == "ADMIN_XOC":
        if not context.delegation_active:
            raise ValueError("invalid_context")
    elif context.delegation_active or context.tenant_id != context.effective_tenant_id:
        raise ValueError("invalid_context")
    if (context.source != "sophia_chat" or not isinstance(context.request_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", context.request_id)):
        raise ValueError("invalid_context")


def check_time(claims, now=None):
    now = int(time.time() * 1000) if now is None else now
    start, end = claims["iat_ms"], claims["deadline_ms"]
    if (type(start) is not int or type(end) is not int or not start <= now < end
            or not 0 < end - start <= MAX_TTL_MS):
        raise ValueError("expired_context")


def issue(kms, key_arn, audience, context, request, deadline):
    validate_context(context)
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining < 0.5:
        raise ValueError("expired_context")
    if request.request_id != context.request_id or request.tool_name not in READ_TOOLS:
        raise ValueError("invalid_request")
    if type(request.arguments) is not dict or len(canonical(request.arguments)) > 8192:
        raise ValueError("invalid_arguments")
    now = int(time.time() * 1000)
    claims = {"version": VERSION, "aud": audience, "context": asdict(context),
              "tool": request.tool_name, "arguments_sha256": hashlib.sha256(canonical(request.arguments)).hexdigest(),
              "iat_ms": now, "deadline_ms": now + min(MAX_TTL_MS, int(remaining * 1000))}
    message = canonical(claims)
    response = kms.generate_mac(KeyId=key_arn, Message=message, MacAlgorithm="HMAC_SHA_256")
    check_time(claims)
    return _encode(message) + "." + _encode(response["Mac"])


def verify(kms, key_arn, audience, assertion, tool, arguments):
    if not isinstance(assertion, str) or len(assertion) > 4096 or assertion.count(".") != 1:
        raise ValueError("invalid_assertion")
    message, mac = map(_decode, assertion.split("."))
    if len(mac) != 32 or len(message) > 3072:
        raise ValueError("invalid_assertion")
    claims = json.loads(message)
    if (type(claims) is not dict or canonical(claims) != message or set(claims) != {
            "version", "aud", "context", "tool", "arguments_sha256", "iat_ms", "deadline_ms"}
            or claims["version"] != VERSION or claims["aud"] != audience
            or claims["tool"] != tool or tool not in READ_TOOLS
            or type(arguments) is not dict or len(canonical(arguments)) > 8192
            or claims["arguments_sha256"] != hashlib.sha256(canonical(arguments)).hexdigest()):
        raise ValueError("invalid_assertion")
    check_time(claims)
    verified = kms.verify_mac(KeyId=key_arn, Message=message, MacAlgorithm="HMAC_SHA_256", Mac=mac)
    if verified.get("MacValid") is not True:
        raise ValueError("invalid_assertion")
    check_time(claims)  # verification latency cannot extend the signed deadline
    context = ToolContext(**claims["context"])
    if set(claims["context"]) != set(asdict(context)):
        raise ValueError("invalid_context")
    validate_context(context)
    return context, ToolRequest(tool, arguments, context.request_id), claims["deadline_ms"]
