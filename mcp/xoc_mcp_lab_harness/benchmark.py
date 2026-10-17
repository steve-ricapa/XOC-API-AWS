"""Installed-only LOCAL Linux benchmark. Not Lambda cold starts or a cloud SLA."""
import asyncio
from contextlib import asynccontextmanager, nullcontext
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import anyio
from mcp.client import stdio
from xoc_mcp_lab.query_worker import QueryWorker
from xoc_mcp_lab_harness.observe import ProcessObserver, audit_events
from xoc_mcp_lab_harness.signing import envelope
from xoc_mcp_lab_harness.resources import snapshot


def read_number(path):
    try: return int(Path(path).read_text().strip())
    except (OSError, ValueError): return None


def rss(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"): return int(line.split()[1]) * 1024
    except OSError: pass
    return 0


class MemorySampler:
    def __init__(self, observer):
        self.observer = observer
        self.stop = threading.Event()
        self.peaks = dict(parent_rss=0, child_rss=0, simultaneous_parent_children_rss=0,
            container_current=None, live_children=0)
        self.samples = 0
    def __enter__(self):
        def sample():
            while not self.stop.is_set():
                parent = rss(os.getpid())
                children = [rss(p.pid) for p, _ in list(self.observer.processes) if p.returncode is None]
                values = dict(parent_rss=parent, child_rss=max(children, default=0),
                    simultaneous_parent_children_rss=parent + sum(children), live_children=len(children),
                    container_current=snapshot()["memory_current_bytes"])
                for key, value in values.items():
                    if value is not None: self.peaks[key] = max(self.peaks[key] or 0, value)
                self.samples += 1
                self.stop.wait(0.02)
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self
    def __exit__(self, *exc):
        self.stop.set(); self.thread.join(timeout=2)
        assert not self.thread.is_alive()
    def evidence(self):
        return dict(sample_interval_seconds=0.02, samples=self.samples, peaks=self.peaks,
            limitation="20ms sampling can miss peaks; RSS counts shared pages per process; cgroup current includes cache")


@asynccontextmanager
async def measured_launch(scenario):
    original = stdio._create_platform_compatible_process
    async def launch(**kwargs):
        args = kwargs["args"]
        assert args[:3] == ["-B", "-m", "xoc_mcp_lab.verified_server"]
        kwargs["args"] = ["-B", "-m", "xoc_mcp_lab_harness.measured_child", "--scenario", scenario, *args[3:]]
        return await original(**kwargs)
    with patch.object(stdio, "_create_platform_compatible_process", launch):
        yield


async def sample(name="xoc.tickets.list", *, scenario="normal", timeout=5, profile="admin_tenant_a",
                 instrumented=True, require_success=True):
    args = {"ticket_id": "lab-ticket-a1"} if name.endswith(".get") else {}
    worker = QueryWorker()
    value = envelope(profile, name, args)
    fault = "import time; time.sleep(60)" if scenario in ("initialization_hang", "cancellation") else "raise SystemExit(7)" if scenario == "eof" else None
    resource_before = snapshot()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver(fault) as observer:
        with MemorySampler(observer) as memory:
            start = time.monotonic()
            if scenario in ("normal", "large", "blocked", "late"):
                async with (measured_launch(scenario) if instrumented or scenario != "normal" else nullcontext()):
                    result = await worker.run(value, timeout_seconds=timeout, errlog=log)
            elif scenario == "cancellation":
                with patch.object(stdio, "_create_platform_compatible_process", observer.patch.new):
                    async with anyio.create_task_group() as group:
                        async def work(): await worker.run(value, timeout_seconds=0.4, errlog=log)
                        group.start_soon(work)
                        await anyio.sleep(0.1)
                        group.cancel_scope.cancel()
                result = dict(status="cancelled")
            else:
                result = await worker.run(value, timeout_seconds=timeout, errlog=log)
            total = time.monotonic() - start
        events = audit_events(log)
        log.seek(0)
        lines = log.read().splitlines()
        stage_rows = [json.loads(line.split("lab_stage ", 1)[1]) for line in lines if "lab_stage " in line]
        fault_rows = [json.loads(line.split("lab_fault_stage ", 1)[1]) for line in lines if "lab_fault_stage " in line]
        processes = observer.evidence()
        assert len(processes) == 1 and processes[0]["reaped"]
    if scenario in ("normal", "large") and (require_success or result["status"] == "completed"):
        assert result["status"] == "completed" and result["result"]["status"] == "allowed", result
        assert len(events) == 1 and events[0]["handler_calls"] == 1 and events[0]["writes"] == 0
    if scenario == "large" and result["status"] == "completed": assert processes[0]["wire_bytes"]["stdout"] > 65536
    marks = worker.measurements[0]
    if scenario in ("blocked", "late") and require_success:
        assert "initialized" in marks and len(fault_rows) == 1, "LAB_FAULT_DID_NOT_REACH_HANDLER"
    cleanup_start = marks.get("cleanup_started", marks["finished"])
    return dict(scenario=scenario, tool=name, status=result["status"], code=result.get("code"),
        tool_status=result.get("result", {}).get("status"),
        instrumentation="inclusive_child_spans" if instrumented else "minimal_parent_observer",
        lifecycle=getattr(worker, "lifecycle", []),
        total_seconds=total, work_seconds=cleanup_start, cleanup_seconds=marks["finished"]-cleanup_start,
        worker_timeline=marks, child_stages=stage_rows, memory=memory.evidence(), processes=processes,
        resource_before=resource_before, resource_after=snapshot(), fault_stages=fault_rows,
        handlers=max(sum(e["handler_calls"] for e in events), len(fault_rows)),
        completed_handler_audit_count=sum(e["handler_calls"] for e in events),
        reads=sum(e["reads"] for e in events), read_count_complete=not bool(fault_rows))


def stats(values):
    ordered = sorted(values)
    return dict(n=len(ordered), minimum=min(ordered), median=statistics.median(ordered), maximum=max(ordered),
        p95_nearest_rank=ordered[math.ceil(.95*len(ordered))-1],
        percentile_method="nearest rank; p99 omitted (small sample)")


async def concurrent():
    # Admission is local and no child gets reused. The fifth request must be rejected.
    resources_before = snapshot()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        with MemorySampler(observer) as memory:
            workers = [QueryWorker() for _ in range(5)]
            values = [envelope("user_a1" if i % 2 == 0 else "user_a2", "xoc.notifications.unread_count") for i in range(5)]
            start = time.monotonic()
            results = await asyncio.gather(*(w.run(v, errlog=log) for w, v in zip(workers, values)))
            elapsed = time.monotonic() - start
        assert sum(r.get("code") == "lab_busy" for r in results) == 1
        assert len(observer.processes) == 4
        completed = [r for r in results if r["status"] == "completed"]
        # Under the fixed CPU budget timeouts are measurements, not a reason to raise limits.
        assert all(r["result"]["data"]["count"] in (2, 5) for r in completed)
        children = observer.evidence()
    return dict(elapsed_seconds=elapsed, statuses=[r.get("code", r["status"]) for r in results],
        workers=[w.measurements for w in workers], processes=children, memory=memory.evidence(),
        resource_before=resources_before, resource_after=snapshot())


async def benchmark():
    from xoc_mcp_lab.installed import require_installed
    require_installed()
    rows, fresh = [], []
    # Each new parent is measured including interpreter/module startup and parent reap.
    for i in range(5):
        start = time.monotonic()
        proc = subprocess.run([sys.executable, "-I", "-B", "-m", "xoc_mcp_lab_harness.benchmark", "--one"],
            capture_output=True, text=True, timeout=20, cwd="/tmp")
        assert proc.returncode == 0, "LAB_FRESH_PARENT_FAILED"
        fresh.append(dict(total_parent_seconds=time.monotonic()-start, sample=json.loads(proc.stdout), parent_returncode=proc.returncode))
    for name in ("xoc.tickets.list", "xoc.tickets.get", "xoc.documents.list", "xoc.notifications.unread_count"):
        for i in range(20): rows.append(await sample(name))
        print(json.dumps(dict(benchmark_progress=name, samples=len(rows))), flush=True)
    parallel = await concurrent()
    faults = []
    for scenario in ("initialization_hang", "eof", "blocked", "late", "cancellation"):
        for i in range(2): faults.append(await sample(scenario=scenario, timeout=4.5 if scenario in ("blocked", "late") else 0.4))
    # Explicit worst-budget sample: 5 seconds of work PLUS actual cleanup.
    faults.append(await sample(scenario="initialization_hang", timeout=5))
    large = [await sample(scenario="large") for _ in range(3)]
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip()
    assert children == ""
    by_tool = {name: stats([r["total_seconds"] for r in rows if r["tool"] == name]) for name in {r["tool"] for r in rows}}
    summary = dict(status="measured", fresh_parents=5, sequential_samples=len(rows), by_tool=by_tool,
        sequential_total=stats([r["total_seconds"] for r in rows]),
        sequential_work=stats([r["work_seconds"] for r in rows]),
        sequential_cleanup=stats([r["cleanup_seconds"] for r in rows]),
        fault_total=stats([r["total_seconds"] for r in faults]), remaining_children=0,
        sequential_errors=sum(r["status"] != "completed" for r in rows),
        concurrency_errors=sum(s == "lab_worker_unavailable" for s in parallel["statuses"]),
        admission_denials=sum(s == "lab_busy" for s in parallel["statuses"]),
        budget_3_5_all_local_successes=all(r["total_seconds"] <= 3.5 for r in [*rows, *large]),
        budget_5_hard_all_scenarios=all(r["total_seconds"] <= 5 for r in [*rows, *large, *faults]),
        cloud_compatibility="NOT_VALIDATED", limitation="Local new processes, fixture providers, no Invoke/KMS/Azure/Foundry latency")
    return dict(lab_only=True, summary=summary, fresh=fresh, sequential=rows, concurrency=parallel,
        faults=faults, large=large, parent_pid=os.getpid(), children_checked_before_container_exit=children)


if __name__ == "__main__":
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    if sys.argv[1:] != ["--one"]: raise SystemExit("Use linux_validation benchmark")
    with Isolation():
        configure_logs()
        print(json.dumps(asyncio.run(sample())))
