import asyncio
from dataclasses import FrozenInstanceError, replace
import json
import time
from unittest.mock import patch
import jwt
import pytest
from xoc_mcp_lab.request_envelope import verify_envelope, EnvelopeError, canonical
from xoc_mcp_lab.query_worker import QueryWorker
from l1_fixtures import envelope


@pytest.mark.parametrize("changes", [
    {"iss": "runtime"}, {"aud": "voice"}, {"aud": ["xoc-sophia-query-worker"]},
    {"purpose": "voice"}, {"source": "voice"}, {"iat": True}, {"exp": "123"},
    {"deadline": False}, {"actor_user_id": True}, {"actor_user_id": "92001"},
    {"tenant_id": True}, {"tenant_id": 1}, {"effective_tenant_id": "91001"},
    {"delegation_active": "true"}, {"actor_role": {"role": "ADMIN"}},
    {"extra": "DO_NOT_LOG_THIS"}, {"tool_hash": "0" * 64}, {"request_id": "lab-other"},
    {"iat": int(time.time()) + 100}, {"exp": int(time.time()) - 100},
    {"deadline": int(time.time()) - 100}, {"exp": int(time.time()) + 1000},
    {"deadline": int(time.time()) + 1000},
])
def test_invalid_claims_zero_child_before_backend_resolution(changes):
    value = envelope(claims_patch=changes)
    with patch("xoc_mcp_lab.query_worker.stdio.stdio_client") as launch, \
            patch("xoc_mcp_lab.query_worker.backend_path") as path:
        result = asyncio.run(QueryWorker().run(value, "MUST_NOT_BE_READ"))
        assert result == {"status": "error", "code": "lab_invalid_envelope"}
        launch.assert_not_called()
        path.assert_not_called()
        assert "DO_NOT_LOG_THIS" not in json.dumps(result)


@pytest.mark.parametrize("headers", [{"kid": "voice"}, {"kid": "runtime"},
    {"jku": "https://fictional.invalid/key"}, {"jwk": {}}, {"crit": ["x"]}, {"typ": "OTHER"}])
def test_no_dynamic_key_or_algorithm_selection(headers):
    with pytest.raises(EnvelopeError):
        verify_envelope(envelope(headers_patch=headers))


@pytest.mark.parametrize("scalar", [2, 3])
def test_other_components_cannot_sign_fixture_chat_context(scalar):
    with pytest.raises(EnvelopeError):
        verify_envelope(envelope(scalar=scalar))


@pytest.mark.parametrize("algorithm", ["HS256", "none"])
def test_no_hs_or_unsigned_fallback(algorithm):
    value = envelope()
    claims = jwt.decode(value["context_assertion"], options={"verify_signature": False})
    value["context_assertion"] = jwt.encode(claims, "LAB_FAKE_SHARED_KEY" if algorithm == "HS256" else "",
        algorithm=algorithm, headers={"kid": "LAB-ONLY-PUBLIC-TEST-KEY-1"})
    with pytest.raises(EnvelopeError):
        verify_envelope(value)


@pytest.mark.parametrize("mutation", ["request_id", "arguments", "tool", "schema", "extra", "signature", "context"])
def test_tampered_envelope(mutation):
    value = envelope()
    if mutation == "request_id": value["request_id"] = "lab-replay-other-request"
    if mutation == "arguments": value["tool"]["arguments"] = {"limit": 2}
    if mutation == "tool": value["tool"]["name"] = "xoc.documents.list"
    if mutation == "schema": value["schema_version"] = "prod"
    if mutation == "extra": value["tenant_id"] = 91002
    if mutation == "signature": value["context_assertion"] = value["context_assertion"][:-20] + "A" * 20
    if mutation == "context":
        h, p, s = value["context_assertion"].split(".")
        claims = jwt.decode(value["context_assertion"], options={"verify_signature": False})
        claims["effective_tenant_id"] = 91002
        value["context_assertion"] = h + "." + jwt.utils.base64url_encode(canonical(claims).encode()).decode() + "." + s
    with pytest.raises(EnvelopeError): verify_envelope(value)


def test_context_immutable_and_detached_from_envelope(wired):
    value = envelope(arguments={"limit": 1})
    verified = verify_envelope(value)
    with pytest.raises(FrozenInstanceError): verified.role = "SUPERADMIN"
    value["tool"]["arguments"]["limit"] = 200
    from xoc_mcp_lab.adapter import Adapter
    adapter = Adapter.from_verified(verified, wired[0])
    with pytest.raises(FrozenInstanceError): adapter.context.user_id = 92002
    result = adapter.call("xoc.tickets.list", {"limit": 1}).structuredContent
    assert result["request_id"] == verified.request_id == adapter.events[0]["request_id"]
    assert result["audit_id"] == adapter.events[0]["audit_id"]
    assert result["status"] == "allowed" and adapter.handler_calls == 1
    assert adapter.call("xoc.tickets.list", {"limit": 1}).isError
    assert adapter.handler_calls == 1


@pytest.mark.parametrize("profile,allowed", [("admin_tenant_a", True), ("user_a1", True),
    ("admin_xoc_delegated_a", True), ("admin_xoc_without_delegation", False), ("superadmin", False)])
def test_verified_identity_still_uses_original_policy(wired, profile, allowed):
    from xoc_mcp_lab.adapter import Adapter
    w = wired[0]
    adapter = Adapter.from_verified(verify_envelope(envelope(profile)), w)
    result = adapter.call("xoc.tickets.list", {})
    assert result.isError != allowed
    assert adapter.handler_calls == int(allowed)
    if not allowed: assert w.reads == 0


@pytest.mark.parametrize("name,args", [("xoc.evil.execute", {}), ("xoc.dashboard.summary", {}),
    ("xoc.integrations.summary", {}), ("xoc.tickets.create", {}),
    *[("xoc.tickets.list", {field: 91002}) for field in ("tenantId", "tenant_id", "userId", "user_id", "role")]])
def test_verified_requests_cannot_bypass_registry_or_ownership(wired, name, args):
    from xoc_mcp_lab.adapter import Adapter
    adapter = Adapter.from_verified(verify_envelope(envelope(name=name, arguments=args)), wired[0])
    assert adapter.call(name, args).isError
    assert adapter.handler_calls == wired[0].reads == 0


def test_mismatched_first_call_consumes_single_attempt(wired):
    from xoc_mcp_lab.adapter import Adapter
    adapter = Adapter.from_verified(verify_envelope(envelope()), wired[0])
    assert adapter.call("xoc.documents.list", {}).structuredContent["code"] == "lab_request_mismatch"
    assert adapter.call("xoc.tickets.list", {}).structuredContent["code"] == "lab_call_limit"
    assert adapter.handler_calls == wired[0].reads == 0


def test_deadline_checked_again_at_execution(wired):
    from xoc_mcp_lab.adapter import Adapter
    verified = verify_envelope(envelope())
    adapter = Adapter.from_verified(verified, wired[0])
    with patch("time.time", return_value=verified.deadline + 1):
        assert adapter.call("xoc.tickets.list", {}).isError
    assert adapter.handler_calls == wired[0].reads == 0


def test_duplicate_json_huge_envelope_and_fake_verified_rejected(wired):
    value = canonical(envelope())
    with pytest.raises(EnvelopeError): verify_envelope('{"request_id":"lab-other",' + value[1:])
    with pytest.raises(EnvelopeError): verify_envelope(" " * 16385)
    from xoc_mcp_lab.adapter import Adapter
    verified = replace(verify_envelope(envelope()), _seal=None)
    with pytest.raises(EnvelopeError): Adapter.from_verified(verified, wired[0])
    with pytest.raises(ValueError): Adapter.from_verified({}, wired[0])


@pytest.mark.parametrize("field", ["tenant_id", "effective_tenant_id", "actor_user_id", "delegation_active", "request_id"])
def test_missing_claims_are_not_nullable_claims(field):
    from cryptography.hazmat.primitives.asymmetric import ec
    from xoc_mcp_lab.request_envelope import KID
    value = envelope()
    claims = jwt.decode(value["context_assertion"], options={"verify_signature": False})
    del claims[field]
    value["context_assertion"] = jwt.encode(claims, ec.derive_private_key(1, ec.SECP256R1()),
                                            algorithm="ES256", headers={"kid": KID})
    with pytest.raises(EnvelopeError): verify_envelope(value)


def test_compatibility_manifest_matches_reviewed_sources(backend):
    import hashlib
    import importlib.metadata
    from xoc_mcp_lab.isolation import LAB_ROOT
    manifest = json.loads((LAB_ROOT / "compatibility.json").read_text(encoding="utf-8"))
    assert manifest["fixture_only"] and not manifest["production_ready"]
    assert importlib.metadata.version("mcp") == manifest["mcp"]["sdk"]
    assert hashlib.sha256((LAB_ROOT / "requirements.lock.txt").read_bytes()).hexdigest() == manifest["requirements_lock_sha256"]
    for relative, expected in manifest["backend"]["files"].items():
        assert hashlib.sha256((backend / relative).read_bytes()).hexdigest() == expected
