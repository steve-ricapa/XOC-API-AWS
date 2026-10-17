"""Fixture-only child. No public token option, production mode or signing material."""
import argparse
import asyncio
import os
from pathlib import Path
import sys
import threading
import time
from xoc_mcp_lab.isolation import Isolation, LAB_ROOT, backend_path, configure_logs, laboratory_python

ENVELOPE_ENV = "XOC_L1_FIXTURE_ENVELOPE"


def main():
    parser = argparse.ArgumentParser(description="L1 fixture child only")
    parser.add_argument("--lab", action="store_true", required=True)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--backend")
    origin.add_argument("--installed-lab", action="store_true")
    args = parser.parse_args()
    watchdog = None
    try:
        # Consume the one launch value BEFORE environment isolation; never log it.
        envelope = os.environ.pop(ENVELOPE_ENV, "")
        with Isolation():
            configure_logs()
            from xoc_mcp_lab.request_envelope import verify_envelope
            verified = verify_envelope(envelope)
            del envelope
            laboratory_python()
            path = backend_path(args.backend)
            watchdog = threading.Timer(max(0.001, verified.deadline - time.time()),
                                       lambda: os._exit(124))
            watchdog.daemon = True
            watchdog.start()
            sys.path.insert(0, str(path))
            from xoc_mcp_lab.wiring import Wiring
            with Wiring() as wiring:
                from xoc_mcp_lab.adapter import Adapter
                from xoc_mcp_lab.transport import serve
                asyncio.run(serve(Adapter.from_verified(verified, wiring)))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("LAB_VERIFIED_CHILD_FAILED", file=sys.stderr)
        return 2
    finally:
        if watchdog:
            watchdog.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
