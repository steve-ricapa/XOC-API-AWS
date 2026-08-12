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
