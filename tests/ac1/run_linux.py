"""Retain a Linux test result from a local image, without credentials or mounts."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('new evidence required')
    image = subprocess.check_output(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'], text=True).strip()
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=134217728', '--memory', '512m', '--memory-swap', '512m',
        '--cpus', '2', '--pids-limit', '128', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        image], capture_output=True, text=True, timeout=60)
    # run.py prints only the summary on success; retain diagnostics on failure.
    report = json.loads(result.stdout.splitlines()[0])
    report.update(image=image, exit_code=result.returncode, stderr=result.stderr)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'stderr'}))
    return result.returncode


if __name__ == '__main__': raise SystemExit(main())
