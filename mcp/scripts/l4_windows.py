"""Run the unchanged Windows laboratory harness; persist only sanitized evidence.

Prepared separately from the Linux benchmark. This script never installs packages,
changes source, invokes Git, or writes raw subprocess output to disk.
"""
from __future__ import annotations

import datetime
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


LAB = Path(__file__).resolve().parents[1]
OUTPUT = LAB / "reports" / "l4-windows-final.json"
COUNT_NAMES = ("passed", "failed", "skipped", "xfailed", "xpassed", "deselected", "errors", "warnings")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_summary(raw):
    """Recognize only pytest's final numeric summary, never test/error text."""
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    for line in reversed(text.splitlines()):
        duration = re.search(r"\bin ([0-9]+(?:\.[0-9]+)?)s\b", line)
        matches = re.findall(r"\b([0-9]+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?|warnings?)\b", line)
        if duration and matches:
            counts = dict.fromkeys(COUNT_NAMES, 0)
            for amount, category in matches:
                counts[{"error": "errors", "warning": "warnings"}.get(category, category)] += int(amount)
            return dict(summary_found=True, counts=counts, pytest_reported_seconds=float(duration.group(1)),
                        sanitized_summary=", ".join(f"{counts[name]} {name}" for name in COUNT_NAMES if counts[name]))
    return dict(summary_found=False, counts=None, pytest_reported_seconds=None,
                sanitized_summary="LAB_PYTEST_SUMMARY_NOT_FOUND")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(LAB / 'reports'):
        raise RuntimeError('LAB_REPORT_PATH_REQUIRED')
    if os.name != "nt":
        raise RuntimeError("LAB_WINDOWS_RUNNER_REQUIRED")
    python = (LAB / ".venv" / "Scripts" / "python.exe").resolve(strict=True)
    runner = (LAB / "run_tests.py").resolve(strict=True)
    backend = (LAB.parent / "XOC-API-AWS").resolve(strict=True)
    if Path(sys.executable).resolve() != python or backend.name != "XOC-API-AWS":
        raise RuntimeError("LAB_EXACT_WINDOWS_INTERPRETER_AND_REVIEWED_BACKEND_REQUIRED")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise RuntimeError("LAB_NEW_REPORT_REQUIRED")

    # The child immediately enters the original Isolation as well. Do not inherit
    # credentials, profiles, proxies, PYTHONPATH or extra pytest configuration.
    sys.path.insert(0, str(LAB))
    from xoc_mcp_lab.isolation import clean_environment

    inputs = {name: sha256(LAB / name) for name in
              ("run_tests.py", "requirements.lock.txt", "compatibility.json", "scripts/l4_windows.py")}
    command = [str(python), "-B", str(runner), "--backend", str(backend)]
    result = dict(lab_only=True, mode="windows_original_run_tests",
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  command=command, inputs_sha256=inputs, interpreter_sha256=sha256(python),
                  raw_output_persisted=False, original_runner_modified=False)
    # Exclusive creation prevents replacing an earlier result, including races.
    with output.open("x", encoding="utf-8", newline="\n") as report:
        start = time.perf_counter()
        try:
            completed = subprocess.run(command, cwd=LAB, env=clean_environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False)
            raw = completed.stdout
            result.update(exit_code=completed.returncode, duration_seconds=time.perf_counter()-start,
                          output_bytes=len(raw), stdout_stderr_sha256=hashlib.sha256(raw).hexdigest(),
                          **safe_summary(raw))
            del raw
            result["status"] = "passed" if completed.returncode == 0 and result["summary_found"] else "failed"
        except OSError as error:
            result.update(exit_code=2, duration_seconds=time.perf_counter()-start,
                          status="launch_failed", exception_type=type(error).__name__,
                          summary_found=False, counts=None)
        result["runner_sha256_after"] = sha256(runner)
        result["original_runner_modified"] = result["runner_sha256_after"] != inputs["run_tests.py"]
        result["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if result["original_runner_modified"]:
            result["status"] = "source_changed"
        json.dump(result, report, ensure_ascii=True, indent=2, allow_nan=False)
        report.write("\n")
    print(json.dumps({key: result.get(key) for key in
        ("status", "exit_code", "counts", "duration_seconds", "stdout_stderr_sha256")}, sort_keys=True))
    return result["exit_code"] if result["status"] == "passed" else result["exit_code"] or 1


if __name__ == "__main__":
    raise SystemExit(main())
