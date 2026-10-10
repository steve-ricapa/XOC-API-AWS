"""TEST FIXTURE ONLY — PUBLIC SCALAR — NOT A SECRET — NEVER PRODUCTION.

No profiles: all identity fields come from the authenticated backend ToolContext.
The existing MCP fixture verifier independently rejects real identity ranges.
"""
import time
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from xoc_mcp_lab.request_envelope import SCHEMA, ISSUER, AUDIENCE, KID, tool_hash, verify_envelope


def issue(context, request, deadline):
    claims = dict(iss=ISSUER, aud=AUDIENCE, purpose="mcp-read", source=context.source,
        actor_user_id=context.user_id, actor_role=context.role,
        tenant_id=context.tenant_id, effective_tenant_id=context.effective_tenant_id,
        delegation_active=context.delegation_active, request_id=context.request_id,
        tool_hash=tool_hash(request.tool_name, request.arguments),
        iat=int(time.time()), exp=deadline, deadline=deadline)
    envelope = dict(schema_version=SCHEMA, request_id=context.request_id,
        tool={"name": request.tool_name, "arguments": request.arguments},
        context_assertion=jwt.encode(claims, ec.derive_private_key(1, ec.SECP256R1()),
            algorithm="ES256", headers={"kid": KID}))
    verify_envelope(envelope)
    return envelope
