"""Exact fixture-output acceptance oracle; never imports or executes the gateway.

Historical normal cases use the preserved C1/L4 reference. The one missing
normal B-owned get and the explicit large fixture have finite expected data.
This is test evidence, not Policy, projection code, or a permission cache.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path


_TICKET_FIELDS = ("ticket_id", "subject", "status", "severity", "created_at", "updated_at")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@lru_cache(maxsize=1)
def _reference_cases(path):
    # A single immutable laboratory expectation document, never client results.
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["normalized_fields"] == ["request_id", "audit_id"], "LAB_FIXTURE_REFERENCE_SCHEMA"
    return tuple(data["cases"])


def _assert_exact(result, expected):
    assert isinstance(result, dict), "LAB_FIXTURE_RESULT_OBJECT_REQUIRED"
    payload = result.get("result")
    assert isinstance(payload, dict), "LAB_FIXTURE_PAYLOAD_REQUIRED"
    assert type(payload.get("request_id")) is str and payload["request_id"].startswith("lab-"), "LAB_FIXTURE_REQUEST_ID"
    assert type(payload.get("audit_id")) is str and payload["audit_id"], "LAB_FIXTURE_AUDIT_ID"
    if "request_id" in result:
        assert result["request_id"] == payload["request_id"], "LAB_FIXTURE_REQUEST_BINDING"
    normalized = {key: value for key, value in payload.items() if key not in ("request_id", "audit_id")}
    assert _canonical(normalized) == _canonical(expected), "LAB_FIXTURE_EXACT_RESULT_MISMATCH"
    expected_status = "error" if expected["code"] in {"execution_failed", "unsafe_tool_result", "unavailable"} else expected["status"]
    assert result.get("status") == expected_status, "LAB_FIXTURE_CLIENT_OUTCOME_MISMATCH"
    if "code" in result:
        assert result["code"] == expected["code"], "LAB_FIXTURE_CLIENT_CODE_MISMATCH"
    if "is_error" in result:
        assert result["is_error"] is (expected["status"] != "allowed"), "LAB_FIXTURE_MCP_ERROR_FLAG"
    assert "LAB_CANARY_NEVER_PUBLIC" not in _canonical(payload), "LAB_FIXTURE_CANARY_EXPOSED"
    return True


def validate_fixture_result(result, profile, name, arguments=None, *, reference_path="/offline/reference.json"):
    """Exact normalized output for a known normal fixture request.

    Unknown expectations fail; there is no 'same tenant somewhere' fallback.
    Dynamic request/audit IDs are validated separately, not mistaken for data.
    """
    if arguments is None:
        arguments = {"ticket_id": "lab-ticket-b1" if profile == "admin_tenant_b" else "lab-ticket-a1"} if name == "xoc.tickets.get" else {}
    found = [case for case in _reference_cases(str(reference_path))
             if case["profile"] == profile and case["name"] == name
             and _canonical(case["arguments"]) == _canonical(arguments)]
    if found:
        assert len(found) == 1, "LAB_FIXTURE_DUPLICATE_EXPECTATION"
        expected = found[0]["result"]
    elif profile == "admin_tenant_b" and name == "xoc.tickets.get" and arguments == {"ticket_id": "lab-ticket-b1"}:
        from xoc_mcp_lab.fixtures import dataset
        source = next(row for row in dataset()["LAB_TICKETS"] if row["ticket_id"] == "lab-ticket-b1")
        ticket = {key: source[key] for key in _TICKET_FIELDS}
        ticket["id"] = "lab-ticket-b1"
        expected = dict(tool_name=name, status="allowed", code="allowed", data={"ticket": ticket})
    else:
        raise AssertionError("LAB_FIXTURE_EXPECTATION_NOT_DEFINED")
    return _assert_exact(result, expected)


def validate_large_result(result):
    """Exact 35-row mixed-profile large fixture (not a production limit rule)."""
    ticket = {key: "x" * 240 for key in ("ticket_id", "id", "subject", "status", "severity")}
    ticket.update(created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-03T00:00:00+00:00")
    expected = dict(tool_name="xoc.tickets.list", status="allowed", code="allowed",
                    data=dict(tickets=[dict(ticket) for _ in range(35)], count=35, truncated=False))
    return _assert_exact(result, expected)
