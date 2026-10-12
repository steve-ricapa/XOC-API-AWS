"""Test-only startup observer. Does not patch budgets, policy or decisions.

Diagnostic children import the original installed module then call its main;
pure original/guarded populations never load this module. Journals are tmpfs,
bounded, technical-only, and independent of the canonical control protocol.
"""
import time
ENTRY = time.monotonic()
import os
import sys
import json
import subprocess
import importlib
import importlib._bootstrap as bootstrap

ROLE = "harness"
GENERATION = None
FD = None
COUNT = 0
COUNTERS = {}
PATCHED = set()
ACTIVE = True
ISOLATIONS = []
PREFIXES = ("xoc_mcp_lab", "src.", "mcp", "anyio", "httpx", "httpcore",
            "boto3", "botocore", "sqlalchemy", "uvicorn", "pydantic", "starlette")


def emit(event, **fields):
    global FD, COUNT
    if COUNT >= 12000:
        return
    if FD is None:
        os.makedirs("/tmp/ft2r-probe", exist_ok=True)
        FD = os.open(f"/tmp/ft2r-probe/{os.getpid()}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    row = dict(event=event, at=time.monotonic(), cpu=time.process_time(),
               pid=os.getpid(), ppid=os.getppid(), role=ROLE, generation=GENERATION, **fields)
    os.write(FD, (json.dumps(row, separators=(",", ":")) + "\n").encode())
    COUNT += 1


def span(owner, name, label):
    original = getattr(owner, name)
    def wrapped(*args, **kwargs):
        emit(label + "_begin")
        try:
            return original(*args, **kwargs)
        finally:
            emit(label + "_end")
    setattr(owner, name, wrapped)


def async_span(owner, name, label):
    original = getattr(owner, name)
    async def wrapped(*args, **kwargs):
        emit(label + "_begin")
        try:
            return await original(*args, **kwargs)
        finally:
            emit(label + "_end")
    setattr(owner, name, wrapped)


def patch_loaded(name, module):
    if name in PATCHED:
        return
    PATCHED.add(name)
    if name == "xoc_mcp_lab.isolation":
        original = module.Isolation.__enter__
        def entered(self):
            value = original(self)
            ISOLATIONS.append(self)
            return value
        module.Isolation.__enter__ = entered
    elif name == "xoc_mcp_lab_l4.wiring":
        span(module.PersistentWiring, "__enter__", "S06_wiring")
    elif name == "xoc_mcp_lab_l6.server":
        span(module, "create_app", "S05_app")
    elif name == "xoc_mcp_lab_l6.integrity":
        span(module, "require_l6_installed", "integrity_l6")
    elif name == "xoc_mcp_lab_l4.integrity":
        span(module, "require_l4_installed", "integrity_l4")
    elif name == "xoc_mcp_lab.installed":
        span(module, "require_installed", "integrity_core")
    elif name == "uvicorn.server":
        async_span(module.Server, "startup", "S07_transport")
    elif name == "asyncio.base_events":
        async_span(module.BaseEventLoop, "create_server", "S07_bind")
    elif name == "importlib.metadata":
        span(module, "distribution", "metadata_distribution")
    elif name == "ssl":
        span(module, "create_default_context", "certificate_context")
    elif name == "urllib.request":
        span(module, "getproxies", "proxy_discovery")
    elif name == "xoc_mcp_lab_l6.control":
        original = module.ControlChannel.send
        seen = set()
        def send(self, kind, **fields):
            global ACTIVE
            result = original(self, kind, **fields)
            if ROLE == "server" and kind in ("hello", "heartbeat", "fault", "ack") and kind not in seen:
                seen.add(kind)
                emit("S08_control_" + kind)
                emit("io_counters", counts=dict(COUNTERS),
                     external_blocked=sum(x.network_attempts for x in ISOLATIONS),
                     secrets_blocked=sum(x.secret_file_attempts for x in ISOLATIONS))
                if kind == "hello":
                    ACTIVE = False
            return result
        module.ControlChannel.send = send
    elif name == "xoc_mcp_lab_l6.supervisor":
        original = module.Supervisor._emit
        def observed(self, kind, **fields):
            if kind == "ready":
                emit("S09_ready", child_pid=fields.get("child_pid"), child_generation=self.generation)
            return original(self, kind, **fields)
        module.Supervisor._emit = observed


def imports():
    original = bootstrap._find_and_load
    def observed(name, import_):
        fresh = name not in sys.modules
        relevant = fresh and name.startswith(PREFIXES)
        began, cpu = (time.monotonic(), time.process_time()) if relevant and ACTIVE else (None, None)
        if relevant and ACTIVE:
            emit("import_begin", module=name)
        outcome = "completed"
        try:
            result = original(name, import_)
            patch_loaded(name, result)
            return result
        except BaseException as error:
            outcome = type(error).__name__
            raise
        finally:
            if relevant and ACTIVE and began is not None:
                emit("import_end", module=name, began=began, elapsed=time.monotonic()-began,
                     cpu_elapsed=time.process_time()-cpu, outcome=outcome)
    bootstrap._find_and_load = observed


def io_observer():
    def audit(event, args):
        if not ACTIVE:
            return
        category = None
        if event == "open" and args and isinstance(args[0], str):
            value = args[0]
            category = ("file_metadata" if ".dist-info/" in value else
                        "file_certificate" if "/cert" in value else
                        "file_temporary" if value.startswith("/tmp/") else "file_other")
        elif event in ("os.listdir", "os.scandir"):
            category = "directory_scan"
        elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.connect", "socket.bind"):
            category = event
            # No addresses, credentials or arguments retained.
            if event != "socket.bind":
                emit("network_attempt", operation=event)
        if category:
            COUNTERS[category] = COUNTERS.get(category, 0) + 1
    sys.addaudithook(audit)
    original = os.urandom
    def random(size):
        began = time.monotonic()
        result = original(size)
        if ACTIVE:
            emit("entropy", elapsed=time.monotonic()-began)
        return result
    os.urandom = random


def install_parent():
    original = subprocess.Popen
    class ObservedPopen(original):
        def __init__(self, args, *pos, **kwargs):
            argv = list(args) if isinstance(args, (list, tuple)) else None
            observed = bool(argv and argv[1:4] == ["-I", "-B", "-m"] and
                            argv[4] in ("xoc_mcp_lab_l6.supervisor", "xoc_mcp_lab_l6.server"))
            if observed:
                generation = int(argv[argv.index("--generation")+1]) if "--generation" in argv else 0
                emit("S01_spawn", child_generation=generation, child_role=argv[4].rsplit(".", 1)[-1])
                argv = [argv[0], "-I", "-B", "/ic1/harness/ft2r_probe.py", argv[4], *argv[5:]]
            super().__init__(argv if observed else args, *pos, **kwargs)
            if observed:
                emit("S02_observed", child_pid=self.pid, child_generation=generation)
    subprocess.Popen = ObservedPopen


def main():
    global ROLE, GENERATION
    name = sys.argv[1]
    if name not in ("xoc_mcp_lab_l6.supervisor", "xoc_mcp_lab_l6.server"):
        raise ValueError("FT2R_INVALID_MODULE")
    ROLE = name.rsplit(".", 1)[-1]
    GENERATION = int(sys.argv[sys.argv.index("--generation")+1]) if "--generation" in sys.argv else 0
    emit("S03_entry", entry_at=ENTRY, nearest="first_time_import_after_python_boot")
    install_parent()
    imports()
    io_observer()
    emit("S04_l6_import_begin")
    module = importlib.import_module(name)
    emit("S04_l6_import_end")
    return module.main(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
