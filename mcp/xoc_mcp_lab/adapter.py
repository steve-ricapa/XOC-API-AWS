"""MCP projection around, not a replacement for, XOC authorization."""
from dataclasses import replace
from copy import deepcopy
from functools import wraps
import json
import logging
import re
import time
from uuid import uuid4
from jsonschema import Draft202012Validator, validators
from mcp import types
from src.tool_gateway import policy
from src.tool_gateway.audit import log_tool_decision
from src.tool_gateway.executor import ToolExecutor, DEFAULT_HANDLERS
from src.tool_gateway.registry import TOOL_REGISTRY
from src.tool_gateway.schemas import ToolAccessLevel, ToolContext, ToolRequest, ToolPolicyDecision, ToolDecision
from src.shared.sophia_tool_chat import model_result
from src.shared.tickets_store import VALID_STATUSES
from .fixtures import PROFILES

ALLOWLIST = ("xoc.tickets.list", "xoc.tickets.get", "xoc.documents.list", "xoc.notifications.unread_count")
LIST_SCHEMA = {"type":"object","properties":{
    "limit":{"type":["integer","null"],"minimum":1,"maximum":200,"default":50},
    "status":{"type":["string","null"]}}, "additionalProperties":False}
SCHEMAS = {
    "xoc.tickets.list": deepcopy(LIST_SCHEMA),
    "xoc.documents.list": deepcopy(LIST_SCHEMA),
    "xoc.tickets.get": {"type":"object","properties":{"ticket_id":{
        "type":"string","minLength":1,"maxLength":256,"pattern":r"\S"}},
        "required":["ticket_id"], "additionalProperties":False},
    "xoc.notifications.unread_count":{"type":"object","properties":{},"additionalProperties":False},
}
# Derive ticket status spelling from the real helper, including strip/upper.
# Character classes keep this portable JSON Schema, not Python-only (?i).
SCHEMAS["xoc.tickets.list"]["properties"]["status"]["pattern"] = (
    r"^\s*(?:" + "|".join("".join("["+c.lower()+c+"]" if c.isalpha() else re.escape(c)
        for c in status) for status in sorted(VALID_STATUSES)) + r")\s*$")
# JSON Schema considers 1.0 integer. The lab intentionally rejects float/bool coercion.
StrictValidator = validators.extend(Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("integer",lambda _,v:type(v) is int))
MISSING = object()
logger = logging.getLogger("xoc_mcp_lab")

class Adapter:
    def __init__(self, profile, wiring):
        if profile not in PROFILES: raise ValueError("Unknown laboratory profile")
        self.profile, self.wiring = profile, wiring
        self.context = ToolContext(*PROFILES[profile], source="mcp_lab")
        self.verified = None
        self.verified_used = False
        self._initialize(wiring)

    @classmethod
    def from_verified(cls, verified, wiring):
        from .request_envelope import VerifiedContext
        if type(verified) is not VerifiedContext:
            raise ValueError("LAB_INVALID_VERIFIED_CONTEXT")
        verified.require_valid()
        adapter = cls.__new__(cls)
        adapter.profile = "verified_fixture"
        adapter.context = verified.tool_context()
        adapter.verified = verified
        adapter.verified_used = False
        adapter._initialize(wiring)
        return adapter

    def _initialize(self, wiring):
        self.wiring = wiring
        self.events = []
        self.handler_calls = 0
        self.executor_calls = 0
        # Observation wraps ORIGINAL handlers, including original policy inside executor.
        def counted(handler):
            @wraps(handler)
            def invoke(context, arguments):
                self.handler_calls += 1
                return handler(context, arguments)
            return invoke
        self.executor = ToolExecutor(handlers={k:counted(v) for k,v in DEFAULT_HANDLERS.items()})
        for name in ALLOWLIST:
            if name not in TOOL_REGISTRY:
                raise RuntimeError("Reviewed gateway registry changed; review wiring")
    def eligible(self, name):
        definition = TOOL_REGISTRY.get(name)
        return (name in ALLOWLIST and definition is not None and definition.enabled
            and definition.access_level is ToolAccessLevel.READ_ONLY)
    def list_tools(self):
        result = []
        for name in ALLOWLIST:
            if self.eligible(name) and policy.evaluate_tool_request(
                    self.context, ToolRequest(name,{})).allowed:
                result.append(types.Tool(name=name,description=TOOL_REGISTRY[name].description,
                    inputSchema=SCHEMAS[name], annotations=types.ToolAnnotations(
                        readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)))
        return result
    def call(self, name, arguments=MISSING):
        started = time.monotonic()
        context = self.context if self.verified else replace(self.context,request_id="lab-"+uuid4().hex)
        before = (self.handler_calls,self.executor_calls,self.wiring.reads)
        self.wiring.start_call()
        stage = "adapter"
        audit_id = None
        code = "invalid_arguments"
        payload = None
        # Do not reflect/log arbitrary tool names (including token-shaped names).
        safe_name = name if isinstance(name,str) and re.fullmatch(r"xoc\.[a-z_]+\.[a-z_]+",name) and len(name)<=128 else "lab.rejected"
        args = {} if arguments is MISSING else arguments
        request = ToolRequest(safe_name,args,context.request_id)
        bound_error = None
        if self.verified:
            if self.verified_used:
                bound_error = "lab_call_limit"
            else:
                self.verified_used = True  # consume even a denied/mismatched attempt
                try:
                    if not self.verified.matches(name, args):
                        bound_error = "lab_request_mismatch"
                except Exception:
                    bound_error = "lab_invalid_context"
        if bound_error:
            code = bound_error
        elif len(self.events)>=100:
            code = "lab_call_limit"
        elif safe_name == "lab.rejected":
            code = "mcp_tool_not_exposed"
        else:
            # Preflight is the real policy, not a local role/tenant rule.
            decision = policy.evaluate_tool_request(context,request)
            if not decision.allowed:
                stage = "policy"
                self.executor_calls += 1
                result = self.executor.execute(context,request)
                payload = model_result(result,safe_name,context)
                code, audit_id = result.code, result.audit_id
            elif not self.eligible(safe_name):
                code = "mcp_tool_not_exposed"
            elif not StrictValidator(SCHEMAS[safe_name]).is_valid(args):
                code = "invalid_arguments"
            else:
                stage = "executor"
                self.executor_calls += 1
                result = self.executor.execute(context,request)
                payload = model_result(result,safe_name,context)
                audit_id = result.audit_id
                code = result.code if payload["code"] != "unavailable" else "unsafe_tool_result"
        if payload is None:
            decision = ToolPolicyDecision(ToolDecision.DENIED,"Laboratory adapter validation denied",code)
            audit_id = log_tool_decision(context=context,request=request,decision=decision,
                definition=TOOL_REGISTRY.get(safe_name))
            payload = {"request_id":context.request_id,"tool_name":safe_name,"status":"denied",
                "code":code,"data":None,"audit_id":audit_id}
        else:
            payload["code"] = code
        error = payload["status"] != "allowed"
        event = {"request_id":context.request_id,"audit_id":audit_id,"tool_name":safe_name,
            "profile":self.profile,"decision":payload["status"],"code":code,"layer":stage,
            "handler_calls":self.handler_calls-before[0],
            "executor_calls":self.executor_calls-before[1],"reads":self.wiring.reads-before[2],
            "writes":0,"duration_ms":round((time.monotonic()-started)*1000,3)}
        if len(self.events)<101: self.events.append(event)
        logger.info("lab_call %s",json.dumps(event,separators=(",",":")))
        text = json.dumps(payload,ensure_ascii=True,allow_nan=False,separators=(",",":"))
        return types.CallToolResult(content=[types.TextContent(type="text",text=text)],
                                    structuredContent=payload,isError=error)
