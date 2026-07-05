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
- Step Functions workflow
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

## Important Migration Note

These stacks create and attach to a new shared HTTP API managed by `xoc-api-shared`.
They do not mutate the old `xoc-api-core` stack.

That avoids route ownership conflicts while you migrate.

## Fast Iteration

For code-only changes after a stack already exists:

```bash
sls deploy function -f authApi --stage dev --config serverless.auth.js
```

Use full `deploy` whenever you change routes, permissions, resources, environment, or integrations.
