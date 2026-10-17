"""DIAGNOSTIC ONLY: cProfile of the real entry point, no replacement handlers.

Stdout remains MCP. Only timings/code names are emitted to stderr; never locals,
arguments, tokens or returned business objects. No installed file is modified.
"""
import sys
import time

FIRST_NS = time.perf_counter_ns()
import cProfile
import json


def main():
    profile = cProfile.Profile()
    profile.enable()
    try:
        from xoc_mcp_lab.verified_server import main as original_main
        return original_main()
    finally:
        profile.disable()
        entries = []
        for entry in profile.getstats():
            code = entry.code
            if isinstance(code, str):
                continue
            path = code.co_filename.replace("\\", "/")
            if "/site-packages/" in path:
                path = path.split("/site-packages/", 1)[1]
            elif "/python3.11/" in path:
                path = "stdlib/" + path.split("/python3.11/", 1)[1]
            else:
                path = path.rsplit("/", 1)[-1]
            entries.append(dict(file=path, function=code.co_name, line=code.co_firstlineno,
                calls=entry.callcount, recursive_calls=entry.reccallcount,
                inclusive_seconds=entry.totaltime, exclusive_seconds=entry.inlinetime))
        print("lab_l3_profile " + json.dumps(dict(first_python_marker_ns=FIRST_NS,
            last_python_marker_ns=time.perf_counter_ns(), clock=time.get_clock_info("perf_counter").implementation,
            entries=entries, inclusive_spans_not_additive=True)), file=sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
