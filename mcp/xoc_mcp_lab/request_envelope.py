"""L1 FIXTURES ONLY. Not production authentication, KMS, or a public API.

The pinned public key corresponds to the deliberately public test scalar 1.
There is no key selection from environment, headers, URLs or model arguments.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
import time
import jwt

SCHEMA = "xoc-mcp-query-v1"
ISSUER = "xoc-mcp-lab-fixture-issuer"
AUDIENCE = "xoc-sophia-query-worker"
KID = "LAB-ONLY-PUBLIC-TEST-KEY-1"
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEaxfR8uEsQkf4vOblY6RA8ncDfYEt
6zOg9KE5RdiYwpZP40Li/hp/m47n60p8D54WK84zV2sxXs7LtkBoN79R9Q==
-----END PUBLIC KEY-----
"""
MAX_ENVELOPE_BYTES = 16384  # Also below Windows' single env-value bound.
MAX_LIFETIME = 30
_SEAL = object()
_CLAIMS = frozenset({"iss", "aud", "purpose", "source", "actor_user_id", "actor_role",
    "tenant_id", "effective_tenant_id", "delegation_active", "request_id", "tool_hash",
    "iat", "exp", "deadline"})


class EnvelopeError(ValueError):
    def __init__(self):
        super().__init__("LAB_INVALID_ENVELOPE")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":"))


def tool_hash(name, arguments):
    return hashlib.sha256(canonical({"name": name, "arguments": arguments}).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EnvelopeError()
        result[key] = value
    return result


@dataclass(frozen=True)
class VerifiedContext:
    request_id: str
    tool_name: str
    arguments_json: str = field(repr=False)
    tenant_id: int | None
    effective_tenant_id: int | None
    user_id: int
    role: str
    delegation_active: bool
    deadline: int
    _seal: object = field(repr=False, compare=False)

    def require_valid(self):
        if self._seal is not _SEAL or time.time() >= self.deadline:
            raise EnvelopeError()

    def matches(self, name, arguments):
        self.require_valid()
        return name == self.tool_name and canonical(arguments) == self.arguments_json

    def tool_context(self):
        self.require_valid()
        # Import only AFTER verification; no provider or backend import in verifier.
        from src.tool_gateway.schemas import ToolContext
        return ToolContext(self.tenant_id, self.effective_tenant_id, self.user_id,
            self.role, self.delegation_active, self.request_id, "sophia_chat")


def verify_envelope(envelope):
    """Only signed fictional identities. No credentials or input reflected on error."""
    try:
        if type(envelope) is str:
            if len(envelope.encode("utf-8")) > MAX_ENVELOPE_BYTES:
                raise EnvelopeError()
            envelope = json.loads(envelope, object_pairs_hook=_unique)
        if type(envelope) is not dict or set(envelope) != {
                "schema_version", "request_id", "tool", "context_assertion"}:
            raise EnvelopeError()
        if len(canonical(envelope).encode()) > MAX_ENVELOPE_BYTES:
            raise EnvelopeError()
        if envelope["schema_version"] != SCHEMA:
            raise EnvelopeError()
        rid, tool, token = envelope["request_id"], envelope["tool"], envelope["context_assertion"]
        if type(rid) is not str or not re.fullmatch(r"lab-[A-Za-z0-9_-]{1,60}", rid):
            raise EnvelopeError()
        if type(tool) is not dict or set(tool) != {"name", "arguments"}:
            raise EnvelopeError()
        if type(tool["name"]) is not str or not re.fullmatch(r"xoc\.[a-z_]{1,40}\.[a-z_]{1,40}", tool["name"]):
            raise EnvelopeError()
        if type(tool["arguments"]) is not dict or type(token) is not str or len(token) > 4096:
            raise EnvelopeError()
        header = jwt.get_unverified_header(token)
        if header != {"alg": "ES256", "typ": "JWT", "kid": KID}:
            raise EnvelopeError()
        claims = jwt.decode(token, PUBLIC_KEY, algorithms=["ES256"], issuer=ISSUER,
            audience=AUDIENCE, options={
                # PyJWT treats null as absent for require; ADMIN_XOC legitimately
                # has tenant_id=None. Exact key/type checks below still require it.
                "require": sorted(_CLAIMS - {"tenant_id", "effective_tenant_id"}),
                "strict_aud": True})
        if set(claims) != _CLAIMS or claims["purpose"] != "mcp-read" or claims["source"] != "sophia_chat":
            raise EnvelopeError()
        if claims["request_id"] != rid or claims["tool_hash"] != tool_hash(tool["name"], tool["arguments"]):
            raise EnvelopeError()
        for key in ("iat", "exp", "deadline", "actor_user_id"):
            if type(claims[key]) is not int:
                raise EnvelopeError()
        now = time.time()
        if not (claims["iat"] <= now < claims["deadline"] <= claims["exp"] <= claims["iat"] + MAX_LIFETIME):
            raise EnvelopeError()
        # Fail closed on real identities even if somebody signs with this PUBLIC fixture key.
        for key in ("tenant_id", "effective_tenant_id"):
            if claims[key] is not None and (type(claims[key]) is not int or claims[key] not in (91001, 91002)):
                raise EnvelopeError()
        if not 92000 <= claims["actor_user_id"] <= 92999:
            raise EnvelopeError()
        if type(claims["actor_role"]) is not str or not re.fullmatch(r"[A-Z_]{1,32}", claims["actor_role"]):
            raise EnvelopeError()
        if type(claims["delegation_active"]) is not bool:
            raise EnvelopeError()
        return VerifiedContext(rid, tool["name"], canonical(tool["arguments"]),
            claims["tenant_id"], claims["effective_tenant_id"], claims["actor_user_id"],
            claims["actor_role"], claims["delegation_active"], claims["deadline"], _SEAL)
    except Exception:
        raise EnvelopeError() from None
