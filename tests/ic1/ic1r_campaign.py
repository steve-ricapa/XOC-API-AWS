"""IC-1R observational campaign. Immutable installed MCP; no changed assertions.

Fresh network-less container per population. Never chooses only green runs.
No raw exception messages, envelopes, results, credentials or environment values
are retained. Historical IC-1 evidence is read/hash-checked, never overwritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

BASELINE = "sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4"
A = "test_l2_lifecycle.py::test_concurrent_calls_same_instance_only_one_child"
B = "test_worker_lifecycle.py::test_inherited_secrets_never_used"
BEFORE_A = "test_l2_lifecycle.py::test_cleanup_error_after_reap_still_safe_and_slot_released"
AFTER_A = "test_l2_lifecycle.py::test_monotonic_total_includes_cleanup"
BEFORE_B = "test_worker_lifecycle.py::test_invalid_child_envelope_never_starts_business"
AFTER_B = "test_worker_lifecycle.py::test_two_workers_concurrently_isolate_users"
SENSITIVE = ("AWS_PROFILE", "AZURE_CLIENT_SECRET", "DATABASE_URL", "PYTHONPATH")


def inside(mode, observe):
    import asyncio
    import contextlib
    from contextvars import ContextVar
    from datetime import datetime, timezone
    import io
    import os
    import re
    import tempfile
    import threading
    from unittest.mock import patch
    from xoc_mcp_lab_harness.linux_validation import environment
    from xoc_mcp_lab_l6.integrity import require_l6_installed
    from xoc_mcp_lab.isolation import Isolation, configure_logs

    require_l6_installed()
    env = environment()
    rows, calls, resources, order = [], [], [], []
    active = ContextVar("ic1r_observed_call", default=None)
    current_test = [None]
    started = time.monotonic()
    start_utc = datetime.now(timezone.utc).isoformat()
    with Isolation() as isolation:
        configure_logs()
        from xoc_mcp_lab.installed import require_installed
        from xoc_mcp_lab import testing, budget, query_worker
        from xoc_mcp_lab_l6 import harness
        from mcp.client import stdio
        import xoc_mcp_lab_harness
        import pytest
        testing.BACKEND = require_installed()
        root = Path(xoc_mcp_lab_harness.__file__).parent / "tests"
        original_run = query_worker.QueryWorker._run
        original_attach = budget.ProcessOwner.attach
        original_exchange = budget.ProcessOwner.exchange
        original_factory = budget._SDK_CREATE

        def sample(label):
            return dict(event=label, monotonic=time.monotonic(), parent_pid=os.getpid(),
                children=Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip(),
                threads=threading.active_count(), process=harness.process_resources(os.getpid()),
                cgroup=harness.cgroup_extra())

        def state():
            # IDs identify restoration within ONE interpreter, not stable cross-run values.
            return dict(owner_clear=budget._OWNER.get() is None,
                free_slots=query_worker._SLOTS._value,
                factory_id=id(stdio._create_platform_compatible_process),
                stdio_id=id(stdio.stdio_client),
                loop_policy_id=id(asyncio.get_event_loop_policy()),
                temp_cache=tempfile.tempdir,
                temp_owner=os.stat(tempfile.gettempdir()).st_uid,
                threads=threading.active_count(),
                children=Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip())

        def exception_categories(error):
            values = [type(error).__name__]
            for child in getattr(error, "exceptions", ()):
                values.extend(exception_categories(child))
            # Never exception text, SQL, payload, args or traceback locals.
            return sorted(set(values))

        async def observed_run(self, *args, **kwargs):
            entry = dict(test=current_test[0], call_index=len(calls),
                worker_id=id(self), attempted_at=time.monotonic(), timeline=[],
                parent_sensitive_presence={name: name in os.environ for name in SENSITIVE})
            calls.append(entry)
            original_mark = kwargs["mark"]
            def mark(name):
                original_mark(name)
                entry["timeline"].append(dict(event=name, monotonic=time.monotonic()))
            kwargs["mark"] = mark
            token = active.set(entry)
            logfile = kwargs.get("errlog")
            try:
                result = await original_run(self, *args, **kwargs)
                entry["status"] = result.get("status")
                code = result.get("code")
                entry["code"] = code if code is None or re.fullmatch(r"[a-z_]{1,80}", code) else "redacted"
                return result
            except BaseException as error:
                entry["raised_categories"] = exception_categories(error)
                raise
            finally:
                entry["returned_at"] = time.monotonic()
                entry["lifecycle"] = self.lifecycle[-1:]
                owner = entry.pop("_owner", None)
                if owner is not None:
                    entry["owner"] = dict(phase=owner.phase, cancelled=owner.cancelled,
                        timed_out=owner.timed_out, cleanup_errors=owner.cleanup_errors,
                        actions=list(owner.actions), child_pid=owner.process.pid if owner.process else None,
                        returncode=owner.process.returncode if owner.process else None,
                        deadline=owner.budget.deadline, work_deadline=owner.budget.work_deadline)
                # Reading the test's own technical audit AFTER completion; restore position.
                if logfile is not None and logfile is not sys.stderr:
                    try:
                        logfile.flush()
                        position = logfile.tell()
                        logfile.seek(0)
                        log = logfile.read(131072)
                        logfile.seek(position)
                        entry["guard_triggered"] = any(s in log for s in (
                            "LAB_SECRET_FILE_BLOCKED", "LAB_EXTERNAL_NETWORK_BLOCKED"))
                        audits = []
                        for line in log.splitlines():
                            if "lab_call " in line:
                                value = json.loads(line.split("lab_call ", 1)[1])
                                audits.append({k: value[k] for k in (
                                    "handler_calls", "executor_calls", "reads", "writes",
                                    "network_attempts", "secret_file_attempts") if k in value})
                        entry["audit_counters"] = audits
                        entry["provider_attempted"] = any(v.get("reads", 0) for v in audits)
                        entry["handler_entered"] = any(v.get("handler_calls", 0) for v in audits)
                    except (OSError, ValueError):
                        entry["audit_observation_unavailable"] = True
                active.reset(token)

        def observed_attach(self, process):
            value = original_attach(self, process)
            entry = active.get()
            if entry is not None:
                entry["_owner"] = self
                entry["child_pid"] = process.pid
            return value

        async def observed_factory(**kwargs):
            entry = active.get()
            if entry is not None:
                child_env = kwargs.get("env") or {}
                entry["child_sensitive_presence"] = {name: bool(child_env.get(name)) for name in SENSITIVE}
            return await original_factory(**kwargs)

        async def observed_exchange(self, *args, **kwargs):
            try:
                return await original_exchange(self, *args, **kwargs)
            except BaseException as error:
                entry = active.get()
                if entry is not None:
                    entry["exchange_error_categories"] = exception_categories(error)
                raise

        class Reporter:
            def pytest_collection_finish(self, session):
                order.extend(item.nodeid for item in session.items)

            @pytest.hookimpl(hookwrapper=True)
            def pytest_runtest_protocol(self, item, nextitem):
                current_test[0] = item.nodeid
                env_before = dict(os.environ)  # process is already isolated; never serialized
                before = state()
                resources.append(dict(test=item.nodeid, **sample("test_start")))
                test_start = time.monotonic()
                yield
                after = state()
                resources.append(dict(test=item.nodeid, **sample("test_end")))
                rows.append(dict(nodeid=item.nodeid, phase="lifecycle", start=test_start,
                    end=time.monotonic(), before=before, after=after,
                    changed_environment_names=sorted(k for k in set(env_before) | set(os.environ)
                        if env_before.get(k) != os.environ.get(k)),
                    guard_counts=dict(network=isolation.network_attempts, secrets=isolation.secret_file_attempts)))
                current_test[0] = None

            def pytest_runtest_logreport(self, report):
                if report.when == "call" or report.failed or report.skipped:
                    rows.append(dict(nodeid=report.nodeid, phase=report.when,
                        outcome=report.outcome, duration=report.duration, observed_at=time.monotonic()))

        initial = sample("run_start")
        log = io.StringIO()
        with contextlib.ExitStack() as patches:
            if observe:
                for target, name, replacement in (
                    (query_worker.QueryWorker, "_run", observed_run),
                    (budget.ProcessOwner, "attach", observed_attach),
                    (budget.ProcessOwner, "exchange", observed_exchange),
                    (budget, "_SDK_CREATE", observed_factory)):
                    patches.enter_context(patch.object(target, name, replacement))
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                if mode == "full":
                    # Exact installed suite/order/assertions, with an additional observational reporter.
                    original_pytest = pytest.main
                    def report_main(args, plugins=None):
                        return original_pytest(args, plugins=[*(plugins or []), Reporter()])
                    with patch.object(pytest, "main", report_main):
                        result = harness.tests()
                    code = result["exit_code"]
                else:
                    populations = {"a": [A], "b": [B], "ab": [A, B], "ba": [B, A],
                        "neighbors-a": [BEFORE_A, A, AFTER_A], "neighbors-b": [BEFORE_B, B, AFTER_B]}
                    code = int(pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider",
                        *[str(root / name) for name in populations[mode]]], plugins=[Reporter()]))
        final = sample("run_end")
        outcomes = [row for row in rows if "outcome" in row]
        focal_calls = [c for c in calls if c["test"] and (c["test"].endswith(A) or c["test"].endswith(B))]
        security_stop = any(c.get("guard_triggered") or any(c.get("child_sensitive_presence", {}).values())
                            for c in focal_calls)
        report = dict(mode=mode, observed_worker=observe, environment=env, start_utc=start_utc,
            end_utc=datetime.now(timezone.utc).isoformat(), start=started, end=time.monotonic(),
            exit_code=int(code), passed=sum(r["outcome"] == "passed" for r in outcomes),
            failed=sum(r["outcome"] == "failed" for r in outcomes),
            skipped=sum(r["outcome"] == "skipped" for r in outcomes),
            order=order, tests=rows, calls=calls, resources=resources,
            initial=initial, final=final, security_stop=security_stop,
            captured_log_sha256=hashlib.sha256(log.getvalue().encode()).hexdigest(),
            captured_log_bytes=len(log.getvalue().encode()), raw_log_not_retained=True)
    print(json.dumps(report))
    return 3 if security_stop else int(code) or int(bool(final["children"]))


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)


def host(args):
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / ".build"):
        raise ValueError("Use a new evidence directory under tests/ic1/.build")
    image = subprocess.check_output(["docker", "image", "inspect", "xoc-mcp-ic1:baseline",
                                    "--format", "{{.Id}}"], text=True).strip()
    if image != BASELINE:
        raise ValueError("Immutable baseline image differs")
    plan = []
    if args.campaign in ("r1", "all"):
        plan += [(f"R1-{mode}-{i:02}", mode) for mode in ("a", "b") for i in range(1, 11)]
    if args.campaign in ("r2", "all"):
        plan += [(f"R2-{i:02}", "ab" if i % 2 else "ba") for i in range(1, 11)]
    if args.campaign in ("r3", "all"):
        plan += [(f"R3-{mode}-{i:02}", mode) for mode in ("neighbors-a", "neighbors-b") for i in range(1, 4)]
    if args.campaign in ("r4", "all"):
        plan += [(f"R4-{i:02}", "full") for i in range(1, 6)]
    output.mkdir(parents=True)
    code_bytes = Path(__file__).read_bytes()
    write_new(output / "plan.json", dict(image=image, script_sha256=hashlib.sha256(code_bytes).hexdigest(),
        runs=plan, worker_observation=not args.no_observe, cpu=2, memory_mib=512, pids=128,
        network="none", mounts=[], fresh_tmpfs_per_run=True, stop_on_security=True))
    all_rows = []
    for run_id, mode in plan:
        name = "xoc-ic1r-" + uuid4().hex[:12]
        command = ["docker", "run", "--rm", "-i", "--name", name, "--label", "xoc.ic1r.run=" + name,
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=134217728",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "2", "--pids-limit", "128",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--platform", "linux/amd64",
            "--entrypoint", "/opt/xoc-l2/bin/python", image, "-I", "-B", "-", "--inside", mode]
        if args.no_observe:
            command.append("--no-observe")
        print(json.dumps(dict(event="run_start", run_id=run_id, mode=mode)), flush=True)
        try:
            completed = subprocess.run(command, input=code_bytes, capture_output=True, timeout=1200)
        except subprocess.TimeoutExpired:
            # Only this exact, labelled test container may be stopped; no directory deletion.
            label = subprocess.check_output(["docker", "inspect", "--format",
                '{{index .Config.Labels "xoc.ic1r.run"}}', name], text=True).strip()
            if label == name:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=True)
            write_new(output / (run_id + ".json"), dict(run_id=run_id, harness_timeout=True,
                                                      owned_container_removed=label == name))
            raise
        try:
            report = json.loads(completed.stdout)
        except ValueError:
            report = dict(runner_error="invalid_json_report", stdout_bytes=len(completed.stdout),
                          stdout_sha256=hashlib.sha256(completed.stdout).hexdigest())
        report.update(run_id=run_id, container_name=name, image=image,
            container_exit_code=completed.returncode, stderr_bytes=len(completed.stderr),
            stderr_sha256=hashlib.sha256(completed.stderr).hexdigest())
        write_new(output / (run_id + ".json"), report)
        summary = {key: report.get(key) for key in (
            "run_id", "mode", "passed", "failed", "skipped", "security_stop", "container_exit_code", "runner_error")}
        all_rows.append(summary)
        print(json.dumps(summary), flush=True)
        if report.get("security_stop") or report.get("runner_error"):
            break
    write_new(output / "summary.json", all_rows)
    return int(any(row["container_exit_code"] for row in all_rows) or len(all_rows) != len(plan))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inside", choices=("a", "b", "ab", "ba", "neighbors-a", "neighbors-b", "full"))
    p.add_argument("--no-observe", action="store_true")
    p.add_argument("--campaign", choices=("r1", "r2", "r3", "r4", "all"))
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if args.inside:
        return inside(args.inside, not args.no_observe)
    if not args.output or not args.campaign:
        p.error("--output and --campaign are required on the host")
    return host(args)


if __name__ == "__main__":
    raise SystemExit(main())
