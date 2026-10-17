"""BENCHMARK ONLY: time calls to originals; no alternative business implementation.

Fault/data selection belongs to this test harness, never QueryWorker arguments.
The fixed verified_server still verifies the envelope and unconditionally uses Wiring.
"""
import argparse
from functools import wraps
import json
import logging
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("normal", "large", "blocked", "late"), default="normal")
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    from xoc_mcp_lab.wiring import Wiring, MemoryTable
    totals, counts = {}, {}
    def timed(name, fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            start = time.monotonic()
            try: return fn(*a, **kw)
            finally:
                totals[name] = totals.get(name, 0) + time.monotonic() - start
                counts[name] = counts.get(name, 0) + 1
        return wrapped
    original_enter = Wiring.__enter__
    def enter(self):
        start = time.monotonic()
        result = original_enter(self)
        totals["provider_import_and_wiring"] = time.monotonic() - start
        import xoc_mcp_lab.adapter as adapter
        import src.tool_gateway.executor as executor
        # Wrap references, delegate unchanged to canonical functions, never patch files.
        adapter.policy.evaluate_tool_request = timed("policy_preflight", adapter.policy.evaluate_tool_request)
        executor.evaluate_tool_request = timed("policy_executor", executor.evaluate_tool_request)
        executor.ToolExecutor.execute = timed("executor_inclusive", executor.ToolExecutor.execute)
        for name, handler in list(adapter.DEFAULT_HANDLERS.items()):
            adapter.DEFAULT_HANDLERS[name] = timed("handler_inclusive", handler)
        adapter.model_result = timed("projection", adapter.model_result)
        adapter.Adapter.call = timed("adapter_inclusive_serialization", adapter.Adapter.call)
        for name in ("query", "get_item"):
            setattr(MemoryTable, name, timed("fixture_read_inclusive", getattr(MemoryTable, name)))
        if args.scenario in ("blocked", "late"):
            original = MemoryTable._read
            def delayed(table):
                logging.getLogger("xoc_mcp_lab").info("lab_fault_stage %s", json.dumps(dict(
                    scenario=args.scenario, handler_entered=True, provider_read_entered=True)))
                time.sleep(60 if args.scenario == "blocked" else 3)
                return original(table)
            MemoryTable._read = delayed
        if args.scenario == "large":
            seed = self.rows["LAB_TICKETS"][0]
            fields = ("ticket_id", "id", "subject", "status", "severity")
            self.rows["LAB_TICKETS"] = [dict(seed, sk=f"TICKET#large-{i:03d}",
                **{key: "x" * 240 for key in fields}) for i in range(35)]
        return result
    Wiring.__enter__ = enter
    from xoc_mcp_lab.verified_server import main as verified_main
    code = verified_main()
    logging.getLogger("xoc_mcp_lab").info("lab_stage %s", json.dumps(dict(
        seconds=totals, calls=counts, inclusive_spans_not_additive=True), separators=(",", ":")))
    return code


if __name__ == "__main__": raise SystemExit(main())
