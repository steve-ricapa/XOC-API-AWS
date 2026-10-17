"""Local read-only cgroup v1/v2 metrics; missing values remain null, never zero."""
from pathlib import Path


def number(*paths):
    for path in paths:
        try: return int(Path(path).read_text().strip())
        except (OSError, ValueError): pass
    return None


def pairs(*paths):
    for path in paths:
        try: return {k: int(v) for k, v in (line.split() for line in Path(path).read_text().splitlines())}
        except (OSError, ValueError): pass
    return None


def snapshot():
    usage_v2 = pairs("/sys/fs/cgroup/cpu.stat")
    nanos = number("/sys/fs/cgroup/cpuacct/cpuacct.usage")
    return dict(memory_current_bytes=number("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        memory_limit_bytes=number("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        memory_events=pairs("/sys/fs/cgroup/memory.events", "/sys/fs/cgroup/memory/memory.oom_control"),
        memory_failcnt=number("/sys/fs/cgroup/memory/memory.failcnt"),
        cpu_seconds=nanos / 1e9 if nanos is not None else usage_v2.get("usage_usec", 0) / 1e6 if usage_v2 else None,
        cpu_quota_us=number("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
        cpu_period_us=number("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
