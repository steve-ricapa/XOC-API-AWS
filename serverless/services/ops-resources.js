const { stageRef } = require('./lib/common');

module.exports = function opsResources(stage) {
  return {
    Resources: {
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
