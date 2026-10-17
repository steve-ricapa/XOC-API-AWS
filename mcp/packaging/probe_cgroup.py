"""Read-only local container resource diagnostics, no environment/secrets dump."""
from pathlib import Path
import json
paths = ("/proc/self/cgroup", "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.events",
    "/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    "/sys/fs/cgroup/memory/memory.failcnt", "/sys/fs/cgroup/memory/memory.oom_control",
    "/sys/fs/cgroup/cpuacct/cpuacct.usage", "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",
    "/sys/fs/cgroup/cpu/cpu.cfs_period_us", "/sys/fs/cgroup/cpu.stat")
print(json.dumps({p: Path(p).read_text() if Path(p).is_file() else None for p in paths}, indent=2))
