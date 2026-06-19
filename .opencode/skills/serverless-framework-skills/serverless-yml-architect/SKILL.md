---

name: serverless-yml-architect
description: create, improve, review, and refactor serverless.yml files for aws serverless framework services. use when the task is specifically about serverless.yml structure, provider configuration, functions, httpapi/rest api events, eventbridge, sqs, sns, s3 triggers, schedules, dynamodb resources, iam permissions, environment variables, stages, custom variables, packaging, plugins, outputs, or cloudformation resources.
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# Serverless YAML Architect

Use this skill when the user needs a clean, production-ready `serverless.yml`.

## Main goal

Produce `serverless.yml` files that are:

* readable
* stage-aware
* secure
* easy to deploy
* easy to debug
* aligned with AWS Serverless Framework best practices

## Recommended structure

Use this order:

```yml
service: orders-service
frameworkVersion: "4"

provider:
  name: aws
  runtime: nodejs20.x
  region: ${opt:region, 'us-east-1'}
  stage: ${opt:stage, 'dev'}

custom:
  # reusable names and stage-aware values

package:
  # packaging rules

functions:
  # lambda functions and triggers

resources:
  Resources:
    # cloudformation resources
  Outputs:
    # exported values
```

## Best-practice template

Use this as the default shape for new services:

```yml
service: orders-service
frameworkVersion: "4"

provider:
  name: aws
  runtime: nodejs20.x
  region: ${opt:region, 'us-east-1'}
  stage: ${opt:stage, 'dev'}
  timeout: 10
  memorySize: 256
  logRetentionInDays: 14

  environment:
    STAGE: ${sls:stage}
    REGION: ${aws:region}
    ORDERS_TABLE_NAME: ${self:custom.ordersTableName}
    EVENT_BUS_NAME: ${self:custom.eventBusName}

  iam:
    role:
      statements:
        - Effect: Allow
          Action:
            - dynamodb:PutItem
            - dynamodb:GetItem
          Resource:
            - !GetAtt OrdersTable.Arn

        - Effect: Allow
          Action:
            - events:PutEvents
          Resource:
            - !GetAtt AppEventBus.Arn

custom:
  ordersTableName: ${self:service}-${sls:stage}-orders
  eventBusName: ${self:service}-${sls:stage}-bus
  orderCreatedQueueName: ${self:service}-${sls:stage}-order-created-queue
  orderCreatedDlqName: ${self:service}-${sls:stage}-order-created-dlq

package:
  individually: true
  patterns:
    - "!node_modules/.cache/**"
    - "!tests/**"
    - "!README.md"
    - "!coverage/**"

functions:
  createOrder:
    handler: src/functions/create-order.handler
    description: Creates an order and emits an OrderCreated event
    timeout: 10
    memorySize: 256
    events:
      - httpApi:
          path: /orders
          method: post

  processOrderCreated:
    handler: src/functions/process-order-created.handler
    description: Processes OrderCreated events from EventBridge
    timeout: 30
    memorySize: 512
    events:
      - eventBridge:
          eventBus: !Ref AppEventBus
          pattern:
            source:
              - app.orders
            detail-type:
              - OrderCreated

  processOrderQueue:
    handler: src/functions/process-order-queue.handler
    description: Processes order messages from SQS
    timeout: 30
    memorySize: 512
    events:
      - sqs:
          arn: !GetAtt OrderCreatedQueue.Arn
          batchSize: 10
          maximumBatchingWindow: 5
          functionResponseType: ReportBatchItemFailures

resources:
  Resources:
    AppEventBus:
      Type: AWS::Events::EventBus
      Properties:
        Name: ${self:custom.eventBusName}

    OrdersTable:
      Type: AWS::DynamoDB::Table
      Properties:
        TableName: ${self:custom.ordersTableName}
        BillingMode: PAY_PER_REQUEST
        AttributeDefinitions:
          - AttributeName: orderId
            AttributeType: S
        KeySchema:
          - AttributeName: orderId
            KeyType: HASH

    OrderCreatedQueue:
      Type: AWS::SQS::Queue
      Properties:
        QueueName: ${self:custom.orderCreatedQueueName}
        VisibilityTimeout: 60
        RedrivePolicy:
          deadLetterTargetArn: !GetAtt OrderCreatedDlq.Arn
          maxReceiveCount: 3

    OrderCreatedDlq:
      Type: AWS::SQS::Queue
      Properties:
        QueueName: ${self:custom.orderCreatedDlqName}

  Outputs:
    OrdersTableName:
      Value: !Ref OrdersTable

    AppEventBusName:
      Value: !Ref AppEventBus

    OrderCreatedQueueUrl:
      Value: !Ref OrderCreatedQueue

    OrderCreatedQueueArn:
      Value: !GetAtt OrderCreatedQueue.Arn
```

## Variable rules

Prefer:

```yml
stage: ${opt:stage, 'dev'}
region: ${opt:region, 'us-east-1'}
```

Use:

```yml
${sls:stage}
${aws:region}
${aws:accountId}
${self:custom.value}
${env:VALUE}
${param:VALUE}
```

Avoid hardcoding:

```yml
dev
prod
us-east-1
123456789012
arn:aws:...
```

unless the resource is intentionally external.

## Function rules

Every function should have:

```yml
functionName:
  handler: src/functions/name.handler
  description: Clear description
  timeout: 10
  memorySize: 256
  events:
    - ...
```

Use business names:

```yml
createOrder:
processPayment:
generateReport:
processOrderCreated:
```

Avoid:

```yml
handler:
main:
lambda1:
processData:
```

## HTTP API example

```yml
functions:
  createOrder:
    handler: src/functions/create-order.handler
    events:
      - httpApi:
          path: /orders
          method: post
```

Prefer `httpApi` for new simple APIs.

Use REST API only when the project needs REST API v1 features.

## EventBridge example

```yml
functions:
  processOrderCreated:
    handler: src/functions/process-order-created.handler
    events:
      - eventBridge:
          eventBus: !Ref AppEventBus
          pattern:
            source:
              - app.orders
            detail-type:
              - OrderCreated
```

Use EventBridge for domain events and service-to-service communication.

## SQS example

```yml
functions:
  processQueue:
    handler: src/functions/process-queue.handler
    timeout: 30
    events:
      - sqs:
          arn: !GetAtt MainQueue.Arn
          batchSize: 10
          maximumBatchingWindow: 5
          functionResponseType: ReportBatchItemFailures
```

For SQS:

* Visibility timeout should be greater than Lambda timeout.
* Use DLQ for important queues.
* Use partial batch failure when processing records independently.
* Make the consumer idempotent.

## S3 event example

```yml
functions:
  processUpload:
    handler: src/functions/process-upload.handler
    events:
      - s3:
          bucket: ${self:custom.uploadsBucketName}
          event: s3:ObjectCreated:*
          rules:
            - prefix: uploads/
            - suffix: .pdf
```

Use prefix and suffix filters when possible.

## Schedule example

```yml
functions:
  dailyReport:
    handler: src/functions/daily-report.handler
    events:
      - schedule:
          rate: cron(0 12 * * ? *)
          enabled: true
```

## IAM rules

Prefer least privilege:

```yml
iam:
  role:
    statements:
      - Effect: Allow
        Action:
          - dynamodb:PutItem
        Resource:
          - !GetAtt OrdersTable.Arn
```

Avoid:

```yml
Action: "*"
Resource: "*"
```

Accept `Resource: "*"` only when AWS requires it or when scoping is impractical. Explain the reason.

## Environment variable rules

Good:

```yml
environment:
  STAGE: ${sls:stage}
  REGION: ${aws:region}
  TABLE_NAME: ${self:custom.tableName}
```

Bad:

```yml
environment:
  PASSWORD: my-password
  API_KEY: hardcoded-key
```

Secrets must come from a secret manager, CI/CD environment, SSM, or another secure source.

## Review checklist

When reviewing a `serverless.yml`, check:

1. Is `service` clear?
2. Is `frameworkVersion` defined?
3. Are `stage` and `region` dynamic?
4. Are reusable names in `custom`?
5. Are functions named by business action?
6. Are handlers real and consistent?
7. Are events correctly configured?
8. Are IAM permissions scoped?
9. Are secrets not hardcoded?
10. Are timeouts and memory explicit?
11. Are SQS DLQs configured?
12. Is package configuration clean?
13. Are CloudFormation resources named clearly?
14. Are useful outputs exposed?
15. Are deploy/test commands obvious?

## Output format

When asked to create or fix a `serverless.yml`, respond with:

1. Problems found
2. Recommended structure
3. Full corrected YAML
4. Explanation of important choices
5. Security/reliability notes
6. Commands to validate or deploy
