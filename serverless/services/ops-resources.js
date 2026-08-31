const { stageRef } = require('./lib/common');

module.exports = function opsResources(stage) {
  return {
    Resources: {
      DeviceRegistryTable: {
        Type: 'AWS::DynamoDB::Table',
        Properties: {
          TableName: `xoc-api-ops-${stage}-device-registry`,
          BillingMode: 'PAY_PER_REQUEST',
          AttributeDefinitions: [
            { AttributeName: 'PK', AttributeType: 'S' },
            { AttributeName: 'SK', AttributeType: 'S' },
          ],
          KeySchema: [
            { AttributeName: 'PK', KeyType: 'HASH' },
            { AttributeName: 'SK', KeyType: 'RANGE' },
          ],
        },
      },
      NotificationCampaignsTable: {
        Type: 'AWS::DynamoDB::Table',
        Properties: {
          TableName: `xoc-api-ops-${stage}-notification-campaigns`,
          BillingMode: 'PAY_PER_REQUEST',
          AttributeDefinitions: [
            { AttributeName: 'PK', AttributeType: 'S' },
            { AttributeName: 'SK', AttributeType: 'S' },
          ],
          KeySchema: [
            { AttributeName: 'PK', KeyType: 'HASH' },
            { AttributeName: 'SK', KeyType: 'RANGE' },
          ],
        },
      },
      NotificationEventInboxTable: {
        Type: 'AWS::DynamoDB::Table',
        Properties: {
          TableName: `xoc-api-ops-${stage}-notification-event-inbox`,
          BillingMode: 'PAY_PER_REQUEST',
          AttributeDefinitions: [
            { AttributeName: 'PK', AttributeType: 'S' },
            { AttributeName: 'SK', AttributeType: 'S' },
          ],
          KeySchema: [
            { AttributeName: 'PK', KeyType: 'HASH' },
            { AttributeName: 'SK', KeyType: 'RANGE' },
          ],
        },
      },
      UserNotificationInboxTable: {
        Type: 'AWS::DynamoDB::Table',
        Properties: {
          TableName: `xoc-api-ops-${stage}-user-notification-inbox`,
          BillingMode: 'PAY_PER_REQUEST',
          AttributeDefinitions: [
            { AttributeName: 'PK', AttributeType: 'S' },
            { AttributeName: 'SK', AttributeType: 'S' },
            { AttributeName: 'GSI1PK', AttributeType: 'S' },
            { AttributeName: 'GSI1SK', AttributeType: 'S' },
            { AttributeName: 'GSI2PK', AttributeType: 'S' },
            { AttributeName: 'GSI2SK', AttributeType: 'S' },
          ],
          KeySchema: [
            { AttributeName: 'PK', KeyType: 'HASH' },
            { AttributeName: 'SK', KeyType: 'RANGE' },
          ],
          GlobalSecondaryIndexes: [
            {
              IndexName: 'UserCreatedAtIndex',
              KeySchema: [
                { AttributeName: 'GSI1PK', KeyType: 'HASH' },
                { AttributeName: 'GSI1SK', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
            {
              IndexName: 'UserStatusCreatedAtIndex',
              KeySchema: [
                { AttributeName: 'GSI2PK', KeyType: 'HASH' },
                { AttributeName: 'GSI2SK', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
          ],
        },
      },
      NotificationEventsBus: {
        Type: 'AWS::Events::EventBus',
        Properties: {
          Name: `xoc-api-ops-${stage}-notifications-bus`,
        },
      },
      NotificationEventsDlq: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-ops-${stage}-notification-events-dlq`,
          MessageRetentionPeriod: 1209600,
        },
      },
      NotificationEventsQueue: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-ops-${stage}-notification-events`,
          VisibilityTimeout: 120,
          MessageRetentionPeriod: 345600,
          RedrivePolicy: {
            deadLetterTargetArn: { 'Fn::GetAtt': ['NotificationEventsDlq', 'Arn'] },
            maxReceiveCount: 3,
          },
        },
      },
      NotificationEventsRule: {
        Type: 'AWS::Events::Rule',
        Properties: {
          Name: `xoc-api-ops-${stage}-notification-requested`,
          EventBusName: { Ref: 'NotificationEventsBus' },
          EventPattern: {
            source: ['xoc.notifications'],
            'detail-type': ['xoc.notification.requested'],
          },
          Targets: [
            {
              Arn: { 'Fn::GetAtt': ['NotificationEventsQueue', 'Arn'] },
              Id: 'NotificationEventsQueueTarget',
              RoleArn: { 'Fn::GetAtt': ['NotificationEventsEventBridgeRole', 'Arn'] },
              InputTransformer: {
                InputPathsMap: { detail: '$.detail' },
                InputTemplate: '{"detail": <detail>}',
              },
            },
          ],
        },
      },
      NotificationEventsEventBridgeRole: {
        Type: 'AWS::IAM::Role',
        Properties: {
          AssumeRolePolicyDocument: {
            Version: '2012-10-17',
            Statement: [{ Effect: 'Allow', Principal: { Service: 'events.amazonaws.com' }, Action: 'sts:AssumeRole' }],
          },
          Policies: [
            {
              PolicyName: 'NotificationEventsToSqsPolicy',
              PolicyDocument: {
                Version: '2012-10-17',
                Statement: [{
                  Effect: 'Allow',
                  Action: ['sqs:SendMessage'],
                  Resource: [{ 'Fn::GetAtt': ['NotificationEventsQueue', 'Arn'] }],
                }],
              },
            },
          ],
        },
      },
      NotificationEventsQueuePolicy: {
        Type: 'AWS::SQS::QueuePolicy',
        Properties: {
          Queues: [{ Ref: 'NotificationEventsQueue' }],
          PolicyDocument: {
            Version: '2012-10-17',
            Statement: [{
              Effect: 'Allow',
              Principal: { Service: 'events.amazonaws.com' },
              Action: 'sqs:SendMessage',
              Resource: { 'Fn::GetAtt': ['NotificationEventsQueue', 'Arn'] },
              Condition: {
                ArnEquals: { 'aws:SourceArn': { 'Fn::GetAtt': ['NotificationEventsRule', 'Arn'] } },
              },
            }],
          },
        },
      },
      SnapshotIngestDlq: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-ops-${stage}-snapshot-ingest-dlq`,
          MessageRetentionPeriod: 1209600,
        },
      },
      SnapshotIngestQueue: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-ops-${stage}-snapshot-ingest`,
          VisibilityTimeout: 720,
          MessageRetentionPeriod: 345600,
          RedrivePolicy: {
            deadLetterTargetArn: { 'Fn::GetAtt': ['SnapshotIngestDlq', 'Arn'] },
            maxReceiveCount: 5,
          },
        },
      },
      SnapshotIngestQueuePolicy: {
        Type: 'AWS::SQS::QueuePolicy',
        Properties: {
          Queues: [{ Ref: 'SnapshotIngestQueue' }],
          PolicyDocument: {
            Version: '2012-10-17',
            Statement: [
              {
                Effect: 'Allow',
                Principal: { Service: 's3.amazonaws.com' },
                Action: 'sqs:SendMessage',
                Resource: { 'Fn::GetAtt': ['SnapshotIngestQueue', 'Arn'] },
                Condition: {
                  ArnEquals: {
                    'aws:SourceArn': stageRef(stage, 'snapshotsBucketArn'),
                  },
                },
              },
            ],
          },
        },
      },
    },
    Outputs: {
      DeviceRegistryTableName: {
        Value: { Ref: 'DeviceRegistryTable' },
      },
      DeviceRegistryTableArn: {
        Value: { 'Fn::GetAtt': ['DeviceRegistryTable', 'Arn'] },
      },
      NotificationCampaignsTableName: {
        Value: { Ref: 'NotificationCampaignsTable' },
      },
      NotificationCampaignsTableArn: {
        Value: { 'Fn::GetAtt': ['NotificationCampaignsTable', 'Arn'] },
      },
      NotificationEventInboxTableName: {
        Value: { Ref: 'NotificationEventInboxTable' },
      },
      NotificationEventInboxTableArn: {
        Value: { 'Fn::GetAtt': ['NotificationEventInboxTable', 'Arn'] },
      },
      NotificationEventsBusName: {
        Value: { Ref: 'NotificationEventsBus' },
      },
      NotificationEventsBusArn: {
        Value: { 'Fn::GetAtt': ['NotificationEventsBus', 'Arn'] },
      },
      NotificationEventsQueueUrl: {
        Value: { Ref: 'NotificationEventsQueue' },
      },
      NotificationEventsQueueArn: {
        Value: { 'Fn::GetAtt': ['NotificationEventsQueue', 'Arn'] },
      },
      NotificationEventsDlqUrl: {
        Value: { Ref: 'NotificationEventsDlq' },
      },
      NotificationEventsDlqArn: {
        Value: { 'Fn::GetAtt': ['NotificationEventsDlq', 'Arn'] },
      },
      SnapshotIngestQueueUrl: {
        Value: { Ref: 'SnapshotIngestQueue' },
      },
      SnapshotIngestQueueArn: {
        Value: { 'Fn::GetAtt': ['SnapshotIngestQueue', 'Arn'] },
      },
      SnapshotIngestDlqUrl: {
        Value: { Ref: 'SnapshotIngestDlq' },
      },
      SnapshotIngestDlqArn: {
        Value: { 'Fn::GetAtt': ['SnapshotIngestDlq', 'Arn'] },
      },
    },
  };
};
