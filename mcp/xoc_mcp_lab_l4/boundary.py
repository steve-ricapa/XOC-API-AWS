"""LAB_ONLY HTTP framing and signed-context boundary. Never authentication for production."""
from __future__ import annotations

import base64
import binascii
import json
import re
import time

import anyio

from xoc_mcp_lab.request_envelope import canonical, verify_envelope

PROTOCOL_VERSION = "2025-11-25"
ENVELOPE_HEADER = b"x-xoc-lab-envelope"
INPUT_LIMIT = 65536
OUTPUT_LIMIT = 262144
ENVELOPE_LIMIT = 16384
HEADER_LIMIT = 32768
ENCODED_ENVELOPE_LIMIT = (ENVELOPE_LIMIT * 4 + 2) // 3
RECEIVE_TIMEOUT_SECONDS = 1.0


class BoundaryError(ValueError):
    def __init__(self, message="LAB_INVALID_HTTP_REQUEST", status=400, code=-32600):
        self.message, self.status, self.code = message, status, code
        super().__init__(message)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def error_bytes(message, request_id=None, code=-32600):
    return json_bytes({"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": code, "message": message}})


async def error_response(send, error, request_id=None):
    body = error_bytes(error.message, request_id, error.code)
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
               (b"cache-control", b"no-store")]
    if error.status == 405:
        headers.append((b"allow", b"POST"))
    await send({"type": "http.response.start", "status": error.status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BoundaryError("LAB_INVALID_JSON")
        result[key] = value
    return result


def _invalid_constant(_):
    raise BoundaryError("LAB_INVALID_JSON")


def parse_json(body):
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise BoundaryError("LAB_INVALID_JSON", code=-32700) from None


def checked_headers(scope, port):
    if scope.get("path") != "/mcp" or scope.get("query_string", b""):
        raise BoundaryError("LAB_INVALID_ENDPOINT", 404)
    pairs = scope.get("headers", [])
    if sum(len(key) + len(value) + 4 for key, value in pairs) > HEADER_LIMIT:
        raise BoundaryError("LAB_HEADERS_TOO_LARGE", 431)
    sensitive = {b"host", b"origin", ENVELOPE_HEADER, b"content-type", b"content-length",
                 b"transfer-encoding", b"mcp-protocol-version"}
    headers = {}
    for key, value in pairs:
        key = key.lower()
        if key in sensitive and key in headers:
            raise BoundaryError("LAB_DUPLICATE_HEADER")
        headers[key] = value
    authority = f"127.0.0.1:{port}".encode("ascii")
    if headers.get(b"host") != authority:
        raise BoundaryError("LAB_INVALID_HOST", 421)
    if b"origin" in headers and headers[b"origin"] != b"http://" + authority:
        raise BoundaryError("LAB_INVALID_ORIGIN", 403)
    if scope.get("method") != "POST":
        raise BoundaryError("LAB_METHOD_NOT_ALLOWED", 405)
    if headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != b"application/json":
        raise BoundaryError("LAB_INVALID_CONTENT_TYPE", 415)
    if b"content-length" in headers:
        value = headers[b"content-length"]
        if not re.fullmatch(rb"[0-9]{1,10}", value):
            raise BoundaryError("LAB_INVALID_CONTENT_LENGTH")
        if int(value) > INPUT_LIMIT:
            raise BoundaryError("LAB_INPUT_LIMIT", 413)
        if b"transfer-encoding" in headers:
            raise BoundaryError("LAB_AMBIGUOUS_FRAMING")
    return headers


def verified_header(headers):
    encoded = headers.get(ENVELOPE_HEADER, b"")
    if not encoded or len(encoded) > ENCODED_ENVELOPE_LIMIT or not re.fullmatch(rb"[A-Za-z0-9_-]+", encoded):
        raise BoundaryError("LAB_INVALID_ENVELOPE", 401)
    try:
        decoded = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        if len(decoded) > ENVELOPE_LIMIT or base64.urlsafe_b64encode(decoded).rstrip(b"=") != encoded:
            raise ValueError()
        text = decoded.decode("utf-8")
        envelope = parse_json(decoded)
        if canonical(envelope) != text:
            raise ValueError()
        # This is the unchanged L1 verifier, including the pinned public fixture key.
        return verify_envelope(text)
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise BoundaryError("LAB_INVALID_ENVELOPE", 401) from None


async def read_body(receive, headers, deadline):
    chunks = bytearray()
    remaining = min(RECEIVE_TIMEOUT_SECONDS, deadline - time.time())
    if remaining <= 0:
        raise BoundaryError("LAB_INVALID_ENVELOPE", 401)
    try:
        with anyio.fail_after(remaining):
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    raise BoundaryError("LAB_REQUEST_DISCONNECTED")
                chunk = message.get("body", b"")
                if len(chunks) + len(chunk) > INPUT_LIMIT:
                    raise BoundaryError("LAB_INPUT_LIMIT", 413)
                chunks.extend(chunk)
                if not message.get("more_body", False):
                    break
    except TimeoutError:
        raise BoundaryError("LAB_RECEIVE_TIMEOUT", 408) from None
    if b"content-length" in headers and len(chunks) != int(headers[b"content-length"]):
        raise BoundaryError("LAB_INVALID_CONTENT_LENGTH")
    return bytes(chunks)


def checked_message(body, headers, verified):
    try:
        verified.require_valid()
    except ValueError:
        raise BoundaryError("LAB_INVALID_ENVELOPE", 401) from None
    message = parse_json(body)
    if type(message) is not dict or set(message) - {"jsonrpc", "id", "method", "params"}:
        raise BoundaryError("LAB_INVALID_PROTOCOL_INPUT")
    if message.get("jsonrpc") != "2.0" or type(message.get("method")) is not str:
        raise BoundaryError("LAB_INVALID_PROTOCOL_INPUT")
    method = message["method"]
    notification = method in {"notifications/initialized", "notifications/cancelled"}
    if notification:
        if "id" in message:
            raise BoundaryError("LAB_INVALID_PROTOCOL_INPUT")
    elif "id" not in message or type(message["id"]) not in (int, str) or len(str(message["id"])) > 128:
        raise BoundaryError("LAB_INVALID_REQUEST_ID")
    params = message.get("params", {})
    if type(params) is not dict:
        raise BoundaryError("LAB_INVALID_PARAMETERS", code=-32602)
    # LAB does not accept identity/context/fault channels in protocol metadata.
    if "_meta" in params:
        raise BoundaryError("LAB_UNSUPPORTED_METADATA", code=-32602)
    if method == "initialize":
        if params.get("protocolVersion") != PROTOCOL_VERSION:
            raise BoundaryError("LAB_PROTOCOL_VERSION_REQUIRED")
    elif headers.get(b"mcp-protocol-version") != PROTOCOL_VERSION.encode("ascii"):
        raise BoundaryError("LAB_PROTOCOL_VERSION_REQUIRED")
    if method == "tools/call":
        if set(params) - {"name", "arguments"} or type(params.get("name")) is not str:
            raise BoundaryError("LAB_INVALID_PARAMETERS", code=-32602)
        arguments = params.get("arguments", {})
        if type(arguments) is not dict:
            raise BoundaryError("LAB_INVALID_PARAMETERS", code=-32602)
        try:
            matches = verified.matches(params["name"], arguments)
        except ValueError:
            raise BoundaryError("LAB_INVALID_ENVELOPE", 401) from None
        if not matches:
            raise BoundaryError("LAB_REQUEST_MISMATCH", 403, -32602)
    elif method in {"tools/list", "ping", "notifications/initialized"}:
        if params:
            raise BoundaryError("LAB_INVALID_PARAMETERS", code=-32602)
    elif method == "notifications/cancelled":
        if set(params) - {"requestId", "reason"} or type(params.get("requestId")) not in (int, str):
            raise BoundaryError("LAB_INVALID_PARAMETERS", code=-32602)
    elif method != "initialize":
        raise BoundaryError("LAB_METHOD_NOT_FOUND", 404, -32601)
    return message


class BoundedResponse:
    """Buffer one complete JSON response; never truncate or disclose SDK error details."""
    def __init__(self, send, request_id=None):
        self.send, self.request_id = send, request_id
        self.start = None
        self.body = bytearray()
        self.overflow = False
        self.sent = False
        self.wire_bytes = 0
        self.status = None
        self.guard_ns = 0

    async def __call__(self, message):
        started = time.perf_counter_ns()
        if message["type"] == "http.response.start":
            self.start = message
            self.guard_ns += time.perf_counter_ns() - started
            return
        if message["type"] != "http.response.body":
            raise BoundaryError("LAB_INVALID_RESPONSE", 500, -32603)
        chunk = message.get("body", b"")
        if not self.overflow and len(self.body) + len(chunk) <= OUTPUT_LIMIT:
            self.body.extend(chunk)
        else:
            self.body.clear()
            self.overflow = True
        if message.get("more_body", False):
            self.guard_ns += time.perf_counter_ns() - started
            return
        status = self.start["status"] if self.start else 500
        body = bytes(self.body)
        if self.overflow:
            body = error_bytes("LAB_OUTPUT_LIMIT", self.request_id, -32001)
            status = 200
        elif body:
            try:
                decoded = parse_json(body)
            except BoundaryError:
                decoded = None
            if not isinstance(decoded, dict):
                body = error_bytes("LAB_INVALID_RESPONSE", self.request_id, -32603)
                status = 500
            elif "error" in decoded:
                code = decoded["error"].get("code", -32603) if isinstance(decoded["error"], dict) else -32603
                allowed = {"LAB_CAPACITY_BUSY", "LAB_REQUEST_TIMEOUT", "LAB_INTERNAL_ERROR",
                           "LAB_INVALID_REQUEST_CONTEXT", "LAB_REQUEST_MISMATCH"}
                error = decoded["error"] if isinstance(decoded["error"], dict) else {}
                safe_message = error.get("message") if error.get("message") in allowed else "LAB_PROTOCOL_ERROR"
                body = error_bytes(safe_message, self.request_id, code if type(code) is int else -32603)
        elif status != 202:
            body = error_bytes("LAB_INVALID_RESPONSE", self.request_id, -32603)
            status = 500
        headers = [(b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]
        if body:
            headers.append((b"content-type", b"application/json"))
        self.status, self.wire_bytes = status, len(body)
        self.sent = True
        self.guard_ns += time.perf_counter_ns() - started
        await self.send({"type": "http.response.start", "status": status, "headers": headers})
        await self.send({"type": "http.response.body", "body": body})
