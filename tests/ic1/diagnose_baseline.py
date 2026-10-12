"""Read-only diagnostics of the immutable baseline's two intermittent stdio tests.

This is not an IC-1 acceptance replacement. Keep every complete-suite result.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def inside():
    import contextlib
    import io
    import logging
    from xoc_mcp_lab.isolation import Isolation
    with Isolation():
        logging.disable(logging.CRITICAL)
        from xoc_mcp_lab.installed import require_installed
        from xoc_mcp_lab import testing
        from xoc_mcp_lab.query_worker import QueryWorker
        import xoc_mcp_lab_harness.linux_validation as validation
        import pytest
        testing.BACKEND = require_installed()
        root = Path(validation.__file__).parent / "tests"
        observations = []
        original = QueryWorker.run

        async def observed(self, *args, **kwargs):
            result = await original(self, *args, **kwargs)
            observations.append(dict(status=result.get("status"), code=result.get("code"),
                                     measurements=self.measurements[-1:], lifecycle=self.lifecycle[-1:]))
            return result

        # Observes outcomes only; no timeout, fixture, result or assertion changes.
        QueryWorker.run = observed
        rounds = []
        for number in range(5):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = pytest.main(["-q", "-p", "no:cacheprovider",
                    str(root / "test_l2_lifecycle.py") + "::test_concurrent_calls_same_instance_only_one_child",
                    str(root / "test_worker_lifecycle.py") + "::test_inherited_secrets_never_used"])
            rounds.append(dict(round=number + 1, exit_code=int(code)))
        import os
        children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip()
        print(json.dumps(dict(rounds=rounds, observations=observations, children=children)))
        return int(bool(children) or any(row["exit_code"] for row in rounds))


def main():
    if "--inside" in sys.argv:
        return inside()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    target = parser.parse_args().output.resolve()
    if target.exists() or not target.is_relative_to(Path(__file__).resolve().parent / ".build"):
        raise ValueError("Use a new evidence file under tests/ic1/.build")
    image = subprocess.check_output(["docker", "image", "inspect", "xoc-mcp-ic1:baseline",
                                     "--format", "{{.Id}}"], text=True).strip()
    command = ["docker", "run", "--rm", "-i", "--name", "xoc-ic1-baseline-diag-" + uuid4().hex[:12],
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=134217728",
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "2", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--platform", "linux/amd64",
        "--entrypoint", "/opt/xoc-l2/bin/python", image, "-I", "-B", "-", "--inside"]
    result = subprocess.run(command, input=Path(__file__).read_bytes(), capture_output=True, timeout=120)
    report = json.loads(result.stdout)
    report.update(image=image, exit_code=result.returncode,
                  stderr_bytes=len(result.stderr), stderr_sha256=hashlib.sha256(result.stderr).hexdigest())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
