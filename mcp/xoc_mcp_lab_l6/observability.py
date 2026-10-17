"""Harness-only deterministic /proc capture; never controls a server lifecycle."""
import time
from pathlib import Path

from xoc_mcp_lab_l4.harness import process_resources

FIELDS = ('VmRSS', 'VmHWM', 'cpu_ticks', 'Threads', 'tasks', 'fds')
POINTS = ('after_ready', 'after_first_cold_query', 'before_retirement')


def identity(pid):
    try:
        parts = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        return dict(pid=pid, ppid=int(parts[1]), start_ticks=int(parts[19]), state=parts[0])
    except (OSError, ValueError, IndexError):
        return None


def capture(pid, generation, owner_pid, expected, point, returncode=None):
    if point not in POINTS:
        raise ValueError('Unknown measurement point')
    began = time.monotonic()
    before = identity(pid)
    owned = bool(before and expected and before['ppid'] == owner_pid
                 and all(before[k] == expected[k] for k in ('pid', 'ppid', 'start_ticks')))
    alive = bool(owned and before['state'] not in ('Z', 'X'))
    raw = process_resources(pid) if alive else {}
    after = identity(pid)
    stable = bool(alive and after and after['state'] not in ('Z', 'X')
                  and all(after[k] == before[k] for k in ('pid', 'ppid', 'start_ticks')))
    reasons = {}
    metrics = {'pid': pid}
    for field in FIELDS:
        value = raw.get(field) if stable else None
        metrics[field] = value
        if value is None:
            reasons[field] = ('process_exited_during_measurement' if alive and not after else
                             'process_identity_not_verified' if not stable else
                             'proc_source_unavailable')
    exited = before is None or (before and before['state'] in ('Z', 'X')) or (alive and after is None)
    return dict(measurement_point=point, monotonic_at=began, finished_at=time.monotonic(),
                pid=pid, generation=generation, owner_pid=owner_pid,
                expected_identity=expected, observed_identity=before,
                ownership_verified=owned, identity_stable=stable, process_alive=stable,
                process_exited_before_measurement=bool(exited and not alive),
                returncode=returncode, returncode_reason=None if returncode is not None else 'not_observed',
                server=metrics, unavailable_reasons=reasons,
                # Container data is explicitly NOT a substitute for process values.
                cgroup=raw.get('cgroup'))
