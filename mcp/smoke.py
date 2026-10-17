import argparse
import asyncio
import json
from xoc_mcp_lab.isolation import Isolation, configure_logs
def main():
    parser=argparse.ArgumentParser(description="Real local MCP smoke, fictional data only")
    parser.add_argument("--backend",required=True)
    args=parser.parse_args()
    with Isolation():
        configure_logs()
        from xoc_mcp_lab.client import smoke
        print(json.dumps(asyncio.run(smoke(args.backend)),indent=2))
if __name__=="__main__":
    main()
