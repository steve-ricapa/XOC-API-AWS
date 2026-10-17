import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import pytest
from mcp import types
from mcp.client import stdio
from mcp.shared.exceptions import McpError
from xoc_mcp_lab.client import connect,check_result,smoke
from xoc_mcp_lab.isolation import LAB_ROOT,clean_environment

def audit_events(log):
    log.seek(0)
    text=log.read()
    assert "LAB_CANARY" not in text and "DO_NOT_LOG_THIS" not in text
    events=[json.loads(line.split("lab_call ",1)[1]) for line in text.splitlines() if "lab_call " in line]
    return events

def test_full_real_client_smoke(backend):
    processes=[]
    original=stdio._create_platform_compatible_process
    async def observed(**kwargs):
        assert kwargs["command"]==sys.executable
        process=await original(**kwargs); processes.append(process); return process
    with patch.object(stdio,"_create_platform_compatible_process",observed):
        evidence=asyncio.run(smoke(backend))
    assert evidence["status"]=="passed" and len(processes)==4
    assert all(p.returncode==0 for p in processes), "Server child left running or crashed"

def test_stdio_protocol_validation_and_metadata_cannot_change_context(backend):
    async def scenario(log):
        async with connect(backend,"admin_tenant_a",errlog=log) as (session,info):
            assert info.protocolVersion=="2025-11-25"
            caps=info.capabilities
            assert caps.tools and not any([caps.resources,caps.prompts,caps.logging,caps.tasks,caps.completions])
            tools=await session.list_tools()
            assert len(tools.tools)==4 and all(t.annotations.readOnlyHint for t in tools.tools)
            with pytest.raises(McpError) as unsupported:
                await session.list_resources()  # known protocol method, no advertised handler
            assert unsupported.value.error.code==-32601
            for args in (None,False,[],0,""):
                # SDK envelopes, deliberately constructed without CLIENT schema validation.
                # The SERVER must reject independently. No manual JSON-RPC implementation.
                request=types.Request[dict,str](
                    method="tools/call",params={"name":"xoc.tickets.list","arguments":args})
                with pytest.raises(McpError) as malformed:
                    await session.send_request(request,types.CallToolResult)
                assert malformed.value.error.code==-32602
            request=types.ClientRequest(types.CallToolRequest(params=types.CallToolRequestParams(
                name="xoc.tickets.list",arguments={},_meta={"tenant_id":91002,"role":"SUPERADMIN",
                    "profile":"admin_tenant_b","clientInfo":{"name":"switch_user"}})))
            result=check_result(await session.send_request(request,types.CallToolResult),success=True)
            assert all(r["ticket_id"].startswith("lab-ticket-a") for r in result["data"]["tickets"])
            check_result(await session.call_tool("xoc.tickets.list",{"access_token":"DO_NOT_LOG_THIS"}),success=False)
            check_result(await session.call_tool("xoc.tickets.list",{"limit":False}),success=False)
            # Omission is valid while explicit null was not.
            request=types.Request[dict,str](method="tools/call",params={"name":"xoc.tickets.list"})
            check_result(await session.send_request(request,types.CallToolResult),success=True)
    with tempfile.TemporaryFile(mode="w+",encoding="utf-8") as log:
        asyncio.run(scenario(log))
        events=audit_events(log)
        assert len(events)==4
        assert [e["handler_calls"] for e in events]==[1,0,0,1]
        assert events[1]["layer"]=="policy" and events[2]["layer"]=="adapter"

def test_two_simultaneous_stdio_instances_do_not_share_context(backend):
    async def scenario(log):
        async with connect(backend,"user_a1",errlog=log) as (a,_):
            async with connect(backend,"user_a2",errlog=log) as (b,_):
                first,second=await asyncio.gather(a.call_tool("xoc.notifications.unread_count",{}),
                    b.call_tool("xoc.notifications.unread_count",{}))
                assert check_result(first,success=True)["data"]["count"]==2
                assert check_result(second,success=True)["data"]["count"]==5
                assert first.structuredContent["request_id"]!=second.structuredContent["request_id"]
    with tempfile.TemporaryFile(mode="w+",encoding="utf-8") as log:
        asyncio.run(scenario(log))
        assert len(audit_events(log))==2

@pytest.mark.parametrize("profile",["invalid_context","superadmin","admin_xoc_without_delegation"])
def test_denied_context_over_wire_has_no_data_reads(backend,profile):
    async def scenario(log):
        async with connect(backend,profile,errlog=log) as (session,_):
            assert not (await session.list_tools()).tools
            check_result(await session.call_tool("xoc.tickets.list",{}),success=False)
    with tempfile.TemporaryFile(mode="w+",encoding="utf-8") as log:
        asyncio.run(scenario(log))
        event=audit_events(log)[0]
        assert event["handler_calls"]==event["reads"]==0

@pytest.mark.parametrize("options",[
    [],["--lab"],["--lab","--profile","../unknown"],
    ["--lab","--profile","admin_tenant_a","--backend","DOES_NOT_EXIST"]])
def test_fail_closed_startup(options):
    proc=subprocess.run([sys.executable,"-B",str(LAB_ROOT/"server.py"),*options],
        input="",capture_output=True,text=True,timeout=8,env=clean_environment(),shell=False)
    assert proc.returncode!=0 and proc.stdout==""
    assert "Traceback" not in proc.stderr

def test_oversized_input_closes_only_child(backend):
    proc=subprocess.run([sys.executable,"-B",str(LAB_ROOT/"server.py"),"--lab",
        "--backend",str(backend),"--profile","admin_tenant_a"],input="x"*65537+"\n",
        capture_output=True,text=True,timeout=10,env=clean_environment(),shell=False)
    assert proc.returncode!=0 and proc.stdout==""
    assert "x"*100 not in proc.stderr

def test_unknown_profile_fails_closed_with_valid_backend(backend):
    proc=subprocess.run([sys.executable,"-B",str(LAB_ROOT/"server.py"),"--lab",
        "--backend",str(backend),"--profile","not-a-fixture"],input="",
        capture_output=True,text=True,timeout=8,env=clean_environment(),shell=False)
    assert proc.returncode==2 and proc.stdout=="" and "Traceback" not in proc.stderr

def test_inherited_cloud_settings_not_used_by_child(backend):
    async def scenario(log):
        async with connect(backend,"admin_tenant_a",errlog=log) as (session,_):
            check_result(await session.call_tool("xoc.tickets.list",{}),success=True)
    with patch.dict(os.environ,{"AWS_PROFILE":"DO_NOT_USE_REAL_PROFILE",
            "AZURE_CLIENT_SECRET":"DO_NOT_LOG_THIS","TICKETS_TABLE_NAME":"NOT_LAB",
            "DATABASE_URL":"DO_NOT_USE_REAL_DATABASE"}):
        with tempfile.TemporaryFile(mode="w+",encoding="utf-8") as log:
            asyncio.run(scenario(log))
            assert audit_events(log)[0]["handler_calls"]==1
