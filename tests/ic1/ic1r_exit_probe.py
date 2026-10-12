"""Observational child-exit probe; not an acceptance test or a baseline fix.

Execute the same verified_server main, reporting only termination/GC phases.
No deadline, return value, signal handling, dependency or assertion is changed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

CHILD = r'''
import atexit, gc, json, os, time
def emit(event, generation=None):
    os.write(2, ("ic1r_exit_phase " + json.dumps(dict(event=event,
        monotonic=time.monotonic(), generation=generation)) + "\n").encode())
atexit.register(lambda: emit("atexit"))
def observe_gc(phase, info):
    emit("gc_" + phase, info.get("generation"))
from xoc_mcp_lab.verified_server import main
try:
    raise SystemExit(main())
finally:
    emit("verified_main_returned")
    gc.callbacks.append(observe_gc)
'''


def inside(profile):
    import asyncio
    import tempfile
    from unittest.mock import patch
    from xoc_mcp_lab.isolation import Isolation, configure_logs
    with Isolation():
        configure_logs()
        from xoc_mcp_lab.installed import require_installed
        from xoc_mcp_lab.query_worker import QueryWorker
        from xoc_mcp_lab_harness.signing import envelope
        from xoc_mcp_lab_harness.observe import ProcessObserver
        from mcp.client import stdio
        original = stdio._create_platform_compatible_process
        async def launch(**kwargs):
            if profile:
                assert kwargs["args"][:3] == ["-B", "-m", "xoc_mcp_lab.verified_server"]
                kwargs["args"] = ["-B", "-c", CHILD, *kwargs["args"][3:]]
            return await original(**kwargs)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
            with patch.object(stdio, "_create_platform_compatible_process", launch), ProcessObserver() as observer:
                worker = QueryWorker()
                value = asyncio.run(worker.run(envelope(), require_installed(), errlog=log))
                children = observer.evidence()
            log.seek(0)
            phases = []
            handler_calls = []
            for line in log:
                if line.startswith("ic1r_exit_phase "):
                    phases.append(json.loads(line.split(" ", 1)[1]))
                elif "lab_call " in line:
                    audit = json.loads(line.split("lab_call ", 1)[1])
                    handler_calls.append(audit.get("handler_calls"))
            report = dict(profile=profile, status=value.get("status"), code=value.get("code"),
                marks=worker.measurements, lifecycle=worker.lifecycle, children=children,
                child_phases=phases, handler_calls=handler_calls)
        print(json.dumps(report))
        return 0  # Diagnostic records failures; it never claims acceptance success.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", choices=("profile", "control"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.inside:
        return inside(args.inside == "profile")
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / ".build"):
        raise ValueError("New evidence directory required")
    image = subprocess.check_output(["docker", "image", "inspect", "xoc-mcp-ic1:baseline",
                                    "--format", "{{.Id}}"], text=True).strip()
    assert image == "sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4"
    output.mkdir(parents=True)
    script = Path(__file__).read_bytes()
    for index in range(10):
        mode = "control" if index % 2 == 0 else "profile"
        name = "xoc-ic1r-exit-" + uuid4().hex[:12]
        command = ["docker", "run", "--rm", "-i", "--name", name, "--label", "xoc.ic1r.run=" + name,
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=134217728",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "2", "--pids-limit", "128",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--platform", "linux/amd64",
            "--entrypoint", "/opt/xoc-l2/bin/python", image, "-I", "-B", "-", "--inside", mode]
        try:
            result = subprocess.run(command, input=script, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            label = subprocess.check_output(["docker", "inspect", "--format",
                '{{index .Config.Labels "xoc.ic1r.run"}}', name], text=True).strip()
            if label == name:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=True)
            with (output / f"exit-{index+1:02}.json").open("x", encoding="utf-8") as stream:
                json.dump(dict(harness_timeout=True, owned_container_removed=label == name), stream)
            raise
        try:
            report = json.loads(result.stdout)
        except ValueError:
            report = dict(error="invalid_report", stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        report.update(index=index + 1, image=image, script_sha256=hashlib.sha256(script).hexdigest(),
            exit_code=result.returncode, stderr_bytes=len(result.stderr),
            stderr_sha256=hashlib.sha256(result.stderr).hexdigest())
        with (output / f"exit-{index+1:02}.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps({k: report.get(k) for k in ("index", "profile", "status", "code", "exit_code", "error")}), flush=True)
        if result.returncode or "error" in report:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
