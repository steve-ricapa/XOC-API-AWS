"""Host runner for local FT-2. New evidence only, exact image, no host mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("ic1", "tests", "smoke", "recovery", "provenance", "control", "guard"))
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--image", default="xoc-chat-mcp-ic1:local")
    args = p.parse_args()
    target = args.output.resolve()
    if target.exists() or not target.is_relative_to(Path(__file__).resolve().parent / ".build"):
        raise ValueError("FT_NEW_EVIDENCE_REQUIRED")
    image = subprocess.check_output(["docker", "image", "inspect", args.image,
                                    "--format", "{{.Id}}"], text=True).strip()
    command = ["docker", "run", "--rm", "--name", "xoc-ft-"+uuid4().hex[:12],
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=134217728",
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "2", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--platform", "linux/amd64",
        "--entrypoint", "/opt/xoc-l2/bin/python", image, "-I", "-B"]
    command += (["/ic1/harness/run.py"] if args.mode == "ic1" else
                ["/ic1/harness/http_regression.py", args.mode])
    result = subprocess.run(command, capture_output=True, timeout=600)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = dict(passed=False, error="invalid_report", stderr=result.stderr.decode(errors="replace"))
    report.update(image=image, exit_code=result.returncode,
                  stderr_sha256=hashlib.sha256(result.stderr).hexdigest())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = {k: report[k] for k in ("passed", "mode", "total", "failures", "errors", "error",
        "exit_code", "http_tool_calls", "checks", "detected_attempts") if k in report}
    if "no_stdio" in report:
        summary["no_stdio"] = {k: v for k, v in report["no_stdio"].items() if k != "events"}
    print(json.dumps(summary, indent=2))
    return result.returncode or int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
