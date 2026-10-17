"""L4 pure HTTP boundary tests; no live server, foreign checkout or provider imports."""
import asyncio
import base64
from dataclasses import replace
import json
import time

import pytest

from xoc_mcp_lab.request_envelope import canonical
from xoc_mcp_lab_harness.signing import envelope
from xoc_mcp_lab_l4 import boundary as b


PORT = 18844


def encoded(value):
    return base64.urlsafe_b64encode(canonical(value).encode()).rstrip(b"=")


def scope(value=None, **updates):
    result = dict(type="http", path="/mcp", query_string=b"", method="POST", headers=[
        (b"host", f"127.0.0.1:{PORT}".encode()), (b"content-type", b"application/json"),
        (b"accept", b"application/json, text/event-stream"),
        (b"mcp-protocol-version", b.PROTOCOL_VERSION.encode()),
        (b.ENVELOPE_HEADER, encoded(envelope() if value is None else value))])
    result.update(updates)
    return result


def replace_header(request, key, value):
    request["headers"] = [(k, v) for k, v in request["headers"] if k != key]
    if value is not None:
        request["headers"].append((key, value))
    return request


def test_each_header_verifies_distinct_immutable_l1_context():
    value = envelope()
    headers = b.checked_headers(scope(value), PORT)
    a, another = b.verified_header(headers), b.verified_header(headers)
    assert a is not another and a == another
    assert a.matches(value["tool"]["name"], {})
    assert a.tenant_id == 91001


@pytest.mark.parametrize("updates,status", [
    ({"method": "GET"}, 405), ({"method": "DELETE"}, 405),
    ({"path": "/mcp/"}, 404), ({"query_string": b"profile=admin_tenant_b"}, 404)])
def test_exact_endpoint_and_post_only(updates, status):
    with pytest.raises(b.BoundaryError) as error:
        b.checked_headers(scope(**updates), PORT)
    assert error.value.status == status


@pytest.mark.parametrize("key,value,status", [
    (b"host", b"localhost:18844", 421), (b"host", b"127.0.0.1:18845", 421),
    (b"host", b"127.0.0.1:18844.evil", 421), (b"origin", b"http://evil.invalid", 403),
    (b"origin", b"null", 403), (b"content-type", b"text/plain", 415),
    (b"content-length", b"65537", 413), (b"content-length", b"-1", 400),
    (b"content-length", b"nope", 400)])
def test_transport_headers_are_not_identity_or_wildcard_channels(key, value, status):
    with pytest.raises(b.BoundaryError) as error:
        b.checked_headers(replace_header(scope(), key, value), PORT)
    assert error.value.status == status


def test_origin_absent_or_exact_only_and_duplicate_headers_rejected():
    b.checked_headers(scope(), PORT)
    b.checked_headers(replace_header(scope(), b"origin", b"http://127.0.0.1:18844"), PORT)
    for key in (b"host", b.ENVELOPE_HEADER, b"mcp-protocol-version"):
        request = scope()
        request["headers"].append((key, b"duplicate"))
        with pytest.raises(b.BoundaryError, match="LAB_DUPLICATE_HEADER"):
            b.checked_headers(request, PORT)


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_host_origin_validation_precedes_unsupported_method_rejection(method):
    request = replace_header(scope(method=method), b"content-type", None)
    with pytest.raises(b.BoundaryError) as rejected:
        b.checked_headers(request, PORT)
    assert rejected.value.status == 405
    request = replace_header(request, b"origin", b"http://evil.invalid")
    with pytest.raises(b.BoundaryError) as rejected:
        b.checked_headers(request, PORT)
    assert rejected.value.status == 403
    request = replace_header(request, b"host", b"evil.invalid")
    with pytest.raises(b.BoundaryError) as rejected:
        b.checked_headers(request, PORT)
    assert rejected.value.status == 421


@pytest.mark.parametrize("value", [b"", b"not.base64", b"a", b"e30=", b"_" * 21847])
def test_header_encoding_is_canonical_and_bounded(value):
    with pytest.raises(b.BoundaryError, match="LAB_INVALID_ENVELOPE"):
        b.verified_header({b.ENVELOPE_HEADER: value})


def test_unsigned_wrong_key_expired_and_noncanonical_rejected_without_echo():
    invalid = [envelope(scalar=2), envelope(headers_patch={"kid": "other"}),
               envelope(claims_patch={"exp": int(time.time()) - 1})]
    for value in invalid:
        with pytest.raises(b.BoundaryError) as caught:
            b.verified_header({b.ENVELOPE_HEADER: encoded(value)})
        assert str(caught.value) == "LAB_INVALID_ENVELOPE"
        assert value["context_assertion"] not in str(caught.value)
    pretty = base64.urlsafe_b64encode(json.dumps(envelope(), indent=2).encode()).rstrip(b"=")
    with pytest.raises(b.BoundaryError):
        b.verified_header({b.ENVELOPE_HEADER: pretty})


def test_envelope_and_inner_token_limits():
    value = envelope()
    value["context_assertion"] = "x" * 4097
    with pytest.raises(b.BoundaryError):
        b.verified_header({b.ENVELOPE_HEADER: encoded(value)})
    with pytest.raises(b.BoundaryError):
        b.verified_header({b.ENVELOPE_HEADER: base64.urlsafe_b64encode(b" " * 16385).rstrip(b"=")})


@pytest.mark.parametrize("target", [16383, 16384, 16385])
def test_exact_envelope_size_boundaries_with_real_valid_signatures(target):
    small = envelope(arguments={"padding": ""})
    value = envelope(arguments={"padding": "x" * (target - len(canonical(small).encode()))})
    assert len(canonical(value).encode()) == target
    if target <= b.ENVELOPE_LIMIT:
        actual = b.verified_header({b.ENVELOPE_HEADER: encoded(value)})
        assert actual.matches(value["tool"]["name"], value["tool"]["arguments"])
    else:
        with pytest.raises(b.BoundaryError, match="LAB_INVALID_ENVELOPE"):
            b.verified_header({b.ENVELOPE_HEADER: encoded(value)})


@pytest.mark.parametrize("target", [4095, 4096, 4097])
def test_jws_size_boundary_never_weakens_signature_verification(target):
    # Exact-size synthetic assertions remain invalid below/at/above the bound.
    # Do not mock crypto or invent valid large claims merely to exercise a limit.
    value = envelope()
    value["context_assertion"] = "x" * target
    assert len(value["context_assertion"]) == target
    with pytest.raises(b.BoundaryError, match="LAB_INVALID_ENVELOPE"):
        b.verified_header({b.ENVELOPE_HEADER: encoded(value)})


def message(method="tools/call", params=None):
    return dict(jsonrpc="2.0", id=7, method=method,
                params={"name": "xoc.tickets.list", "arguments": {}} if params is None else params)


def verified():
    headers = b.checked_headers(scope(), PORT)
    return headers, b.verified_header(headers)


def test_bound_actual_name_arguments_not_metadata():
    headers, context = verified()
    good = message()
    assert b.checked_message(b.json_bytes(good), headers, context) == good
    for params in ({"name": "xoc.documents.list", "arguments": {}},
                   {"name": "xoc.tickets.list", "arguments": {"limit": 1}},
                   {"name": "xoc.tickets.list", "arguments": None},
                   {"name": "xoc.tickets.list", "arguments": {}, "_meta": {"tenant_id": 91002}},
                   {"name": "xoc.tickets.list", "arguments": {}, "task": {}}):
        with pytest.raises(b.BoundaryError):
            b.checked_message(b.json_bytes(message(params=params)), headers, context)


def test_protocol_pin_and_notification_shape():
    headers, context = verified()
    initialize = message("initialize", {"protocolVersion": b.PROTOCOL_VERSION,
        "capabilities": {}, "clientInfo": {"name": "lab", "version": "1"}})
    b.checked_message(b.json_bytes(initialize), {}, context)
    initialize["params"]["protocolVersion"] = "2025-03-26"
    with pytest.raises(b.BoundaryError):
        b.checked_message(b.json_bytes(initialize), headers, context)
    with pytest.raises(b.BoundaryError):
        b.checked_message(b.json_bytes(message()), {}, context)
    cancel = dict(jsonrpc="2.0", method="notifications/cancelled", params={"requestId": 7})
    b.checked_message(b.json_bytes(cancel), headers, context)
    cancel["id"] = 7
    with pytest.raises(b.BoundaryError):
        b.checked_message(b.json_bytes(cancel), headers, context)


def test_envelope_expiring_during_receive_stays_closed_with_401():
    headers, context = verified()
    expired = replace(context, deadline=int(time.time()) - 1)
    with pytest.raises(b.BoundaryError) as error:
        b.checked_message(b.json_bytes(message()), headers, expired)
    assert error.value.status == 401


@pytest.mark.parametrize("body", [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}',
                                  b'\xff', b'{', b'[' * 1500])
def test_strict_json_parser_rejects_ambiguity_and_malformed_bytes(body):
    with pytest.raises(b.BoundaryError, match="LAB_INVALID_JSON"):
        b.parse_json(body)


def receive_chunks(chunks):
    iterator = iter(chunks)

    async def receive():
        return next(iterator)
    return receive


def test_fragmented_utf8_body_counts_bytes_and_exact_limit():
    async def run():
        chunks = [{"type": "http.request", "body": b"a" * 32768, "more_body": True},
                  {"type": "http.request", "body": b"b" * 32768, "more_body": False}]
        result = await b.read_body(receive_chunks(chunks), {}, time.time() + 10)
        assert len(result) == b.INPUT_LIMIT
        chunks[1]["body"] += b"c"
        with pytest.raises(b.BoundaryError) as error:
            await b.read_body(receive_chunks(chunks), {}, time.time() + 10)
        assert error.value.status == 413
        utf8 = "á".encode()
        assert await b.read_body(receive_chunks([
            {"type": "http.request", "body": utf8[:1], "more_body": True},
            {"type": "http.request", "body": utf8[1:], "more_body": False}]), {}, time.time() + 10) == utf8
    asyncio.run(run())


def test_receive_timeout_disconnect_and_content_length(monkeypatch):
    monkeypatch.setattr(b, "RECEIVE_TIMEOUT_SECONDS", .01)

    async def run():
        async def stalled():
            await asyncio.sleep(1)
        with pytest.raises(b.BoundaryError) as error:
            await b.read_body(stalled, {}, time.time() + 10)
        assert error.value.status == 408
        with pytest.raises(b.BoundaryError, match="LAB_REQUEST_DISCONNECTED"):
            await b.read_body(receive_chunks([{"type": "http.disconnect"}]), {}, time.time() + 10)
        with pytest.raises(b.BoundaryError, match="LAB_INVALID_CONTENT_LENGTH"):
            await b.read_body(receive_chunks([{"type": "http.request", "body": b"a"}]),
                              {b"content-length": b"2"}, time.time() + 10)
    asyncio.run(run())


def bounded_output(body, fragments=False, status=200):
    async def run():
        sent = []

        async def send(item):
            sent.append(item)
        response = b.BoundedResponse(send, request_id=7)
        await response({"type": "http.response.start", "status": status,
                        "headers": [(b"mcp-session-id", b"must-not-escape")]})
        if fragments:
            await response({"type": "http.response.body", "body": body[:100], "more_body": True})
            assert not sent
            await response({"type": "http.response.body", "body": body[100:]})
        else:
            await response({"type": "http.response.body", "body": body})
        return sent, response
    return asyncio.run(run())


def test_exact_output_limit_and_oversize_are_complete_json():
    prefix = b.json_bytes({"jsonrpc": "2.0", "id": 7, "result": ""})
    body = b.json_bytes({"jsonrpc": "2.0", "id": 7, "result": "x" * (b.OUTPUT_LIMIT - len(prefix))})
    assert len(body) == b.OUTPUT_LIMIT
    sent, response = bounded_output(body, fragments=True)
    assert sent[1]["body"] == body and response.wire_bytes == b.OUTPUT_LIMIT
    sent, response = bounded_output(body + b" ", fragments=True)
    assert len(sent[1]["body"]) < 256
    assert json.loads(sent[1]["body"])["error"]["message"] == "LAB_OUTPUT_LIMIT"
    assert all(key != b"mcp-session-id" for key, _ in sent[0]["headers"])
    assert response.guard_ns > 0


@pytest.mark.parametrize("target", [b.OUTPUT_LIMIT - 1, b.OUTPUT_LIMIT, b.OUTPUT_LIMIT + 1])
@pytest.mark.parametrize("encoding", ["ascii", "raw_unicode", "escaped_unicode"])
def test_output_minus_at_plus_one_counts_wire_bytes_unicode_and_escapes(target, encoding):
    initial = "text" if encoding == "ascii" else 'á🙂\\"line\n'
    ensure_ascii = encoding != "raw_unicode"
    payload = {"jsonrpc": "2.0", "id": 7, "result": initial}
    small = json.dumps(payload, ensure_ascii=ensure_ascii, separators=(",", ":")).encode("utf-8")
    payload["result"] += "x" * (target - len(small))
    body = json.dumps(payload, ensure_ascii=ensure_ascii, separators=(",", ":")).encode("utf-8")
    assert len(body) == target
    sent, response = bounded_output(body, fragments=True)
    actual = sent[1]["body"]
    if target <= b.OUTPUT_LIMIT:
        assert actual == body and response.wire_bytes == target
        assert json.loads(actual)["result"].startswith(initial)
    else:
        assert len(actual) < 256
        assert json.loads(actual)["error"]["message"] == "LAB_OUTPUT_LIMIT"


def test_sdk_errors_and_invalid_responses_never_reflect_submitted_data():
    raw = b.json_bytes({"jsonrpc": "2.0", "id": 7,
                        "error": {"code": -32602, "message": "LAB_CANARY_NEVER_PUBLIC", "data": "token"}})
    sent, _ = bounded_output(raw)
    assert b"LAB_CANARY_NEVER_PUBLIC" not in sent[1]["body"]
    assert b"token" not in sent[1]["body"]
    assert json.loads(sent[1]["body"])["error"]["message"] == "LAB_PROTOCOL_ERROR"
    sent, _ = bounded_output(b"<p>secret SDK detail</p>", status=400)
    assert sent[0]["status"] == 500 and b"secret" not in sent[1]["body"]
    sent, _ = bounded_output(b"", status=202)
    assert sent[0]["status"] == 202 and sent[1]["body"] == b""


def test_nonreading_peer_cannot_hold_error_response(monkeypatch):
    from xoc_mcp_lab_l4 import server
    monkeypatch.setattr(server, "ERROR_SEND_TIMEOUT_SECONDS", .01)

    async def run():
        async def stalled_send(_):
            await asyncio.sleep(1)
        started = time.monotonic()
        await server._bounded_error_response(stalled_send, b.BoundaryError())
        assert time.monotonic() - started < .25
    asyncio.run(run())


def test_error_delivery_never_extends_remaining_total_budget(monkeypatch):
    from xoc_mcp_lab_l4 import server
    budgets = []
    actual_move_on_after = server.anyio.move_on_after

    def observed_move_on_after(seconds):
        budgets.append(seconds)
        return actual_move_on_after(seconds)

    monkeypatch.setattr(server.anyio, "move_on_after", observed_move_on_after)

    async def run():
        sends = []

        async def stalled_send(message):
            sends.append(message)
            await asyncio.sleep(1)

        for remaining in (0.0, -0.01):
            await server._bounded_error_response(stalled_send, b.BoundaryError(), remaining)
        assert sends == [] and budgets == []
        await server._bounded_error_response(stalled_send, b.BoundaryError(), 0.005)
        assert budgets == [0.005]
        await server._bounded_error_response(stalled_send, b.BoundaryError(), 1.0)
        assert budgets == [0.005, server.ERROR_SEND_TIMEOUT_SECONDS]
    asyncio.run(run())


def test_installed_actual_http_exact_input_boundaries_and_fragmented_unicode():
    from xoc_mcp_lab.installed import is_installed
    if not is_installed():
        pytest.skip("Actual HTTP is tested in installed Linux artifacts, no checkout backend")
    import httpx
    from xoc_mcp_lab_l4.client import headers_for
    from xoc_mcp_lab_l4.harness import ServerProcess, make_envelope

    async def run():
        server = ServerProcess()
        try:
            await server.wait_ready()
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=3) as client:
                for target in (b.INPUT_LIMIT - 1, b.INPUT_LIMIT, b.INPUT_LIMIT + 1):
                    request = dict(jsonrpc="2.0", id=7, method="initialize", params={
                        "protocolVersion": b.PROTOCOL_VERSION, "capabilities": {},
                        "clientInfo": {"name": "á", "version": "1"}})
                    small = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    request["params"]["clientInfo"]["name"] += "x" * (target - len(small))
                    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    assert len(body) == target

                    async def chunks():
                        # Split the actual UTF-8 code point across HTTP data frames.
                        split = body.index("á".encode("utf-8")) + 1
                        yield body[:split]
                        await asyncio.sleep(0)
                        yield body[split:32768]
                        await asyncio.sleep(0)
                        yield body[32768:]

                    headers = headers_for(make_envelope())
                    headers.update({"Content-Type": "application/json", "Accept": "application/json"})
                    response = await client.post(server.url, headers=headers, content=chunks())
                    assert response.status_code == (413 if target > b.INPUT_LIMIT else 200)
                    assert "mcp-session-id" not in response.headers
                    parsed = response.json()
                    if target <= b.INPUT_LIMIT:
                        assert parsed["result"]["protocolVersion"] == b.PROTOCOL_VERSION
                    else:
                        assert parsed["error"]["message"] == "LAB_INPUT_LIMIT"
                serialized_events = json.dumps(server.events())
                assert "context_assertion" not in serialized_events
                assert "eyJ" not in serialized_events
        finally:
            assert server.close()["process_absent"]
    asyncio.run(run())
