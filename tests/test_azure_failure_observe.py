"""The production observer only projects safe telemetry and rejects injected IDs."""
import importlib.util
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "azure_failure_observe", Path(__file__).parent / "ac1/azure_failure_observe.py"
)
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def test_failure_query_is_bounded_to_instrumented_requests():
    query = observer.queries("2026-10-13T17:49:00Z")["failures"]
    assert "sophia_run" in query
    assert "2026-10-13T05:52:19Z" in query
    assert "timestamp <= datetime(2026-10-13T17:49:00Z)" in query
    assert "top 20" in query
    assert "project timestamp,operation_Id,resultCode,duration" in query


def test_new_observation_does_not_reuse_cached_query():
    before = observer.queries("2026-10-13T17:47:00Z")
    after = observer.queries("2026-10-13T17:49:00Z")
    assert before["requests"] != after["requests"]
    assert before["failures"] != after["failures"]


def test_stage_query_projects_no_messages_or_payloads():
    assert observer.stage_query(["bad') | project message //"]) is None
    query = observer.stage_query(["a" * 32])
    assert "operation_Id in ('" + "a" * 32 + "')" in query
    assert "project timestamp,operation_Id,phase,stage,outcome,status,code,http_status,duration_ms" in query
    assert "project message" not in query


def test_observer_correlates_a_failure_without_printing_raw_traces(capsys):
    operation_id = "a" * 32
    requested = []

    def get(_url, *, headers, params, timeout):
        requested.append(params["query"])
        if "sophia_provider_stage" in params["query"]:
            columns = ["operation_Id", "phase", "stage", "outcome", "status"]
            rows = [[operation_id, "result", "get_run", "failed", ""]]
        elif "startswith '5'" in params["query"]:
            columns = ["operation_Id", "resultCode", "duration"]
            rows = [[operation_id, "502", 9000]]
        else:
            columns = ["resultCode", "count"]
            rows = [["502", 1]]
        assert headers["Authorization"] == "Bearer fictional-test-token"
        assert timeout == 30
        return type("Response", (), {"ok": True, "json": lambda self: {"tables": [{
            "columns": [{"name": name} for name in columns], "rows": rows,
        }]}})()

    with patch.object(observer.subprocess, "check_output", return_value=b'{"accessToken":"fictional-test-token"}'), \
         patch.object(observer.requests, "get", side_effect=get):
        observer.main()

    output = capsys.readouterr().out
    assert operation_id in output
    assert '"stage": "get_run"' in output
    assert "fictional-test-token" not in output
    assert len(requested) == 3
