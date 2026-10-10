"""Run only the explicit IC-1 local image, retain sanitized evidence, no host mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    target = args.output.resolve()
    root = Path(__file__).resolve().parent / ".build"
    if not target.is_relative_to(root) or target.exists():
        raise ValueError("Use a new evidence file under backend/tests/ic1/.build")
    image = subprocess.check_output(["docker", "image", "inspect", "xoc-chat-mcp-ic1:local", "--format", "{{.Id}}"], text=True).strip()
    command = ["docker", "run", "--rm", "--name", "xoc-chat-ic1-"+uuid4().hex[:12],
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=134217728",
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "2", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--platform", "linux/amd64", image]
    result = subprocess.run(command, capture_output=True, timeout=180)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = {"passed": False, "runner_error": "invalid_report"}
    report.update(image=image, exit_code=result.returncode,
                  stderr_sha256=hashlib.sha256(result.stderr).hexdigest(), stderr_bytes=len(result.stderr))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("passed", "tests", "failures", "errors", "details", "exit_code", "image") if k in report}, indent=2))
    return result.returncode or int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
