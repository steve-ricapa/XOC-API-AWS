"""Strict acceptance oracles reject empty, crossed, reordered or extra data."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from xoc_mcp_lab_l6.validation import validate_fixture_result, validate_large_result


REFERENCE = Path("/offline/reference.json")
if not REFERENCE.is_file():
    REFERENCE = Path(__file__).resolve().parents[1] / "l4-runs/l4-final04-20260916/execution/offline/reference.json"
CASES = json.loads(REFERENCE.read_text(encoding="utf-8"))["cases"]


def wrap(payload):
    payload = dict(deepcopy(payload), request_id="lab-oracle-unit", audit_id="fixture-audit-id")
    status = "error" if payload["code"] == "execution_failed" else payload["status"]
    return dict(status=status, code=payload["code"], request_id=payload["request_id"],
                is_error=payload["status"] != "allowed", result=payload)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["profile"] + ":" + c["name"])
def test_preserved_reference_cases_compare_exactly(case):
    assert validate_fixture_result(wrap(case["result"]), case["profile"], case["name"], case["arguments"],
                                   reference_path=REFERENCE)


@pytest.mark.parametrize("change", ["empty", "crossed", "count", "order", "extra", "bool_count", "binding"])
def test_C01_C15_exact_oracle_rejects_wrong_data(change):
    result = wrap(CASES[0]["result"])
    data = result["result"]["data"]
    if change == "empty":
        data["tickets"] = []
    elif change == "crossed":
        data["tickets"][0]["ticket_id"] = "lab-ticket-b2"
    elif change == "count":
        data["count"] = 3
    elif change == "order":
        data["tickets"].reverse()
    elif change == "extra":
        data["tickets"][0]["private"] = "forbidden"
    elif change == "bool_count":
        data["count"] = True
    else:
        result["request_id"] = "lab-other-request"
    with pytest.raises(AssertionError):
        validate_fixture_result(result, "admin_tenant_a", "xoc.tickets.list", reference_path=REFERENCE)


def test_missing_B_owned_get_expectation_is_exact_and_not_cross_tenant():
    tickets = next(case for case in CASES if case["profile"] == "admin_tenant_b" and case["name"] == "xoc.tickets.list")
    ticket = next(row for row in tickets["result"]["data"]["tickets"] if row["ticket_id"] == "lab-ticket-b1")
    result = wrap(dict(tool_name="xoc.tickets.get", status="allowed", code="allowed", data=dict(ticket=ticket)))
    assert validate_fixture_result(result, "admin_tenant_b", "xoc.tickets.get", reference_path=REFERENCE)
    result["result"]["data"]["ticket"] = None
    with pytest.raises(AssertionError):
        validate_fixture_result(result, "admin_tenant_b", "xoc.tickets.get", reference_path=REFERENCE)


def test_large_expected_count_is_35_and_empty_allowed_is_not_success():
    row = {field: "x" * 240 for field in ("ticket_id", "id", "subject", "status", "severity")}
    row.update(created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-03T00:00:00+00:00")
    result = wrap(dict(tool_name="xoc.tickets.list", status="allowed", code="allowed",
                       data=dict(tickets=[dict(row) for _ in range(35)], count=35, truncated=False)))
    assert validate_large_result(result)
    result["result"]["data"]["tickets"] = []
    with pytest.raises(AssertionError):
        validate_large_result(result)
