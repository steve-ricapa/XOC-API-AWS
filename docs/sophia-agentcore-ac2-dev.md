# SOPHIA AC-2 — DEV deployment gate and local artifact validation

Date: 2026-10-10. **AC2_STATUS = BLOCKED. READY_FOR_RELEASE_REVIEW = NO.**

## Latest resumption — identity resolved, DEV dependencies absent

This section supersedes the identity blocker in the historical first-attempt
report below. Resumed from HEAD `5bd4444d4772ffcb312df2cdb82bf93ad94c186b` on
`feature/sophia-agentcore-ac2`. Existing untracked files remain untouched.

**AWS_IDENTITY_BLOCKER = RESOLVED.** Every AWS and Serverless operation in this
resumption explicitly used `xoc-dev`, `us-east-1`. STS returned the requested
account `8117****6524`, principal
`arn:aws:iam::8117****6524:user/santiago.silva`. No other profile was used.

**EXTERNAL_RESOURCE_BLOCKER = REQUIRED XOC DEV ENVIRONMENT ABSENT.**

Verified via real, read-only AWS requests, not inferred from local naming:

| Required resource | AWS result |
|---|---|
| `xoc-api-shared-dev` | DescribeStacks: ValidationError, stack does not exist |
| `xoc-api-chat-dev` | DescribeStacks: ValidationError, stack does not exist |
| `xoc-api-tickets-dev` | DescribeStacks: ValidationError, stack does not exist |
| `xoc-api-reports-dev` | DescribeStacks: ValidationError, stack does not exist |
| `xoc-api-ops-dev` | DescribeStacks: ValidationError, stack does not exist |
| `xoc-api-agentcore-dev` | DescribeStacks: ValidationError, stack does not exist; expected for initial deploy |
| `xoc-api-tickets-dev-tickets` | DescribeTable: ResourceNotFoundException |
| `xoc-api-reports-dev-reports` | DescribeTable: ResourceNotFoundException |
| `xoc-api-ops-dev-user-notification-inbox` | DescribeTable: ResourceNotFoundException |
| `xoc-api-chat-dev-chatAgentsApi` | GetFunctionConfiguration: ResourceNotFoundException |
| `/aws/lambda/xoc-api-chat-dev*` | No log groups |
| DEV AgentCore gateways | None |

Consequently there are no table indexes or actual Chat role to verify, no Chat
DEV endpoint to smoke and no shared authentication outputs to resolve.
These are resource-not-found responses, not AccessDenied responses.

The only active stack with `dev` in its name was `xoc-reportes-dev`,
UPDATE_COMPLETE. Its resource inventory contains analyticsMock, convertirDocx,
generarreporte and transiaXml Lambdas, report REST API and S3 bucket. It does NOT
contain the required XOC Chat or DynamoDB tables and was not substituted for them.
DEV exports belong only to this separate report service. The broad initial table
name search also matched a PROD `device-registry` because `device` contains `dev`;
that was explicitly rejected as a DEV resource. No table items were read.

The local DEV stage additionally has an empty `agentsFunctionBaseUrl` and refers
to shared/data/network/storage/documents DEV outputs. ListStacks found none of
those required infra stacks; bootstrap would be a separate scope, not an AgentCore
packaging repair. No PROD URL, secret, table or data was borrowed.

### Independent validation completed after resumption

```powershell
$env:AWS_PROFILE='xoc-dev'
$env:AWS_REGION='us-east-1'
$env:AWS_DEFAULT_REGION='us-east-1'
$env:AWS_SDK_LOAD_CONFIG='1'
npm.cmd run package:agentcore:dev -- --aws-profile xoc-dev --package tests/ic1/.build/ac2-20261010-resume/package
```

- Real Serverless package: PASS, 68 seconds.
- Linux artifact import, static IAM, four-tool allowlist, no public bucket,
  acyclic template graph and artifact secret-pattern scan: PASS.
- ZIP is byte-identical to the previously validated artifact:
  `cc5869f79a36ab2333f8a02581c3ada4bb300d803ba8b85ff48cdd5226a491ed`.
- Dependency versions unchanged; no runtime/IaC/test code was modified.
- New template SHA256:
  `e3fd694ced47b24c6a422aed5d95fa83208439bddb56840192a1177dbac34412`.
- Deep comparison: the ONLY template change is the generated timestamp prefix
  in Lambda Code.S3Key. No new resources, permission or configuration drift.
- Recursive CFN specification validation: PASS, previously fetched same-day
  version 267.0.0 reused.
- Real `aws cloudformation validate-template --profile xoc-dev --region us-east-1`
  succeeded; reports CAPABILITY_IAM and no parameters. This does not prove
  creation permissions, service acceptance of all properties or live IAM.
- Ten planned resources, zero unexpected resources, zero resources created.
- Closed local suites were NOT unnecessarily repeated: no relevant code changed.
  Prior 32/32 AC-1, 27/27 Linux, 4/4 packaging, 32 HTTP, 120/120 focal and
  285/287 full (two unchanged VICTOR failures) remain the latest regression evidence.

### Stop decision and precise next dependency

The existing AC-2 contract requires real existing DEV providers and forbids
creating substitute business stores or using PROD. An absent table is not an
empty authorized query result or controlled ticket-not-found. Deploying Chat
would require bootstrapping missing shared/auth/data/network resources and a
configured SOPHIA DEV endpoint. That is not an automatic IaC repair in this block.

Therefore no deployment was attempted and no retained KMS key was created. The
independent package and template gates were completed before stopping. This is
not a rollback situation: nothing was activated or changed in AWS.

To resume safely, provide the mapping to an existing isolated XOC DEV environment
if it has different names, or explicitly authorize a separately planned bootstrap
of the missing XOC DEV dependencies, with no PROD data/credentials reuse. A new
region/account must not be assumed or chosen automatically. No AWS secret is
needed in chat. Once prerequisites exist, repeat their inventory, then begin
the previously authorized AgentCore deploy/direct-smoke/Chat-disabled sequence.

All real smoke gates remain NOT RUN: KMS/transport, four providers, roles/tenant,
negative security, C.3, no fallback, second-tool stop, log review, cold/warm and
concurrent latency, errors/throttles, pagination, rollback and reactivation.
No cloud PASS is claimed from prior local mocks or from ValidateTemplate.

Evidence: ignored `tests/ic1/.build/ac2-20261010-resume/` with sanitized inventory,
real package, dependency/import manifest and schema validation. Only this report
is changed in Git during resumption. Feature push remains withheld because the
user conditioned it on AC-2 PASS. PROD/Voice/VICTOR untouched.

## Historical first attempt (before xoc-dev became available)

No cloud deployment, activation, rollback, push or production change occurred.
This report must not be interpreted as evidence that the live integration works.

## External blocker: AWS identity

- Initial branch: `feature/sophia-agentcore-ac1`.
- Initial HEAD / AC-1 documentation: `d2070df5cff6f06a42ddab2d8ccd98d50ddc9af3`.
- AC-1 implementation: `8c11578446d7a272b05fe6e38cbd9edb4ac50b8e`.
- Work branch: `feature/sophia-agentcore-ac2`; no main/master changes.
- Default `aws sts get-caller-identity`: `NoCredentials: Unable to locate credentials`.
- `aws configure list-profiles`: only `fernando-dev`.
- Explicit STS on that profile succeeds as
  `arn:aws:iam::8830****8334:user/aldebaran-dev`.
- That is NOT the previously identified XOC account `8117****6524`.
- Read-only CLI inventory in that profile returned no gateways or DEV tables,
  and only three deleted FernandoApp DEV stacks. It is NOT an XOC inventory.
- No environment AWS credential chain was configured. No credentials were
  copied from another host, fabricated, printed or written into the repository.

Classification: **AWS_IDENTITY_BLOCKER**, not an IAM `AccessDenied` and not a
regional service failure. No policy change can safely substitute for selecting
the correct account. No deployment was attempted under the unrelated identity.

To unblock: the operator must enable a locally authenticated profile/role/session
for XOC DEV. Supply its profile name, not keys or tokens in chat. Re-run STS,
verify the account, then inventory DEV before any mutation. Availability of
XOC DEV Chat, tables, indexes, runtime endpoint and test identities is still
unknown; they must NOT be inferred from PROD or the unrelated account.

## Verified toolchain and documentation

Windows PowerShell; Node 22.13.1; npm 10.9.2; Serverless Framework 3.40.0;
AWS CLI 2.36.40; host Python 3.13.1; Docker Linux engine available.

Configured region `us-east-1`, stage `dev`. Gateway is supported there according
to the [AWS regional matrix](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html).
Account-specific quotas and permissions remain unverified.

Rechecked on the task date:

- [Gateway CFN](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-gateway.html)
  and [GatewayTarget CFN](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-gatewaytarget.html).
- [SchemaDefinition](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrockagentcore-gatewaytarget-schemadefinition.html):
  capitalized CFN `Type`, `Properties`, `Required` retained.
- [MCP use](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using.html)
  and [tool call](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-call.html):
  2026-07-28 is stateless, has no initialize handshake, uses server/discover,
  version header and `_meta`, plus Mcp-Method/Mcp-Name. AC-1 already implements
  these; no protocol rewrite was necessary.
- [IAM](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-prerequisites-permissions.html):
  same-account Lambda invocation uses the exact target identity permission.
- [KMS key CFN](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-kms-key.html)
  and [HMAC](https://docs.aws.amazon.com/kms/latest/developerguide/hmac.html).

Downloaded current public CFN specification: version `267.0.0`, SHA256
`90869779742fd2cfc4fdb07eea3cc7fd98bfa1ed3d6aa83d6ae1c7cfa806c90b`.
Recursive resource/property validation passed. It matches the AC-1 spec hash.
This is schema validation, not service-side deployment validation.

## Real packaging and corrections

Executed the existing script, not the source-only offline profile:

```powershell
npm.cmd run package:agentcore:dev -- --package tests/ic1/.build/ac2-20261010/package-final
```

First real package succeeded as a Serverless operation but failed Linux import:
python-requirements injects Windows `path.join()` names with backslashes into
the ZIP. Windows ZIP inspection normalized these names on read and masked the
problem; Linux could not import `sqlalchemy`. It was present with the wrong path.

Correction is scoped to `serverless.agentcore.js` only:

1. A local Serverless plugin runs after python-requirements injection, normalizes
   ZIP names to POSIX before Serverless completes packaging, rejects traversal
   and collisions, preserves file contents and does nothing on an already POSIX ZIP.
2. Keep distribution `.dist-info` metadata for dependency provenance and imports;
   continue removing bytecode, cache and library test directories.
3. Explicit `deploymentBucket.blockPublicAccess=true` for this service.

No node_modules patch, alternative deployment framework, manual ZIP deployment,
global dependency upgrade, runtime code change or shared packaging refactor.

The artifact inspector mounts only the ZIP and its script read-only into the
Lambda Python 3.11 Linux image, disables network, uses bounded temporary storage,
and imports the actual private target handler. It also validates the resource
allowlist, IAM actions, four tools, sizes, dependency graph and secret-pattern scan.
`certifi/cacert.pem` is an explicitly permitted public CA bundle, not a private key.
Tests of the inspector initially exposed its own script-name shadowing and tmpfs
execution restrictions; both were corrected in the harness, not in runtime code.

The corrected package imports successfully under Python 3.11.16. Its dependency
manifest is retained in ignored `package-final-inspection.json`; versions include
requests 2.34.2, SQLAlchemy 2.1.4, aws-lambda-powertools 3.35.0, PyJWT 2.15.1,
cryptography 44.0.2, pydantic 2.14.0, fastapi 0.143.0, Werkzeug 3.1.9.
boto3/botocore are intentionally excluded by the existing packaging policy:
the tested Lambda image provides both at 1.42.97. The eventual AWS-managed runtime
versions must be measured again; local image versions are not cloud evidence.
Requirements ranges remain unchanged, so a future package can resolve differently.

Final ZIP: 21,963,883 bytes; 55,032,729 bytes uncompressed; 1,509 entries.
SHA256 `cc5869f79a36ab2333f8a02581c3ada4bb300d803ba8b85ff48cdd5226a491ed`.
Final compiled template SHA256
`329d2c6b8e27d46ce2cdf00fa59ec17d80124dc236e1e4ffe1a9067e159341e5`.
Lambda version CodeSha256 matches the final normalized ZIP, confirming that
Serverless hashed the corrected artifact rather than its Windows predecessor.
All four bucket PublicAccessBlock settings are true in the compiled template.

## Infrastructure and IAM

Planned stack `xoc-api-agentcore-dev`, private function
`xoc-api-agentcore-dev-read`. Exactly four `xoc-read___` tools:
tickets-list, tickets-get, documents-list, notifications-unread-count.

Real compiled template contains ten expected resources: Gateway, GatewayTarget,
KMS key, two IAM roles, Lambda function, Lambda version, log group, Serverless
deployment S3 bucket and its bucket policy. The last two are absent in AC-1's
offline template because it explicitly supplied a dummy bucket name. They are
framework deployment infrastructure, not public application endpoints.

Other template differences from AC-1 offline: service name, real deployment
bucket references, artifact object key/hash, generated Lambda version identifier,
removal of the offline-only environment marker, and explicit S3 public-access
blocking. No new runtime architecture or business-resource change.

Static IAM PASS:

- Chat opt-in policy: exact Gateway `InvokeGateway`, exact key `GenerateMac`.
- Gateway role: exact private target `lambda:InvokeFunction` only.
- Target: exact key `VerifyMac`; exact DEV table/index `GetItem`/`Query`;
  own log group stream creation/log events. No GenerateMac, DynamoDB writes or Scan.
- Existing Chat role has older broad DEV permissions and is shared with Voice;
  the new additive policy does not remove or expand those existing permissions.
  Never claim the entire legacy Chat role is READ_ONLY.
- Scoped wildcards: Gateway name suffix in trust for generated ID; own log streams;
  KMS key-policy `Resource:*` means the attached key. No wildcard service action.
- KMS HMAC_256 / GENERATE_VERIFY_MAC retains on deletion/replacement. No key created.
- No EC2, ECS, Fargate, ALB, NAT, RDS, OpenSearch, new secret, public API or Function URL.

Effective cloud IAM, AWS resource state and CloudWatch security are NOT VERIFIED.

## Regression evidence

All evidence below is local; the required cloud-first order could not be followed
because identity preflight failed. Regressions were run independently, not waived.

| Check | Result |
|---|---|
| AC-1 Windows, including final packaging config checks | 32/32 PASS |
| AC-1 Python Linux | 27/27 PASS; Node infrastructure class skipped in Python image, tested on Windows |
| New packaging unit tests | 4/4 PASS |
| IC-1 actual local HTTP integration | 32 calls PASS; no failures/errors; no stdio |
| Backend focal | 120/120 PASS |
| Backend full, run once | 285/287 PASS; same two historical failures |
| Cloud smoke / rollback | NOT RUN |

Unchanged historical failures, compared by test ID, category and source location:

- `test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global`, AssertionError, line 50.
- `test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy`, AssertionError, line 125.

The isolated full suite records three blocked network attempts and zero secret-file
accesses, matching baseline. AC-1/focal have zero network and secret-file attempts.
No tests contacted VICTOR or Voice services. XOC-MCP was used read-only as the existing
local test baseline; no file in that repo was edited.

## Live gates — all pending, not inferred from mocks

Gateway creation/READY, target synchronization, deployed protocol versions,
KMS sign/verify, effective role permissions, discovery, direct smoke, disabled
Chat smoke, activation, normal Chat, four tools, roles, tenant isolation, tampered/
expired/wrong-audience/wrong-tool/wrong-digest/wrong-request contexts, unknown/
disabled/write tools, no fallback, second-tool stop, C.3, log security, pagination,
cold/warm/concurrent timings, errors/throttles and rollback remain NOT RUN.

Local tests cover those applicable code paths but cannot prove the live gates.
No Gateway ARN, URL, key ARN or deployed commit is invented in this report.

## Resume sequence after identity is corrected

1. STS account gate; DEV-only metadata inventory of stacks, Chat role, tables and
   indexes, endpoints and logs. Confirm external SOPHIA DEV runtime contract and
   governed flags; current DEV stage config has an empty runtime base URL.
2. Repackage through the same script and re-run artifact/schema/IAM checks.
3. `npm.cmd run deploy:agentcore:dev` with the verified credential chain.
   On failure inspect events. If a retained key exists, reconcile before retry;
   never delete keys or blindly recreate the stack.
4. Verify outputs and target state; real signed direct four-tool/negative smoke.
5. Deploy Chat DEV provisioned with execution disabled, verify normal Chat and C.3.
6. Activate agentcore only after those gates pass, retaining governed 18s/20s limits.
7. Live tool/security/pagination/timing/second-turn smoke; no data preparation writes.
8. Exercise disabled rollback, verify normal Chat, then reactivate only if safe.
9. Regressions and secret/diff review; push only the feature branch after AC-2 PASS.

Rollback is a plan, NOT tested here: provisioned=true, execution=disabled,
redeploy Chat DEV through its existing script, verify normal Chat; never direct
ToolExecutor/LocalMCP/stdio fallback. Retain the AgentCore stack and KMS key.

## Evidence, risks and handoff

Ignored root: `tests/ic1/.build/ac2-20261010/`. Contains sanitized preflight,
initial/fixed/final real packages, artifact manifest, spec validation and regression
JSON. No credentials or raw cloud payloads are retained. Failed initial ZIP is
preserved only as local evidence and must NOT be deployed.

HIGH: correct-account access missing; live security/performance/rollback unproven.
MEDIUM: dependency ranges are not pinned; runtime SDK may differ; Chat shared IAM;
cold starts and KMS latency must fit the existing budget. No timeout was increased.
LOW: local plugin maintenance against the installed python-requirements lifecycle.

Implementation changes are restricted to packaging/test tooling. AC-1 runtime
remains intact. PROD, Voice, VICTOR, Step Functions, frontend/mobile and Azure
were not modified. No push: user explicitly conditioned it on AC-2 PASS.

Local implementation commit: `ee449d483937d467d3cbc7594f402a981ef83f72`
(`fix: make AgentCore Serverless packages importable on Linux`). This report
is committed separately. No runtime/infrastructure deployment has that revision.
