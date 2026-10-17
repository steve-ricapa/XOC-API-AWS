"""Test-only instrumentation of the pinned SDK; never modifies the installed SDK."""
import json
import os
from pathlib import Path
import time
from unittest.mock import patch
from mcp.client import stdio


class ProcessObserver:
    def __init__(self, fault=None):
        self.processes = []
        self.fault = fault
        self.stream_patches = []
        self.bytes = {}
        self.groups = {}

    def __enter__(self):
        original = stdio._create_platform_compatible_process
        async def launch(**kwargs):
            if self.fault:
                kwargs["args"] = ["-B", "-c", self.fault]
            started = time.monotonic()
            process = await original(**kwargs)
            self.processes.append((process, started))
            self.bytes[process.pid] = {"stdin": 0, "stdout": 0}
            self.groups[process.pid] = os.getpgid(process.pid) if os.name == "posix" else None
            for stream, method, key in ((process.stdin, "send", "stdin"), (process.stdout, "receive", "stdout")):
                if stream is None: continue
                original_method = getattr(stream, method)
                async def counted(*a, _original=original_method, _key=key, **kw):
                    result = await _original(*a, **kw)
                    self.bytes[process.pid][_key] += len(a[0] if _key == "stdin" else result)
                    return result
                p = patch.object(stream, method, counted)
                p.start()
                self.stream_patches.append(p)
            return process
        self.patch = patch.object(stdio, "_create_platform_compatible_process", launch)
        self.patch.start()
        return self

    def evidence(self):
        rows = []
        for process, started in self.processes:
            reaped = process.returncode is not None
            # waitpid is authoritative for OUR direct children on Linux; no kill by name.
            waitpid_not_child = None
            if os.name == "posix" and reaped:
                try:
                    os.waitpid(process.pid, os.WNOHANG)
                    waitpid_not_child = False  # observer must not be the reaper
                except ChildProcessError:
                    waitpid_not_child = True
            rows.append(dict(pid=process.pid, returncode=process.returncode,
                reaped=reaped, waitpid_not_child=waitpid_not_child,
                process_group=self.groups[process.pid], wire_bytes=self.bytes[process.pid],
                proc_entry_exists=Path(f"/proc/{process.pid}").exists() if os.name == "posix" else None))
        return rows

    def __exit__(self, *exc):
        self.patch.stop()
        for p in reversed(self.stream_patches): p.stop()
        evidence = self.evidence()
        assert all(row["reaped"] for row in evidence), "LAB_CHILD_NOT_REAPED"
        if os.name == "posix":
            assert all(row["waitpid_not_child"] for row in evidence), "LAB_ZOMBIE_NOT_REAPED"


def audit_events(log):
    log.flush()
    log.seek(0)
    text = log.read()
    assert "LAB_CANARY" not in text and "DO_NOT_LOG_THIS" not in text
    return [json.loads(line.split("lab_call ", 1)[1]) for line in text.splitlines() if "lab_call " in line]
