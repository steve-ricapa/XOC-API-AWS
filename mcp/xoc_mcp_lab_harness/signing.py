"""PUBLIC, reproducible test signing key. Never a production issuer."""
import time
from uuid import uuid4
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from xoc_mcp_lab.fixtures import PROFILES
from xoc_mcp_lab.request_envelope import AUDIENCE, ISSUER, KID, SCHEMA, tool_hash


def envelope(profile="admin_tenant_a", name="xoc.tickets.list", arguments=None,
             *, claims_patch=None, headers_patch=None, scalar=1):
    tenant, effective, user, role, delegated = PROFILES[profile]
    args = {} if arguments is None else arguments
    rid = "lab-" + uuid4().hex
    now = int(time.time())
    claims = dict(iss=ISSUER, aud=AUDIENCE, purpose="mcp-read", source="sophia_chat",
        actor_user_id=user, actor_role=role, tenant_id=tenant, effective_tenant_id=effective,
        delegation_active=delegated, request_id=rid, tool_hash=tool_hash(name, args),
        iat=now, exp=now + 30, deadline=now + 30)
    claims.update(claims_patch or {})
    headers = {"kid": KID}
    headers.update(headers_patch or {})
    token = jwt.encode(claims, ec.derive_private_key(scalar, ec.SECP256R1()),
                       algorithm="ES256", headers=headers)
    return dict(schema_version=SCHEMA, request_id=rid, tool={"name": name, "arguments": args},
                context_assertion=token)
