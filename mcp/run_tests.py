"""Offline harness installs the guard before importing pytest or XOC."""
import argparse
import sys
from pathlib import Path
from xoc_mcp_lab.isolation import Isolation, backend_path, configure_logs
def main():
    parser=argparse.ArgumentParser()
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--backend")
    origin.add_argument("--installed-lab", action="store_true")
    args=parser.parse_args()
    with Isolation():
        configure_logs()
        path=backend_path(args.backend)
        sys.path.insert(0,str(path))
        import pytest
        import xoc_mcp_lab.testing as testing
        testing.BACKEND=path
        return pytest.main(["-q","-p","no:cacheprovider",str(Path(__file__).parent / "tests")])
if __name__=="__main__":
    raise SystemExit(main())
