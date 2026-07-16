module.exports = function reportsResources(stage) {
  const pitrEnabled = stage === 'prod';

  return {
    Resources: {
      ReportsEventBus: {
        Type: 'AWS::Events::EventBus',
        Properties: {
          Name: `xoc-api-reports-${stage}-bus`,
        },
      },
      ReportsTable: {
        Type: 'AWS::DynamoDB::Table',
        Properties: {
          TableName: `xoc-api-reports-${stage}-reports`,
          BillingMode: 'PAY_PER_REQUEST',
          AttributeDefinitions: [
            { AttributeName: 'pk', AttributeType: 'S' },
            { AttributeName: 'sk', AttributeType: 'S' },
            { AttributeName: 'gsi1pk', AttributeType: 'S' },
            { AttributeName: 'gsi1sk', AttributeType: 'S' },
            { AttributeName: 'gsi2pk', AttributeType: 'S' },
            { AttributeName: 'gsi2sk', AttributeType: 'S' },
            { AttributeName: 'gsi3pk', AttributeType: 'S' },
            { AttributeName: 'gsi3sk', AttributeType: 'S' },
          ],
          KeySchema: [
            { AttributeName: 'pk', KeyType: 'HASH' },
            { AttributeName: 'sk', KeyType: 'RANGE' },
          ],
          GlobalSecondaryIndexes: [
            {
              IndexName: 'ReportLookupIndex',
              KeySchema: [
                { AttributeName: 'gsi1pk', KeyType: 'HASH' },
                { AttributeName: 'gsi1sk', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
            {
              IndexName: 'StatusIndex',
              KeySchema: [
                { AttributeName: 'gsi2pk', KeyType: 'HASH' },
                { AttributeName: 'gsi2sk', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
            {
              IndexName: 'TenantCreatedIndex',
              KeySchema: [
                { AttributeName: 'gsi3pk', KeyType: 'HASH' },
                { AttributeName: 'gsi3sk', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
          ],
          TimeToLiveSpecification: {
            AttributeName: 'ttl',
            Enabled: true,
          },
          PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: pitrEnabled },
          SSESpecification: { SSEEnabled: true },
        },
      },
      ReportRequestsQueue: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-reports-${stage}-report-requests`,
          VisibilityTimeout: 300,
          MessageRetentionPeriod: 86400,
          RedrivePolicy: {
            deadLetterTargetArn: { 'Fn::GetAtt': ['ReportRequestsDlq', 'Arn'] },
            maxReceiveCount: 3,
          },
        },
      },
      ReportRequestsDlq: {
        Type: 'AWS::SQS::Queue',
        Properties: {
          QueueName: `xoc-api-reports-${stage}-report-requests-dlq`,
          MessageRetentionPeriod: 1209600,
        },
      },
      ReportWorkflowStateMachine: {
        Type: 'AWS::StepFunctions::StateMachine',
        Properties: {
          StateMachineName: `xoc-api-reports-${stage}-report-workflow`,
          StateMachineType: 'STANDARD',
          DefinitionString: {
            'Fn::Sub': [
              JSON.stringify({
                Comment: 'Report generation workflow',
                StartAt: 'CollectReportData',
                States: {
                  CollectReportData: {
                    Type: 'Task',
                    Resource: '${CollectReportDataArn}',
                    Next: 'GenerateReportContent',
                    Retry: [
                      { ErrorEquals: ['Lambda.ServiceException', 'Lambda.AWSLambdaException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 },
                    ],
                    Catch: [
                      { ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'FailReport' },
                    ],
                  },
                  GenerateReportContent: {
                    Type: 'Task',
                    Resource: '${GenerateReportContentArn}',
                    Next: 'ValidateReport',
                    Retry: [
                      { ErrorEquals: ['Lambda.ServiceException', 'Lambda.AWSLambdaException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 },
                    ],
                    Catch: [
                      { ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'FailReport' },
                    ],
                  },
                  ValidateReport: {
                    Type: 'Task',
                    Resource: '${ValidateReportArn}',
                    Next: 'GenerateDocx',
                    Retry: [
                      { ErrorEquals: ['Lambda.ServiceException', 'Lambda.AWSLambdaException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 },
                    ],
                    Catch: [
                      { ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'FailReport' },
                    ],
                  },
                  GenerateDocx: {
                    Type: 'Task',
                    Resource: '${GenerateDocxArn}',
                    Next: 'CompleteReport',
                    Retry: [
                      { ErrorEquals: ['Lambda.ServiceException', 'Lambda.AWSLambdaException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 },
                    ],
                    Catch: [
                      { ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'FailReport' },
                    ],
                  },
                  CompleteReport: {
                    Type: 'Task',
                    Resource: '${CompleteReportArn}',
                    End: true,
                    Catch: [
                      { ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'FailReport' },
                    ],
                  },
                  FailReport: {
                    Type: 'Task',
                    Resource: '${CompleteReportArn}',
                    Parameters: {
                      'status': 'FAILED',
                      'error.$': '$.error',
                    },
                    End: true,
                  },
                },
              }),
              {
                CollectReportDataArn: { 'Fn::GetAtt': ['CollectReportDataLambdaFunction', 'Arn'] },
                GenerateReportContentArn: { 'Fn::GetAtt': ['GenerateReportContentLambdaFunction', 'Arn'] },
                ValidateReportArn: { 'Fn::GetAtt': ['ValidateReportLambdaFunction', 'Arn'] },
                GenerateDocxArn: { 'Fn::GetAtt': ['GenerateDocxLambdaFunction', 'Arn'] },
                CompleteReportArn: { 'Fn::GetAtt': ['CompleteReportLambdaFunction', 'Arn'] },
              },
            ],
          },
          RoleArn: { 'Fn::GetAtt': ['ReportWorkflowRole', 'Arn'] },
        },
      },
      ReportWorkflowRole: {
        Type: 'AWS::IAM::Role',
        Properties: {
          AssumeRolePolicyDocument: {
            Version: '2012-10-17',
            Statement: [{ Effect: 'Allow', Principal: { Service: 'states.amazonaws.com' }, Action: 'sts:AssumeRole' }],
          },
          Policies: [
            {
              PolicyName: 'ReportWorkflowPolicy',
              PolicyDocument: {
                Version: '2012-10-17',
                Statement: [
                  {
                    Effect: 'Allow',
                    Action: ['lambda:InvokeFunction'],
                    Resource: [
                      { 'Fn::GetAtt': ['CollectReportDataLambdaFunction', 'Arn'] },
                      { 'Fn::GetAtt': ['GenerateReportContentLambdaFunction', 'Arn'] },
                      { 'Fn::GetAtt': ['ValidateReportLambdaFunction', 'Arn'] },
                      { 'Fn::GetAtt': ['GenerateDocxLambdaFunction', 'Arn'] },
                      { 'Fn::GetAtt': ['CompleteReportLambdaFunction', 'Arn'] },
                    ],
                  },
                ],
              },
            },
          ],
        },
      },
      ReportEventRule: {
        Type: 'AWS::Events::Rule',
        Properties: {
          Name: `xoc-api-reports-${stage}-report-requested`,
          EventBusName: { Ref: 'ReportsEventBus' },
          EventPattern: {
            source: ['xoc.report'],
            'detail-type': ['report.requested'],
          },
          Targets: [
            {
              Arn: { 'Fn::GetAtt': ['ReportRequestsQueue', 'Arn'] },
              Id: 'ReportRequestsQueueTarget',
              InputTransformer: {
                InputPathsMap: {
                  detail: '$.detail',
                },
                InputTemplate: '{"detail": <detail>}',
              },
            },
          ],
        },
      },
      ReportEventToSqsRole: {
        Type: 'AWS::IAM::Role',
        Properties: {
          AssumeRolePolicyDocument: {
            Version: '2012-10-17',
            Statement: [{ Effect: 'Allow', Principal: { Service: 'events.amazonaws.com' }, Action: 'sts:AssumeRole' }],
          },
          Policies: [
            {
              PolicyName: 'ReportEventToSqsPolicy',
              PolicyDocument: {
                Version: '2012-10-17',
                Statement: [
                  {
                    Effect: 'Allow',
                    Action: ['sqs:SendMessage'],
                    Resource: [{ 'Fn::GetAtt': ['ReportRequestsQueue', 'Arn'] }],
                  },
                ],
              },
            },
          ],
        },
      },
      ReportRequestsQueuePolicy: {
        Type: 'AWS::SQS::QueuePolicy',
        Properties: {
          Queues: [{ Ref: 'ReportRequestsQueue' }],
          PolicyDocument: {
            Version: '2012-10-17',
            Statement: [
              {
                Effect: 'Allow',
                Principal: { Service: 'events.amazonaws.com' },
                Action: 'sqs:SendMessage',
                Resource: { 'Fn::GetAtt': ['ReportRequestsQueue', 'Arn'] },
                Condition: {
                  ArnEquals: {
                    'aws:SourceArn': { 'Fn::GetAtt': ['ReportEventRule', 'Arn'] },
                  },
                },
              },
            ],
          },
        },
      },
    },
    Outputs: {
      ReportsEventBusName: {
        Value: { Ref: 'ReportsEventBus' },
      },
      ReportsTableName: {
        Value: { Ref: 'ReportsTable' },
      },
      ReportsTableArn: {
        Value: { 'Fn::GetAtt': ['ReportsTable', 'Arn'] },
      },
      ReportRequestsQueueUrl: {
        Value: { Ref: 'ReportRequestsQueue' },
      },
      ReportRequestsQueueArn: {
        Value: { 'Fn::GetAtt': ['ReportRequestsQueue', 'Arn'] },
      },
      ReportWorkflowStateMachineArn: {
        Value: { Ref: 'ReportWorkflowStateMachine' },
      },
    },
  };
};
