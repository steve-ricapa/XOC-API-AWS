"""FT-2R local campaign. Immutable installed L6, sequential containers, no cloud.

Control populations use original modules and timing contracts. Diagnostic mode
adds only a test bootstrap; no candidate fix, warm-up or replacement assertion.
"""
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

BASELINE = "sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4"
FAILED = ("C06", "C08", "C10", "C11", "C12", "C14", "C15", "C18", "C19")


def safe_control(rows):
    # Control messages contain technical metadata, not business results.
    excluded = {"jobs"}  # Keep lifecycle timing, not request/identity details.
    return [{k: v for k, v in row.items() if k not in excluded} for row in rows]


def classify_wait(predicate, caller):
    values = set(v for v in predicate.__code__.co_consts if isinstance(v, str))
    if "ready" in values:
        return "replacement_ready" if caller in ("restored", "recovered") else "ready"
    for marker in ("incident", "retired", "heartbeat", "ack", "snapshot", "phase", "state", "status"):
        if marker in values:
            return marker + ":" + ",".join(sorted(values - {marker}))
    return "other:" + caller


async def one_inside(mode, case=None):
    sys.path.insert(0, "/ic1/harness")
    natural = mode in ("natural", "natural-recovery")
    diagnostic = mode in ("timeline", "focal") or natural
    if natural:
        from ft2r1_observer import install_parent
        install_parent()
    if mode == "guarded":
        from no_stdio import install
        install("/tmp/ft2r-guard")
    if mode in ("timeline", "focal"):
        from ft2r_probe import install_parent
        install_parent()
    from xoc_mcp_lab_l6.integrity import evidence
    integrity = evidence()
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    from xoc_mcp_lab_harness.linux_validation import environment
    metadata = environment()
    with Isolation() as isolation:
        configure_logs()
        from xoc_mcp_lab_l6 import harness
        from xoc_mcp_lab_l6.acceptance import recovered, run_case
        waits, servers, resources = [], [], []
        original_cgroup = harness.cgroup_extra
        def cgroup():
            value = original_cgroup()
            # cgroup v1 CPU accounting is not included by the canonical sampler.
            for name in ("cpu/cpu.stat", "cpuacct/cpuacct.usage", "cpuacct/cpuacct.stat"):
                path = Path("/sys/fs/cgroup") / name
                if path.is_file():
                    value[name] = path.read_text().strip()
            return value
        harness.cgroup_extra = cgroup
        original_wait = harness.SupervisedProcess.wait_message
        original_init = harness.SupervisedProcess.__init__

        def init(self, *args, **kwargs):
            began = time.monotonic()
            original_init(self, *args, **kwargs)
            servers.append(self)
            self.ft2r_requested = began
            self.ft2r_profile = args[0] if args else kwargs.get("profile", "none")

        async def wait(self, predicate, timeout=16, after=0):
            caller = sys._getframe(1).f_code.co_name
            name = classify_wait(predicate, caller)
            closure = dict(zip(predicate.__code__.co_freevars,
                [cell.cell_contents for cell in (predicate.__closure__ or ())]))
            expected_generation = closure.get("generation")
            if name == "ready" and isinstance(expected_generation, int) and expected_generation > 1:
                name = "replacement_ready"
            began = time.monotonic()
            row = dict(event=name, caller=caller, begin=began, timeout=timeout,
                       generation=self.latest.get("child_generation"), expected_generation=expected_generation,
                       supervisor_pid=self.proc.pid, fault_profile=self.ft2r_profile)
            try:
                value = await original_wait(self, predicate, timeout, after)
                row.update(ok=True, received_kind=value.get("kind"), received_generation=value.get("child_generation"))
                return value
            except Exception as error:
                row.update(ok=False, category=type(error).__name__)
                raise
            finally:
                row["end"] = time.monotonic()
                waits.append(row)

        harness.SupervisedProcess.__init__ = init
        harness.SupervisedProcess.wait_message = wait

        async def sample():
            while True:
                for server in servers:
                    # Read only the exact owned supervisor/children. No process search.
                    try:
                        pids = Path(f"/proc/{server.proc.pid}/task/{server.proc.pid}/children").read_text().split()
                    except FileNotFoundError:
                        pids = []
                    for pid in pids:
                        path = Path("/proc") / pid
                        try:
                            stat = (path / "stat").read_text().split(") ", 1)[1].split()
                            status = dict(line.split(":", 1) for line in (path / "status").read_text().splitlines() if ":" in line)
                            resources.append(dict(at=time.monotonic(), pid=int(pid), ppid=int(stat[1]),
                                generation=server.latest.get("child_generation"), state=stat[0],
                                utime=int(stat[11]), stime=int(stat[12]), start_ticks=int(stat[19]),
                                rss=status.get("VmRSS", "").strip(), hwm=status.get("VmHWM", "").strip(),
                                threads=status.get("Threads", "").strip(), fds=len(list((path / "fd").iterdir())),
                                schedstat=(path / "schedstat").read_text().strip() if (path / "schedstat").is_file() else None,
                                cgroup=harness.cgroup_extra()))
                        except (OSError, ValueError):
                            pass
                await asyncio.sleep(.25)

        task = asyncio.create_task(sample()) if diagnostic else None
        record = dict(mode=mode, integrity=integrity, environment=metadata,
                      requested=time.monotonic(), cgroup_before=harness.cgroup_extra(),
                      process_ticks_per_second=os.sysconf("SC_CLK_TCK"))
        if natural:
            record.update(PRODUCT_STARTUP_BUDGET=4.0, DIAGNOSTIC_OBSERVATION_CAP=12.0,
                          diagnostic_only=True, product_acceptance=False)
        try:
            if mode == "focal":
                row = await run_case(case)
                record.update(case=case, passed=row["passed"], error_type=row.get("error_type"),
                    error_code=row.get("error_code"), failure_location=row.get("failure_location"),
                    cleanup=row["cleanup"], control=safe_control(row["control"]))
            else:
                # Official fault injection is permitted only in the mixed lab profile.
                # Control populations retain the original no-fault profile.
                server = harness.SupervisedProcess("mixed" if mode in ("timeline", "natural-recovery", "recovery-original") else "none")
                try:
                    # Original supervisor decides success/failure; no extra attempt.
                    ready = await server.wait_message(lambda r: r.get("kind") == "ready" or
                        (r.get("kind") == "state" and r.get("state") == "UNAVAILABLE"), 18)
                    record["initial_ready"] = ready.get("kind") == "ready"
                    if record["initial_ready"]:
                        server.generation = ready["child_generation"]
                        server.server_pid = ready["child_pid"]
                        server.readiness = ready
                        query = await harness.query(server)
                        record["first_query"] = dict(at=time.monotonic(), status=query.get("status"),
                                                     seconds=query.get("total_seconds"))
                        harness.check_result(query, "admin_tenant_a", harness.TOOLS[0])
                        if mode in ("timeline", "natural-recovery", "recovery-original"):
                            record["recovery_requested"] = time.monotonic()
                            server.command("test", action="recover")
                            result = await recovered(server)
                            record["recovery"] = result
                    record["passed"] = record["initial_ready"]
                finally:
                    record["cleanup"] = await server.close()
                    record["control"] = safe_control(server.messages)
        except Exception as error:
            record.update(passed=False, error_type=type(error).__name__)
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            record["waits"] = waits
            record["owned_servers"] = [dict(supervisor_pid=s.proc.pid, fault_profile=s.ft2r_profile,
                requested=s.ft2r_requested, control=safe_control(s.messages), cleanup=s.close_evidence) for s in servers]
            record["resources"] = resources
            record["finished"] = time.monotonic()
            record["cgroup_after"] = harness.cgroup_extra()
            record["isolation"] = dict(network_attempts=isolation.network_attempts,
                                       secret_file_attempts=isolation.secret_file_attempts)
            record["final_children"] = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip()
            if diagnostic:
                record["timeline"] = [json.loads(line) for p in sorted(Path("/tmp/ft2r-probe").glob("*.jsonl"))
                    for line in p.read_text().splitlines() if line]
            if mode == "guarded":
                from no_stdio import evidence as no_stdio
                record["no_stdio"] = no_stdio("/tmp/ft2r-guard")
        return record


def invoke(image, mode, target, case=None):
    name = "xoc-ft2r-" + uuid4().hex[:12]
    command = ["docker", "run", "--rm", "--name", name, "--label", "xoc.campaign=ft2r-20261010",
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=134217728",
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "2", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--platform", "linux/amd64",
        "--entrypoint", "/opt/xoc-l2/bin/python", image, "-I", "-B", "/ic1/harness/ft2r.py", "--inside", mode]
    if case:
        command += ["--case", case]
    requested = time.monotonic()
    try:
        run = subprocess.run(command, capture_output=True, timeout=100)
    except subprocess.TimeoutExpired:
        # Exact generated name + owned campaign label, never all Docker processes.
        label = subprocess.check_output(["docker", "inspect", "--format", '{{index .Config.Labels "xoc.campaign"}}', name], text=True).strip()
        if label == "ft2r-20261010":
            subprocess.run(["docker", "stop", "--time", "5", name], check=True, capture_output=True)
        raise
    try:
        record = json.loads(run.stdout)
    except ValueError:
        record = dict(passed=False, infrastructure_error="invalid_report",
                      stderr=run.stderr.decode(errors="replace"))
    record.update(image=image, host_requested=requested, host_finished=time.monotonic(), exit_code=run.returncode,
                  stderr_sha256=hashlib.sha256(run.stderr).hexdigest())
    with target.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    phases = [r for r in record.get("control", []) if r.get("kind") == "phase" and r.get("phase") == "startup"]
    print(json.dumps(dict(file=target.name, passed=record["passed"],
        startups=[dict(generation=r["child_generation"], seconds=r["elapsed"], ok=r["ok"]) for r in phases])), flush=True)
    if record.get("final_children") or record.get("cleanup", {}).get("own_server_residual"):
        raise RuntimeError("FT2R_CLEANUP_STOP")
    if "infrastructure_error" in record:
        raise RuntimeError("FT2R_INFRASTRUCTURE_STOP")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", choices=("original", "guarded", "timeline", "focal", "natural", "natural-recovery", "recovery-original"))
    parser.add_argument("--case")
    parser.add_argument("--population", choices=("original", "guarded", "timeline", "focal"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-diagnostic-runs", type=int, default=30)
    parser.add_argument("--focal-repeats", type=int, choices=range(0, 6), default=1)
    args = parser.parse_args()
    if args.inside:
        print(json.dumps(asyncio.run(one_inside(args.inside, args.case))))
        return 0  # Red experimental observations are data, not aborted population.
    target = args.output.resolve()
    if target.exists() or not target.is_relative_to(Path(__file__).resolve().parent / ".build"):
        raise ValueError("FT2R_NEW_OUTPUT_REQUIRED")
    target.mkdir(parents=True)
    active = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
    if active:
        raise RuntimeError("FT2R_CONCURRENT_CONTAINER_STOP")
    image = subprocess.check_output(["docker", "image", "inspect", "xoc-chat-mcp-ft2r:diagnostic", "--format", "{{.Id}}"], text=True).strip()
    baseline = subprocess.check_output(["docker", "image", "inspect", "xoc-mcp-ic1:baseline", "--format", "{{.Id}}"], text=True).strip()
    if baseline != BASELINE:
        raise RuntimeError("FT2R_BASELINE_DRIFT")
    if args.population in ("original", "guarded"):
        for index in range(1, 21):
            invoke(image, args.population, target / f"run-{index:02}.json")
    elif args.population == "timeline":
        recoveries = 0
        for index in range(1, min(30, args.max_diagnostic_runs)+1):
            row = invoke(image, "timeline", target / f"run-{index:02}.json")
            recoveries += int("recovery_requested" in row)
            if index >= 10 and recoveries >= 10:
                break
        print(json.dumps(dict(initials=index, recovery_attempts=recoveries)), flush=True)
    else:
        failed = []
        if args.case and args.case not in FAILED:
            raise ValueError("FT2R_FOCAL_CASE_NOT_AUTHORIZED")
        for case in ((args.case,) if args.case else FAILED):
            row = invoke(image, "focal", target / f"{case}-01.json", case)
            if not row["passed"]:
                failed.append(case)
        for case in failed:
            for repeat in range(2, 2 + args.focal_repeats):
                row = invoke(image, "focal", target / f"{case}-{repeat:02}.json", case)
                if row["passed"]:
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
