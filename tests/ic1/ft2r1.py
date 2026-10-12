"""Fixed, sequential U1/U2 population. All outcomes retained, no retries."""
import argparse
import json
from pathlib import Path
import subprocess
from ft2r import BASELINE, invoke


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", choices=("U1", "U2", "original", "guarded", "recovery-original"), required=True)
    parser.add_argument("--image", default="xoc-chat-mcp-ft2r1:diagnostic")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists() or not target.is_relative_to(Path(__file__).resolve().parent / ".build"):
        raise ValueError("FT2R1_NEW_OUTPUT_REQUIRED")
    if subprocess.check_output(["docker", "ps", "-q"], text=True).strip():
        raise RuntimeError("FT2R1_CONCURRENT_CONTAINER_STOP")
    baseline = subprocess.check_output(["docker", "image", "inspect", "xoc-mcp-ic1:baseline", "--format", "{{.Id}}"], text=True).strip()
    if baseline != BASELINE:
        raise RuntimeError("FT2R1_BASELINE_DRIFT")
    image = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    target.mkdir(parents=True)
    count, mode = {"U1":(30,"natural"), "U2":(20,"natural-recovery"), "original":(30,"original"),
                   "guarded":(30,"guarded"), "recovery-original":(20,"recovery-original")}[args.population]
    for index in range(1, count+1):
        row = invoke(image, mode, target / f"run-{index:02}.json")
        print(json.dumps(dict(population=args.population, ordinal=index, diagnostic_only=mode.startswith("natural"),
              initial_complete=row.get("initial_ready"), replacement_requested="recovery_requested" in row)), flush=True)


if __name__ == "__main__":
    main()
