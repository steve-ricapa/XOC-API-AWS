"""L3 acceptance uses the L2 observer, WITHOUT invasive child profiling.

The same module is packaged before and after the candidate. Original L2 images
and measurements remain immutable. Diagnostic cProfile is a separate phase.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def summarize(rows):
    from xoc_mcp_lab_harness.benchmark import stats
    def subset(values):
        return stats(values) if values else None
    return dict(n=len(rows), successes=sum(r["status"] == "completed" for r in rows),
        expected_denials=sum(r.get("tool_status") == "denied" for r in rows),
        cancelled=sum(r["status"] == "cancelled" for r in rows),
        timeouts=sum(any(x.get("timeout") for x in r.get("lifecycle", [])) or
            (not r.get("lifecycle") and r["scenario"] in ("initialization_hang", "blocked", "late") and r["status"] == "error") for r in rows),
        technical_errors=sum(r["status"] == "error" for r in rows),
        over_3_5=sum(r["total_seconds"] > 3.5 for r in rows),
        over_5=sum(r["total_seconds"] > 5 for r in rows),
        total=subset([r["total_seconds"] for r in rows]),
        work=subset([r["work_seconds"] for r in rows]),
        cleanup=subset([r["cleanup_seconds"] for r in rows]),
        successful_total=subset([r["total_seconds"] for r in rows if r["status"] == "completed"]))


async def benchmark():
    from xoc_mcp_lab_harness.benchmark import sample, concurrent
    fresh, sequential = [], []
    for _ in range(5):
        start = time.monotonic()
        proc = subprocess.run([sys.executable, "-I", "-B", "-m", "xoc_mcp_lab_harness.l3_benchmark", "one"],
            capture_output=True, text=True, timeout=20, cwd="/tmp")
        assert proc.returncode == 0, "LAB_L3_FRESH_PARENT_FAILED"
        fresh.append(dict(total_parent_seconds=time.monotonic()-start,
            sample=json.loads(proc.stdout), parent_returncode=proc.returncode))
    for name in ("xoc.tickets.list", "xoc.tickets.get", "xoc.documents.list", "xoc.notifications.unread_count"):
        for _ in range(20):
            sequential.append(await sample(name, instrumented=False, require_success=False))
        print(json.dumps(dict(l3_progress=name, samples=len(sequential))), flush=True)
    pressure = await concurrent()
    faults = []
    for scenario in ("initialization_hang", "eof", "blocked", "late", "cancellation"):
        for _ in range(2):
            faults.append(await sample(scenario=scenario, timeout=4.5 if scenario in ("blocked", "late") else 0.4,
                instrumented=False, require_success=False))
    faults.append(await sample(scenario="initialization_hang", timeout=5, instrumented=False, require_success=False))
    large = [await sample(scenario="large", instrumented=False, require_success=False) for _ in range(3)]
    return dict(lab_only=True, minimal_telemetry=True, fresh=fresh, sequential=sequential,
        concurrency=pressure, faults=faults, large=large,
        summary=dict(sequential=summarize(sequential), faults=summarize(faults), large=summarize(large)))


async def profiles():
    from xoc_mcp_lab.query_worker import QueryWorker
    from xoc_mcp_lab_harness.benchmark import sample
    from xoc_mcp_lab_harness.observe import ProcessObserver
    from xoc_mcp_lab_harness.signing import envelope
    from mcp.client import stdio
    rows = []
    # Interleaved diagnostics/minimal samples; never subtract profiler cost from acceptance.
    for _ in range(3):
        minimal = await sample(instrumented=False, require_success=False)
        original = stdio._create_platform_compatible_process
        launch_marks = {}
        async def launch(**kwargs):
            launch_marks["parent_spawn_enter_ns"] = time.perf_counter_ns()
            args = kwargs["args"]
            kwargs["args"] = ["-B", "-X", "importtime", "-m", "xoc_mcp_lab_harness.l3_profile_child", *args[3:]]
            result = await original(**kwargs)
            launch_marks["parent_spawn_return_ns"] = time.perf_counter_ns()
            return result
        worker = QueryWorker()
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
            original = stdio._create_platform_compatible_process
            with patch.object(stdio, "_create_platform_compatible_process", launch):
                start = time.monotonic()
                result = await worker.run(envelope(), errlog=log)
                total = time.monotonic()-start
            log.seek(0)
            lines = log.read().splitlines()
            detail = [json.loads(line.split("lab_l3_profile ", 1)[1]) for line in lines if line.startswith("lab_l3_profile ")]
            # CPython importtime records only public module names and microseconds.
            imports = []
            for line in lines:
                if not line.startswith("import time:"): continue
                parts = line[len("import time:"):].split("|")
                if len(parts) == 3 and parts[0].strip().isdigit():
                    imports.append(dict(exclusive_us=int(parts[0]), cumulative_us=int(parts[1]), module=parts[2].strip()))
            rows.append(dict(minimal=minimal, diagnostic=dict(status=result["status"], code=result.get("code"),
                total_seconds=total, worker_timeline=worker.measurements, child_profile=detail,
                imports=imports, launch_marks=launch_marks, processes=observer.evidence())))
    return dict(lab_only=True, diagnostic_only=True, pairs=rows,
        clock_method="Linux perf_counter CLOCK_MONOTONIC in same container/boot. Parent spawn and child first marker give scheduled interpreter bootstrap interval, not CPU-only time; no Windows cross-clock subtraction.",
        warning="cProfile and importtime are invasive; inclusive times overlap and are NOT added. Missing final child profile means interrupted diagnostic, never zero cost.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("one", "benchmark", "profile"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    if args.phase == "one":
        with Isolation():
            configure_logs()
            from xoc_mcp_lab_harness.benchmark import sample
            print(json.dumps(asyncio.run(sample(instrumented=False, require_success=False))))
        return 0
    from xoc_mcp_lab_harness.linux_validation import environment, write_new
    from xoc_mcp_lab_harness.resources import snapshot
    args.output.mkdir(exist_ok=False)
    write_new(args.output / "environment.json", environment())
    with Isolation():
        configure_logs()
        result = asyncio.run(benchmark() if args.phase == "benchmark" else profiles())
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip()
    assert not children, "LAB_CHILDREN_REMAIN"
    result["final_process_check"] = dict(children=children, checked_before_exit=True, resources=snapshot())
    write_new(args.output / (args.phase + ".json"), result)
    print(json.dumps(dict(profile_pairs=len(result["pairs"])) if args.phase == "profile" else result["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
