"""Official SDK client, fixed launcher. No LLM, HTTP, shell or global registration."""
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters, types
from mcp.client import stdio
from .fixtures import PROFILES
from .isolation import LAB_ROOT, backend_path, clean_environment, laboratory_python

PROTOCOL = "2025-11-25"
@asynccontextmanager
async def connect(backend, profile, *, errlog=sys.stderr):
    if profile not in PROFILES: raise ValueError("Unknown lab profile")
    backend = backend_path(backend)
    python = laboratory_python()
    params = StdioServerParameters(command=str(python),
        args=["-B",str(LAB_ROOT/"server.py"),"--lab","--backend",str(backend),
              "--profile",profile,"--max-seconds","60"],env=clean_environment(),cwd=str(LAB_ROOT))
    async with stdio.stdio_client(params,errlog=errlog) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=12),
                client_info=types.Implementation(name="xoc-lab-python-client",version="0.1.0")) as session:
            info = await session.initialize()
            if info.protocolVersion!=PROTOCOL: raise RuntimeError("Unexpected MCP revision")
            yield session,info

def check_result(result, *, success):
    if result.isError == success: raise AssertionError("Unexpected tool result")
    if len(result.content)!=1 or result.content[0].type!="text":
        raise AssertionError("Unexpected result content")
    data = result.structuredContent
    if json.loads(result.content[0].text)!=data: raise AssertionError("Inconsistent structured/text result")
    if "LAB_CANARY" in result.content[0].text: raise AssertionError("Unsafe projection")
    return data

async def smoke(backend):
    evidence = {"sdk":"mcp==1.30.0","protocol":PROTOCOL,"transport":"stdio",
                "model_calls":0,"profiles":[]}
    for profile,letter,count in (("admin_tenant_a","a",2),("admin_tenant_b","b",3),
                                 ("user_a2","a",5)):
        async with connect(backend,profile) as (session,info):
            tools = await session.list_tools()
            expected=["xoc.tickets.list","xoc.tickets.get","xoc.documents.list","xoc.notifications.unread_count"]
            assert [tool.name for tool in tools.tools]==expected
            evidence["capabilities"]=info.capabilities.model_dump(exclude_none=True)
            listed = check_result(await session.call_tool(expected[0],{"limit":20}),success=True)
            assert {r["ticket_id"] for r in listed["data"]["tickets"]}=={f"lab-ticket-{letter}1",f"lab-ticket-{letter}2"}
            check_result(await session.call_tool(expected[1],{"ticket_id":f"lab-ticket-{letter}1"}),success=True)
            docs=check_result(await session.call_tool(expected[2],{}),success=True)
            assert all(r["document_id"].startswith(f"lab-document-{letter}") for r in docs["data"]["documents"])
            unread=check_result(await session.call_tool(expected[3],{}),success=True)
            assert unread["data"]["count"]==count
            other="b" if letter=="a" else "a"
            for name,args in [
                (expected[1],{"ticket_id":f"lab-ticket-{other}1"}),
                (expected[0],{"tenant_id":91002}),
                ("xoc.dashboard.summary",{}),
                ("xoc.evil.execute",{}),
                ("xoc.tickets.create",{}),
            ]:
                check_result(await session.call_tool(name,args),success=False)
            evidence["profiles"].append({"profile":profile,"tools":len(tools.tools),
                "allowed":4,"denied":5,"unread":count})
    async with connect(backend,"superadmin") as (session,_):
        assert not (await session.list_tools()).tools
        check_result(await session.call_tool("xoc.tickets.list",{}),success=False)
    evidence["status"]="passed"
    return evidence
