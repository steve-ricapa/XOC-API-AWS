"""Independent signed fixture smoke; source and installed LAB_ONLY artifact."""
import argparse
import asyncio
import json
import sys
import tempfile
import time
from xoc_mcp_lab.isolation import Isolation, configure_logs


async def smoke(backend):
    from xoc_mcp_lab.query_worker import QueryWorker
    from xoc_mcp_lab_harness.signing import envelope
    from xoc_mcp_lab_harness.observe import ProcessObserver, audit_events
    summary = dict(lab_only=True, fixture_only=True, model_calls=0, cases=[])
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        with ProcessObserver() as observer:
            for profile, letter in (("admin_tenant_a", "a"), ("admin_tenant_b", "b")):
                value, worker = envelope(profile), QueryWorker()
                result = await worker.run(value, backend, errlog=log)
                assert result["status"] == "completed", result
                assert {r["ticket_id"] for r in result["result"]["data"]["tickets"]} == {
                    f"lab-ticket-{letter}1", f"lab-ticket-{letter}2"}
                assert (await worker.run(value, backend, errlog=log))["code"] == "lab_call_limit"
                summary["cases"].append(dict(case=profile, status="passed"))
            count = len(observer.processes)
            for case in ("invalid_signature", "changed_tool", "changed_arguments"):
                value = envelope(scalar=2 if case == "invalid_signature" else 1)
                if case == "changed_tool": value["tool"]["name"] = "xoc.documents.list"
                if case == "changed_arguments": value["tool"]["arguments"] = {"limit": 1}
                assert (await QueryWorker().run(value, backend, errlog=log))["code"] == "lab_invalid_envelope"
                assert len(observer.processes) == count
                summary["cases"].append(dict(case=case, status="passed", children=0))
            denied = await QueryWorker().run(envelope("superadmin"), backend, errlog=log)
            assert denied["result"]["status"] == "denied" and denied["result"]["data"] is None
            events = audit_events(log)
            assert [e["handler_calls"] for e in events] == [1, 1, 0]
            assert [e["reads"] for e in events] == [1, 1, 0]
            summary["cases"].append(dict(case="signed_superadmin_denied", status="passed", handlers=0, reads=0))
            summary["children"] = observer.evidence()
    summary["status"] = "passed"
    return summary


def main():
    parser = argparse.ArgumentParser(description="Signed L1 fixture smoke, no model")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backend")
    group.add_argument("--installed-lab", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    with Isolation():
        configure_logs()
        summary = asyncio.run(smoke(args.backend))
    summary["total_seconds"] = time.monotonic() - started
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
