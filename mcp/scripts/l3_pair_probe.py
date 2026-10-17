"""Supplemental alternating 3.5s probes, using ONLY installed immutable images.

No build/download, no sources mounted, no local Python dependencies. Reuses the
same packaged L2 sample/observer for both sides. Docker startup is not worker time.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

LAB = Path(__file__).resolve().parents[1]
IMAGES = {
    "baseline": "sha256:54ec2ff0f4762dac8781a86606686804e18f5f14044492537eeb8f00f8c5254a",
    "candidate": "sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40",
}
PROGRAM = '''import asyncio,json,os,sys
from pathlib import Path
from xoc_mcp_lab.isolation import Isolation,configure_logs
with Isolation():
 configure_logs()
 from xoc_mcp_lab_harness.benchmark import sample
 row=asyncio.run(sample(scenario=sys.argv[1],timeout=3.5,instrumented=False,require_success=False))
children=Path('/proc/%s/task/%s/children'%(os.getpid(),os.getpid())).read_text().strip()
assert not children,'LAB_CHILDREN_REMAIN'
row['checked_children_before_exit']=children
print(json.dumps(row))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,40}", args.run_id):
        raise ValueError("Invalid run ID")
    output = LAB / "l3-runs" / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for scenario, count in (("normal", 5), ("blocked", 2), ("late", 2)):
        for index in range(count):
            for label, image in IMAGES.items():
                name = f"xoc-l3-{args.run_id}-{len(rows)}"
                command = ["docker", "run", "--rm", "--name", name,
                    "--platform", "linux/amd64", "--memory", "512m", "--cpus", "2", "--pids-limit", "128",
                    "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "--entrypoint", "/opt/xoc-l2/bin/python",
                    image, "-I", "-B", "-c", PROGRAM, scenario]
                started = time.monotonic()
                # No automatic destructive cleanup if the daemon hangs: report the
                # exact own container name; never kill/prune unrelated containers.
                process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                external = time.monotonic()-started
                if process.returncode:
                    safe = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED_FIXTURE]", process.stderr)
                    with (output / f"error-{len(rows)}.txt").open("x", encoding="utf-8") as stream: stream.write(safe)
                    raise RuntimeError(f"Probe failed in {name}: exit {process.returncode}")
                row = dict(label=label, scenario=scenario, pair_index=index, image=image,
                    docker_and_parent_wall_seconds=external, sample=json.loads(process.stdout))
                rows.append(row)
                with (output / f"sample-{len(rows):02d}.json").open("x", encoding="utf-8") as stream:
                    json.dump(row, stream, indent=2)
                print(json.dumps(dict(label=label, scenario=scenario, total=row["sample"]["total_seconds"],
                    status=row["sample"]["status"], handlers=row["sample"]["handlers"])), flush=True)
    evidence = dict(lab_only=True, total_budget_requested_seconds=3.5, images=IMAGES, rows=rows,
        program_sha256=hashlib.sha256(PROGRAM.encode()).hexdigest(),
        method="Alternating baseline/candidate; 5 normal, 2 blocked, 2 late each. Same installed sample/fixtures/limits. No mounts. External Docker wall time is separate, not Lambda cold start.")
    with (output / "paired-results.json").open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)


if __name__ == "__main__": main()
