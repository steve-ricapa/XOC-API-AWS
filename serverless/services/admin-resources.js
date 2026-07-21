module.exports = function adminResources(stage) {
  return {
    Resources: {
      TenantDeletionQueue: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-admin-${stage}-tenant-deletion`,
          VisibilityTimeout: 300,
          MessageRetentionPeriod: 86400,
          RedrivePolicy: {
            deadLetterTargetArn: { 'Fn::GetAtt': ['TenantDeletionDlq', 'Arn'] },
            maxReceiveCount: 3,
          },
        },
      },
      TenantDeletionDlq: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-admin-${stage}-tenant-deletion-dlq`,
          MessageRetentionPeriod: 1209600,
        },
      },
    },
    Outputs: {
      TenantDeletionQueueUrl: {
        Value: { Ref: 'TenantDeletionQueue' },
      },
      TenantDeletionQueueArn: {
        Value: { 'Fn::GetAtt': ['TenantDeletionQueue', 'Arn'] },
      },
    },
  };
};
