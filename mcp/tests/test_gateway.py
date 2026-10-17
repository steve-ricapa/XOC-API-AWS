import copy
from decimal import Decimal
import json
import socket
import time
from unittest.mock import patch
import pytest
from xoc_mcp_lab.fixtures import CANARY, PROFILES
from xoc_mcp_lab.client import check_result

@pytest.mark.parametrize("name,args,key",[
    ("xoc.tickets.list",{},"tickets"),
    ("xoc.tickets.get",{"ticket_id":"lab-ticket-a1"},"ticket"),
    ("xoc.documents.list",{},"documents"),
    ("xoc.notifications.unread_count",{},"count")])
def test_real_gateway_handlers_and_helpers(wired,name,args,key):
    w,make=wired; a=make()
    out=check_result(a.call(name,args),success=True)
    assert key in out["data"]
    assert a.events[-1]["executor_calls"]==a.events[-1]["handler_calls"]==1
    assert w.reads>0 and w.write_attempts==w.provider_attempts==0
    if name.endswith(".get"):
        assert w.queries==[("LAB_TICKETS","get",{"pk":"TICKET#91001","sk":"TICKET#lab-ticket-a1"})]

@pytest.mark.parametrize("profile,allowed",[(p,p not in {"superadmin","invalid_context","admin_xoc_without_delegation"}) for p in PROFILES])
def test_roles_use_original_policy(wired,profile,allowed):
    w,make=wired; a=make(profile)
    assert len(a.list_tools())==(4 if allowed else 0)
    out=check_result(a.call("xoc.tickets.list",{}),success=allowed)
    assert a.handler_calls==int(allowed)
    if not allowed: assert w.reads==0 and a.events[-1]["layer"]=="policy"

@pytest.mark.parametrize("field",["tenantId","tenant_id","effective_tenant_id","userId","user_id",
    "role","actor_role","delegation_active","JWT","access_token","refresh_token",
    "effectiveTenantId","actorUserId","delegationActive","accessToken","refreshToken"])
def test_ownership_injection_is_policy_deny(wired,field):
    w,make=wired; a=make()
    result=check_result(a.call("xoc.tickets.list",{field:"LAB_FAKE_ATTACK"}),success=False)
    assert result["code"]=="ownership_argument_forbidden"
    assert a.events[-1]["layer"]=="policy"
    assert a.handler_calls==w.reads==0

@pytest.mark.parametrize("name,layer",[
    ("xoc.dashboard.summary","policy"),("xoc.integrations.summary","policy"),
    ("xoc.evil.execute","policy"),("xoc.tickets.create","policy"),
    ("ticket_create","adapter"),("ticket_patch","adapter"),("ticket_delete","adapter")])
def test_direct_hidden_unknown_write_calls(wired,name,layer):
    w,make=wired; a=make()
    check_result(a.call(name,{}),success=False)
    assert a.events[-1]["layer"]==layer
    assert a.handler_calls==w.reads==w.write_attempts==0

@pytest.mark.parametrize("args",[None,False,[],0,"",True,{"limit":False},{"limit":0},
    {"limit":201},{"limit":"2"},{"limit":1.0},{"limit":-1},{"status":False},{"wat":1},
    {"limit":{"tenant_id":91002}}])
def test_invalid_falsy_schema_inputs(wired,args):
    w,make=wired; a=make()
    check_result(a.call("xoc.tickets.list",args),success=False)
    assert a.handler_calls==w.reads==0

def test_absence_and_nullable_fields_are_distinct(wired):
    w,make=wired; a=make()
    check_result(a.call("xoc.tickets.list"),success=True)
    check_result(a.call("xoc.tickets.list",{"limit":None,"status":None}),success=True)
    check_result(a.call("xoc.tickets.list",None),success=False)
    check_result(a.call("xoc.tickets.get"),success=False)
    assert a.handler_calls==2

@pytest.mark.parametrize("tid",["","  ",1,False,None,"x"*257])
def test_ticket_id_strict_contract(wired,tid):
    w,make=wired; a=make()
    check_result(a.call("xoc.tickets.get",{"ticket_id":tid}),success=False)
    assert a.handler_calls==w.reads==0

def test_tenant_and_user_partition_keys(wired):
    w,make=wired
    a,b,a2=make(),make("admin_tenant_b"),make("user_a2")
    for adapter,letter,tenant,user,count in [(a,"a",91001,92001,2),(b,"b",91002,92003,3),(a2,"a",91001,92002,5)]:
        for name,key in [("xoc.tickets.list","tickets"),("xoc.documents.list","documents")]:
            result=check_result(adapter.call(name,{}),success=True)["data"][key]
            assert all(row.get("ticket_id",row.get("document_id")).split("-")[-1].startswith(letter) for row in result)
        out=check_result(adapter.call("xoc.notifications.unread_count",{}),success=True)
        assert out["data"]["count"]==count
        assert w.queries[-1][2]=={"GSI2PK":f"TENANT#{tenant}#USER#{user}#STATUS#UNREAD"}

def test_foreign_and_missing_tickets_same_safe_failure(wired):
    w,make=wired; a=make()
    for tid in ("lab-ticket-b1","does-not-exist"):
        out=check_result(a.call("xoc.tickets.get",{"ticket_id":tid}),success=False)
        assert out["code"]=="execution_failed" and out["data"] is None
        assert a.events[-1]["reads"]==1  # real tenant-key lookup, not a pre-policy deny

def test_store_error_no_details_retry_or_fallback(wired):
    w,make=wired; a=make(); w.fail_reads=True
    result=check_result(a.call("xoc.tickets.list",{}),success=False)
    assert result["code"]=="execution_failed"
    assert a.handler_calls==a.executor_calls==w.reads==1

def test_projection_removes_canaries_decimal_and_internal_fields(wired):
    w,make=wired; a=make()
    for name in ("xoc.tickets.list","xoc.documents.list"):
        out=check_result(a.call(name,{}),success=True)
        encoded=json.dumps(out)
        for bad in (CANARY,"download_url","s3_key","parameters","pushToken","action_plan","company_id","description"):
            assert bad not in encoded
    # Count conversion is in real helper, not adapter stringify.
    assert type(check_result(a.call("xoc.notifications.unread_count",{}),success=True)["data"]["count"]) is int

def test_projection_limits_and_unsafe_rows(wired):
    w,make=wired; a=make()
    seed=w.rows["LAB_TICKETS"][0]
    rows=[dict(seed,sk=f"TICKET#synthetic-{i}",ticket_id=f"synthetic-{i}",subject="s"*300) for i in range(60)]
    w.rows["LAB_TICKETS"]=rows
    out=check_result(a.call("xoc.tickets.list",{"limit":200}),success=True)
    assert out["data"]["count"]==50 and out["data"]["truncated"]
    assert len(out["data"]["tickets"][0]["subject"])==240
    # Bad store row beyond display cutoff must still fail ownership projection.
    w.rows["LAB_TICKETS"][-1]["tenant_id"]=Decimal(91002)
    out=check_result(a.call("xoc.tickets.list",{"limit":200}),success=False)
    assert out["code"]=="unsafe_tool_result"

def test_status_filters_real_helpers_and_pagination(wired):
    w,make=wired; a=make()
    out=check_result(a.call("xoc.tickets.list",{"status":"pending","limit":1}),success=True)
    assert len(out["data"]["tickets"])==1
    assert w.queries[-1][2]=={"gsi1pk":"TICKET#91001#STATUS#PENDING"}
    check_result(a.call("xoc.documents.list",{"status":"COMPLETED"}),success=True)
    assert w.queries[-1][2]=={"gsi2pk":"DOCUMENT#91001#STATUS#COMPLETED"}
    check_result(a.call("xoc.tickets.list",{"status":"invalid-status"}),success=False)
    assert a.events[-1]["reads"]==0
    assert a.events[-1]["handler_calls"]==0

def test_registry_drift_cannot_auto_publish(wired):
    w,make=wired; a=make()
    from src.tool_gateway.registry import TOOL_REGISTRY
    from dataclasses import replace
    definition=TOOL_REGISTRY["xoc.tickets.list"]
    with patch.dict(TOOL_REGISTRY,{"xoc.tickets.list":replace(definition,enabled=False),
            "xoc.extra.read":replace(definition,name="xoc.extra.read")}):
        assert len(a.list_tools())==3
        check_result(a.call("xoc.tickets.list",{}),success=False)
        check_result(a.call("xoc.extra.read",{}),success=False)
        assert a.handler_calls==w.reads==0
        assert a.events[-1]["layer"]=="adapter"

def test_audit_original_without_arguments(wired,caplog):
    w,make=wired; a=make()
    with caplog.at_level("INFO",logger="src.tool_gateway.audit"):
        a.call("xoc.tickets.list",{"access_token":"DO_NOT_LOG_THIS"})
    assert "tool_gateway_decision" in caplog.text and "ownership_argument_forbidden" in caplog.text
    assert "DO_NOT_LOG_THIS" not in caplog.text

def test_no_network_credentials_or_fallback(wired):
    w,make=wired
    import boto3
    import botocore.session
    with pytest.raises(RuntimeError,match="PROVIDER"): boto3.client("secretsmanager")
    with pytest.raises(RuntimeError,match="PROVIDER"): botocore.session.Session().get_credentials()
    with pytest.raises(RuntimeError,match="TABLE"): w.Table("a-real-table")
    with pytest.raises(RuntimeError,match="NETWORK"):
        socket.socket().connect(("203.0.113.9",443))
    with pytest.raises(RuntimeError,match="DNS"): socket.getaddrinfo("metadata.invalid",443)
    with pytest.raises(RuntimeError,match="SECRET_FILE"): open("../proxy-sophia-xoc/.env")
    assert w.reads==0

def test_bounded_business_work_not_distributed_exactly_once(wired):
    w,make=wired; a=make()
    a.events=[{}]*100
    check_result(a.call("xoc.tickets.list",{}),success=False)
    assert a.handler_calls==0
    a.events=[]
    a.call("xoc.tickets.list",{}); a.call("xoc.tickets.list",{})
    assert a.handler_calls==2  # independent reads, no distributed dedup promise
    w.deadline=time.monotonic()-1
    with pytest.raises(RuntimeError,match="BUDGET"):
        w.Table("LAB_TICKETS").get_item(Key={"pk":"TICKET#91001","sk":"TICKET#x"})

def test_no_mutable_fixture_cache_between_wirings(wired):
    w,make=wired
    from xoc_mcp_lab.fixtures import dataset
    first=dataset(); first["LAB_TICKETS"].clear()
    assert len(dataset()["LAB_TICKETS"])==4
    with pytest.raises(ValueError): make("../../external_fixture")

def test_original_source_and_wiring_restore(backend):
    import boto3
    import inspect
    from pathlib import Path
    from xoc_mcp_lab.wiring import Wiring
    original=boto3.resource
    with Wiring() as w:
        from src.tool_gateway import policy, executor
        for function in (policy.evaluate_tool_request, executor.ToolExecutor.execute,
                         executor.DEFAULT_HANDLERS["tickets.list"], w.tickets.list_tenant_tickets,
                         w.documents.list_tenant_document_jobs, w.inbox.unread_count_for_user):
            assert Path(inspect.getsourcefile(function)).resolve().is_relative_to(backend)
        assert boto3.resource is not original
    assert boto3.resource is original
    with Wiring() as fresh:
        assert fresh.reads==0 and len(fresh.rows["LAB_TICKETS"])==4

def test_incomplete_wiring_fails_without_real_provider():
    from xoc_mcp_lab.wiring import Wiring
    import boto3
    original=boto3.resource
    w=Wiring(); w.rows.clear()
    with pytest.raises(RuntimeError,match="TABLE"):
        with w: pass
    assert boto3.resource is original and w.reads==0

def test_projection_rejects_invalid_counts_and_excessive_output(wired):
    w,make=wired; a=make()
    from src.shared.sophia_tool_chat import model_result
    from src.tool_gateway.schemas import ToolResult, ToolDecision
    for bad in (Decimal(2),-1,True,"2"):
        out=model_result(ToolResult(ToolDecision.ALLOWED,{"count":bad}),
                         "xoc.notifications.unread_count",a.context)
        assert out["code"]=="unavailable" and out["data"] is None
    row={key:"x"*240 for key in ("ticket_id","id","subject","status","severity","created_at","updated_at")}
    out=model_result(ToolResult(ToolDecision.ALLOWED,{"tickets":[row]*50}),
                     "xoc.tickets.list",a.context)
    assert out["code"]=="unavailable"  # exceeds 64000 despite row/field bounds

def test_schema_matches_helper_statuses_and_limits(wired):
    w,make=wired; a=make()
    from xoc_mcp_lab.adapter import SCHEMAS, StrictValidator
    for status in w.tickets.VALID_STATUSES:
        for value in (status,status.lower()," "+status+" "):
            assert StrictValidator(SCHEMAS["xoc.tickets.list"]).is_valid({"status":value})
    assert StrictValidator(SCHEMAS["xoc.documents.list"]).is_valid({"status":"custom-no-matches"})
    check_result(a.call("xoc.tickets.list",{"limit":200}),success=True)
    check_result(a.call("xoc.documents.list",{"limit":200}),success=True)
