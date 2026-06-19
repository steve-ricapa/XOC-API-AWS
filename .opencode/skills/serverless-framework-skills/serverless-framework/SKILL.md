---

name: serverless-framework
description: build, review, refactor, and debug aws serverless framework projects using serverless.yml, aws lambda, api gateway httpapi/rest, eventbridge, sqs, sns, s3 events, schedules, dynamodb/kinesis streams, iam permissions, environment variables, packaging, local invocation, deployment, logs, and production reliability patterns. use when creating or modifying serverless framework services, lambdas, event-driven architectures, cloudformation resources, ci/cd deploy commands, or troubleshooting serverless framework configuration and aws event integrations.
--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# Serverless Framework

Use this skill to design, implement, review, and troubleshoot AWS Serverless Framework services.

## Start every task

1. Inspect the repository before editing:

   * `serverless.yml`
   * `serverless.ts`
   * `package.json`
   * `src/`
   * `handler.*`
   * plugins
   * CI/CD files
2. Identify the user's goal:

   * create a new service
   * add a Lambda
   * add an event trigger
   * fix `serverless.yml`
   * deploy
   * test locally
   * review architecture
3. Preserve the existing framework version and project style unless the user asks to migrate.
4. Prefer AWS provider patterns.
5. Always think about reliability: retries, DLQ, idempotency, logs, timeout, and IAM.

## Default workflow

When building or modifying a feature, provide:

1. Files to create or change.
2. Architecture flow.
3. `serverless.yml` changes.
4. Lambda handler code.
5. Event payload example.
6. IAM permissions.
7. Environment variables.
8. Local test command.
9. Deploy command.
10. Logs/debug command.
11. Risks or assumptions.

## Service design rules

Prefer:

* Small Lambda functions with one clear responsibility.
* `httpApi` for new HTTP APIs unless REST API v1 features are required.
* EventBridge for domain events and cross-service routing.
* SQS for buffering, retries, backpressure, and async workers.
* SNS for fan-out pub/sub.
* S3 events for file/object processing.
* DynamoDB Streams for reacting to table changes.
* Step Functions for multi-step workflows.
* CloudFormation `resources` for infrastructure not directly modeled by Serverless Framework events.

Avoid:

* One Lambda doing unrelated tasks.
* Hardcoded secrets.
* Hardcoded account IDs.
* Hardcoded regions.
* `Resource: "*"` unless absolutely necessary.
* Swallowing errors in event consumers.
* Missing DLQs for critical async flows.
* Missing idempotency for async consumers.

## Lambda handler rules

For HTTP handlers:

* Validate input.
* Return `statusCode`.
* Return stringified `body`.
* Include useful error responses.
* Log with structured JSON.

For async/event handlers:

* Validate event shape.
* Make processing idempotent.
* Throw errors when processing fails so AWS retry/DLQ behavior works.
* Do not silently catch and ignore errors.
* Add correlation IDs when possible.

## Event-driven rules

For each async feature, identify:

1. Producer.
2. Event transport.
3. Consumer.
4. Event schema.
5. Retry behavior.
6. DLQ behavior.
7. Idempotency key.
8. Observability strategy.

Use past-tense event names:

* `OrderCreated`
* `PaymentApproved`
* `InvoiceGenerated`
* `ReportRequested`
* `UserRegistered`

Avoid vague event names:

* `ProcessData`
* `DoTask`
* `UpdateThing`

## Review checklist

When reviewing a Serverless Framework project, check:

1. Is the provider configured correctly?
2. Are stage and region dynamic?
3. Do handlers point to real exported functions?
4. Are event triggers valid?
5. Are IAM permissions least privilege?
6. Are environment variables safe?
7. Are secrets handled correctly?
8. Are timeouts and memory explicit?
9. Are async consumers idempotent?
10. Do critical async flows have DLQs?
11. Are SQS batch failures handled correctly?
12. Are logs useful for debugging?
13. Are deploy and local test commands documented?

## Useful commands

Use these when relevant:

```bash
serverless deploy --stage dev
serverless deploy function --function myFunction --stage dev
serverless remove --stage dev
serverless info --stage dev
serverless logs --function myFunction --stage dev
serverless invoke local --function myFunction --path event.json
serverless package --stage dev
```

If the project uses `sls`, the equivalent commands are acceptable:

```bash
sls deploy --stage dev
sls invoke local --function myFunction --path event.json
sls logs --function myFunction --stage dev
```

## Response format

When implementing something, respond with:

1. Files to change
2. Architecture
3. Code
4. `serverless.yml`
5. IAM/security notes
6. Testing
7. Deploy/logs
8. Risks
