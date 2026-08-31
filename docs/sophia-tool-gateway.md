# SOPHIA Tool Gateway

## Purpose

SOPHIA Chat and SOPHIA Voice must never receive unrestricted access to XOC
internal APIs, secrets, infrastructure, SQL, or administrative routes.  The
Tool Gateway is the backend-owned policy boundary for every future AI tool.

Fase B introduces the contract, allowlist, deterministic policy, safe audit
logging, and a small read-only executor.  It is **not** a public MCP server and
it does not expose an HTTP route by itself.

## Current architecture

```text
Future governed Chat/Voice caller
  -> ToolContext built from authenticated XOC context
  -> Tool Gateway policy
  -> explicit Tool Registry
  -> fixed internal read handler
  -> tenant-scoped XOC store
```

The model supplies only a tool name and limited arguments.  Tenant, effective
tenant, actor user, role, delegation, and request ID are trusted values built
outside the model from XOC authentication context.

## Fase B allowlist

Enabled tools:

| Tool | Access | Data source | Roles |
| --- | --- | --- | --- |
| `xoc.tickets.list` | `READ_ONLY` | Existing tickets DynamoDB store | USER, ADMIN, delegated ADMIN_XOC |
| `xoc.tickets.get` | `READ_ONLY` | Existing tickets DynamoDB store | USER, ADMIN, delegated ADMIN_XOC |
| `xoc.documents.list` | `READ_ONLY` | Existing reports DynamoDB store | USER, ADMIN, delegated ADMIN_XOC |
| `xoc.notifications.unread_count` | `READ_ONLY` | Existing notification inbox DynamoDB store | USER, ADMIN, delegated ADMIN_XOC |

Registered but disabled:

| Tool | Reason |
| --- | --- |
| `xoc.dashboard.summary` | Existing helper requires SQLAlchemy/RDS access; Fase B introduces no RDS dependency. |
| `xoc.integrations.summary` | Existing helper requires SQLAlchemy/RDS access; Fase B introduces no RDS dependency. |

No new tables, queries, models, migrations, or RDS writes are part of this
module.

## Hard blocks

The policy denies unknown, disabled, destructive, or unsupported write tools.
It also denies missing/invalid roles, `SUPERADMIN` AI access, missing
authentication context, and any tool arguments that try to provide tenant or
user ownership fields.

The following remain outside the registry:

- SQL, shell/command execution, arbitrary HTTP, and Secrets Manager access;
- deletion of data, tenants, users, integrations, documents, or tickets;
- integration credentials, agent keys, key rotation, and agent-instance edits;
- approval/rejection of tickets and operational automation;
- superadmin routes and administrative APIs.

`WRITE_REQUIRES_APPROVAL` definitions return `needs_approval`; they do not run.
`WRITE_SAFE` definitions are denied during Fase B.  No write definition is
registered in production code.

## Role and tenant rules

- `USER` and `ADMIN` can use only the explicit read-only allowlist.
- `ADMIN_XOC` requires an active delegated tenant context.
- `SUPERADMIN` is blocked by default for AI tools even if the normal UI has
  platform capabilities.
- Tool arguments cannot contain `tenantId`, `tenant_id`, `userId`, `user_id`,
  or effective-tenant equivalents.  Ownership always comes from `ToolContext`.

## Safe audit logging

Each decision writes a structured log with a derived audit ID, request ID, tool
name, effective tenant, actor user, role, source, decision, reason code, risk,
and access level.  It never logs JWTs, tokens, credentials, agent keys,
arguments, document content, or other full payloads.

Fase B intentionally does not create a persistence table for audit events.

## Why Voice is not integrated yet

Voice clients currently connect directly to an external WebSocket proxy and
receive `tool.call` events.  The proxy's tool definitions are not in this
repository.  Wiring Voice to this module before removing or governing those
external tools would create a false impression of protection.

Before Voice is considered governed, its proxy/runtime must:

1. disable direct tools, or route every tool exclusively through XOC;
2. use a short-lived capability minted by XOC for the specific request;
3. send no raw user JWT through URLs or logs where avoidable;
4. preserve actor user, role, effective tenant, delegation and request ID;
5. have no alternate API credentials that bypass Tool Gateway policy.

## Chat integration point

Chat currently proxies to an external SOPHIA Function in
`src/handlers/routes/chat.py`.  Fase C recognizes a structured
`tool_request`/`toolRequest` returned by that runtime and sends it through the
internal gateway with a context built from the authenticated Chat user.  An
unknown or denied request cannot reach XOC data handlers.

The external SOPHIA Function does not yet send the result back into a second
model turn, so Fase C does not claim full agentic tool use.  It establishes the
only accepted execution boundary and returns the policy outcome as response
metadata for an eventual runtime adapter.

Chat no longer auto-creates tickets from an agent `action_plan` or keyword
heuristic.  It returns a short-lived ticket proposal instead.  Only an `ADMIN`
or delegated `ADMIN_XOC` can confirm it through protected
`POST /chat/tickets/confirm`; confirmation must use the signed proposal issued
to the same user, effective tenant, role, and delegation state.  This endpoint
is an explicit user action, not a model tool or arbitrary write endpoint.

The external SOPHIA runtime should eventually receive a short-lived,
audience-bound tool capability carrying only:

```text
actor_user_id
actor_role
effective_tenant_id
delegation_active
request_id
audience = xoc-ai-tool-gateway
scope = ai:tools
```

It must not receive a capability for arbitrary XOC APIs.

## Hardening backlog from Fase A

1. Add a fixed allowlist for `agentType` in `/agents/auth/token-from-user`.
2. Verify `thread_id` locally belongs to the current tenant/user before Chat or
   History forwards it to the external runtime.
3. Add web/mobile UI for the existing protected ticket-proposal confirmation
   contract; keep the model unable to confirm on the user's behalf.
4. Replace current generic SOPHIA service tokens with scoped, audience-bound
   tool capabilities when the external Function is ready.
5. Audit and reconfigure the external Voice proxy before any Voice integration.

## Adding a tool safely

1. Classify its access and risk before writing code.
2. Add an explicit static definition to `registry.py`; never dynamically load a
   model-supplied name.
3. Start disabled unless a small bounded internal handler already exists.
4. Derive tenant/user only from `ToolContext`.
5. Accept a tiny, validated argument allowlist.
6. Do not use SQL text, shell, `eval`, `exec`, dynamic imports, or arbitrary
   HTTP from a handler.
7. Add allow, deny, tenant-isolation, and error-sanitization tests.
8. Do not expose it through Chat or Voice until its external caller uses the
   same authenticated policy boundary.
