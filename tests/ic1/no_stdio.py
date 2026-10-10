"""Test-only tripwire propagated to the real L6 supervisor and server processes.

Never changes installed MCP code, timeouts, results or cleanup. Importing any
legacy worker lifecycle fails the run, as does launching an unapproved child.
The journal contains only fixed event/module names and local process IDs.
"""
import json
import importlib.abc
import os
from pathlib import Path
import runpy
import subprocess
import sys

FORBIDDEN = frozenset(("xoc_mcp_lab.query_worker", "xoc_mcp_lab.budget",
                       "xoc_mcp_lab.verified_server"))
ALLOWED = frozenset(("xoc_mcp_lab_l6.supervisor", "xoc_mcp_lab_l6.server"))
_installed = False


def install(directory):
    global _installed
    if _installed:
        return
    _installed = True
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / (str(os.getpid()) + ".jsonl")

    def record(event, module=None):
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(event=event, pid=os.getpid(), module=module)) + "\n")

    record("guard_installed")
    if FORBIDDEN.intersection(sys.modules):
        record("violation_preloaded")
        raise RuntimeError("FT_STDIO_PRELOADED")

    def audit(event, args):
        if event == "import" and args[0] in FORBIDDEN:
            record("violation_import", args[0])
            raise RuntimeError("FT_STDIO_FORBIDDEN")

    sys.addaudithook(audit)
    class NoLegacyFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in FORBIDDEN:
                record("violation_import", fullname)
                raise RuntimeError("FT_STDIO_FORBIDDEN")
            return None
    sys.meta_path.insert(0, NoLegacyFinder())
    original = subprocess.Popen

    def instrument(argv):
        if (not isinstance(argv, list) or len(argv) < 5
                or argv[1:4] != ["-I", "-B", "-m"] or argv[4] not in ALLOWED):
            record("violation_spawn")
            raise RuntimeError("FT_NON_HTTP_CHILD_FORBIDDEN")
        module = argv[4]
        record("http_process_spawn", module)
        bootstrap = ("import sys; sys.path.insert(0, '/ic1/harness'); "
                     "from no_stdio import launch; launch(" + repr(str(directory)) + ", "
                     + repr(module) + ")")
        return [argv[0], "-I", "-B", "-c", bootstrap, *argv[5:]]

    class HTTPOnlyPopen(original):
        def __init__(self, argv, *args, **kwargs):
            super().__init__(instrument(argv), *args, **kwargs)

    subprocess.Popen = HTTPOnlyPopen


def launch(directory, module):
    install(directory)
    try:
        runpy.run_module(module, run_name="__main__")
    except Exception as error:
        import traceback
        with (Path(directory) / (str(os.getpid()) + ".jsonl")).open("a") as stream:
            stream.write(json.dumps(dict(event="bootstrap_error", pid=os.getpid(),
                category=type(error).__name__, frames=[dict(file=Path(f.filename).name, line=f.lineno)
                for f in traceback.extract_tb(error.__traceback__)])) + "\n")
        raise


def evidence(directory):
    rows = [json.loads(line) for path in sorted(Path(directory).glob("*.jsonl"))
            for line in path.read_text().splitlines()]
    violations = [r for r in rows if r["event"].startswith("violation") or r["event"] == "bootstrap_error"]
    processes = len({r["pid"] for r in rows if r["event"] == "guard_installed"})
    return dict(passed=not violations and processes >= 3, guarded_processes=processes,
                query_worker_calls=0 if not violations else None,
                process_owner_spawns=0 if not violations else None,
                verified_server_launches=0 if not violations else None,
                violations=violations, events=rows)
