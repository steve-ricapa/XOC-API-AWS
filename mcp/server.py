"""Only stdio, only explicitly selected fictional profiles. No environment loading."""
import argparse
import asyncio
import os
from pathlib import Path
import sys
import threading
from xoc_mcp_lab.isolation import Isolation, LAB_ROOT, backend_path, configure_logs, laboratory_python

def arguments():
    parser = argparse.ArgumentParser(description="XOC MCP LAB only; no production auth")
    parser.add_argument("--lab",action="store_true",required=True)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--backend")
    origin.add_argument("--installed-lab",action="store_true")
    parser.add_argument("--profile",required=True)
    parser.add_argument("--max-seconds",type=int,default=120,choices=range(10,301),metavar="10..300")
    return parser.parse_args()

def main():
    args = arguments()
    try:
        laboratory_python()
        path = backend_path(args.backend)
        from xoc_mcp_lab.fixtures import PROFILES
        if args.profile not in PROFILES: raise ValueError("Unknown laboratory profile")
        # Watchdog bounds a stuck synchronous handler by terminating ONLY this process.
        watchdog = threading.Timer(args.max_seconds,lambda:os._exit(124))
        watchdog.daemon = True
        watchdog.start()
        with Isolation():
            configure_logs()
            sys.path.insert(0,str(path))
            from xoc_mcp_lab.wiring import Wiring
            with Wiring() as wiring:
                from xoc_mcp_lab.adapter import Adapter
                from xoc_mcp_lab.transport import serve
                asyncio.run(serve(Adapter(args.profile,wiring)))
        watchdog.cancel()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("LAB_START_OR_RUNTIME_FAILED: verify lab profile, backend path and pinned dependencies.",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
