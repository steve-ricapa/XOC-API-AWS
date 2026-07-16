# Multi-Service Serverless Split

This repo now includes 7 independent Serverless templates at the repo root:

- `serverless.shared.js`
- `serverless.tickets.js`
- `serverless.auth.js`
- `serverless.chat.js`
- `serverless.ops.js`
- `serverless.tenant.js`
- `serverless.admin.js`

## Ownership

1. `xoc-api-shared`
- HTTP API
- default stage
- request authorizer lambda + HTTP API authorizer

2. `xoc-api-tickets`
- tickets DynamoDB
- EventBridge bus/rule
- Step Functions workflow (placeholder V1)
- `/tickets/**`

3. `xoc-api-auth`
- `/health`
- `/auth/**`
- `/onboarding/tenant`

4. `xoc-api-chat`
- `/chat/**`
- `/agents/**`

5. `xoc-api-ops`
- `/scans/**`
- `/integrations/**`
- `/alerts/**`
- `/analytics/**`
- `/systems/**`
- `/vulnerabilities/**`

6. `xoc-api-tenant`
- `/tenant/**`
- `/users/**`
- `/audit`

7. `xoc-api-admin`
- `/admin/**`
- `/superadmin/**`

## Deploy Order

Because the domain stacks import the shared API id and authorizer id, deploy in this order:

1. `serverless.shared.js`
2. `serverless.tickets.js`
3. `serverless.auth.js`
4. `serverless.chat.js`
5. `serverless.tenant.js`
6. `serverless.admin.js`
7. `serverless.ops.js`

## Commands

Examples for `dev`:

```bash
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.shared.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.tickets.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.auth.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.chat.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.tenant.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.admin.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.ops.js
```

## Production Notes

- `xoc-api-tickets` enables DynamoDB PITR only in `prod`.
- The ticket Step Functions state machine is intentionally a placeholder in V1. It acknowledges ticket events but does not yet implement full orchestration, retries, or timeout handling.
- The production VPC currently uses a single NAT Gateway for cost control. That is acceptable for now, but it is not full multi-AZ egress HA.

## Network Expectations By Stage

- `dev`, `staging`, and `prod` are expected to keep the same single-AZ app topology.
- `xoc-api-auth`, `xoc-api-chat`, `xoc-api-ops`, `xoc-api-tenant`, and `xoc-api-admin` attach to VPC in every stage.
- `xoc-api-shared` and `xoc-api-tickets` stay outside VPC in every stage.
- App-tier Lambdas use a single private Lambda subnet per stage.
- DB networking still preserves two private DB subnets because the current RDS VPC subnet group flow expects them.
- Stage network stacks expected by the current stage files:
  - `xoc-infra-network-dev`
  - `xoc-infra-network-staging`
  - `xoc-infra-network-prod`

## Fast Iteration

For code-only changes after a stack already exists:

```bash
sls deploy function -f authApi --stage dev --config serverless.auth.js
```

Use full `deploy` whenever you change routes, permissions, resources, environment, or integrations.

## IAM Posture By Stage

- `dev`: intentionally relaxed IAM to reduce deployment and runtime friction while redesigning the platform.
- `staging`: follows the stricter resource-scoped shape unless explicitly loosened later.
- `prod`: resource-scoped IAM by domain.

Examples:

- `xoc-api-auth`, `xoc-api-chat`, `xoc-api-tenant`, `xoc-api-admin`, `xoc-api-ops` keep database-related access.
- `xoc-api-ops` keeps S3 snapshots access.
- `xoc-api-tickets` keeps DynamoDB + EventBridge access.
- VPC attachment remains `prod`-only for the services that need private RDS access.
