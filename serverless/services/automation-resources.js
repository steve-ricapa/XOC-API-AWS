module.exports = function automationResources(stage) {
  const pitrEnabled = stage === 'prod';

  return {
    Resources: {
      AutomationCasesTable: {
        Type: 'AWS::DynamoDB::Table',
        Properties: {
          TableName: `xoc-api-automation-${stage}-cases-v2`,
          BillingMode: 'PAY_PER_REQUEST',
          AttributeDefinitions: [
            { AttributeName: 'sk', AttributeType: 'S' },
            { AttributeName: 'tenant_id', AttributeType: 'N' },
            { AttributeName: 'gsi1pk', AttributeType: 'S' },
            { AttributeName: 'gsi1sk', AttributeType: 'S' },
            { AttributeName: 'gsi2pk', AttributeType: 'S' },
            { AttributeName: 'gsi2sk', AttributeType: 'S' },
            { AttributeName: 'gsi3pk', AttributeType: 'S' },
            { AttributeName: 'gsi3sk', AttributeType: 'S' },
          ],
          KeySchema: [
            { AttributeName: 'sk', KeyType: 'HASH' },
            { AttributeName: 'tenant_id', KeyType: 'RANGE' },
          ],
          GlobalSecondaryIndexes: [
            {
              IndexName: 'TicketIndex',
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
              IndexName: 'TenantIndex',
              KeySchema: [
                { AttributeName: 'gsi3pk', KeyType: 'HASH' },
                { AttributeName: 'gsi3sk', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
          ],
          PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: pitrEnabled },
          SSESpecification: { SSEEnabled: true },
        },
      },
      AutomationWorkflowStateMachine: {
        Type: 'AWS::StepFunctions::StateMachine',
        Properties: {
          StateMachineName: `xoc-api-automation-${stage}-workflow`,
          StateMachineType: 'STANDARD',
          DefinitionString: {
            'Fn::Sub': [
              JSON.stringify({
                Comment: 'Ticket automation workflow: assess → search similar cases → generate plan → approval gate → check result → register case.',
                StartAt: 'AssessTicketAutomation',
                States: {
                  AssessTicketAutomation: {
                    Type: 'Task',
                    Resource: '${AssessTicketAutomationArn}',
                    Parameters: { 'ticketId.$': '$.input.ticketId', 'tenantId.$': '$.input.tenantId', 'subject.$': '$.input.subject', 'description.$': '$.input.description', phase: 'assessment' },
                    ResultPath: '$.assessment',
                    Next: 'CheckCanResolve',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  CheckCanResolve: {
                    Type: 'Choice',
                    Choices: [{ Variable: '$.assessment.canResolve', BooleanEquals: true, Next: 'SearchSimilarCases' }],
                    Default: 'EndCannotResolve',
                  },
                  SearchSimilarCases: {
                    Type: 'Task',
                    Resource: '${SearchSimilarCasesArn}',
                    Parameters: { 'ticketId.$': '$.input.ticketId', 'tenantId.$': '$.input.tenantId', 'subject.$': '$.input.subject' },
                    ResultPath: '$.similarCases',
                    Next: 'CheckSimilarCase',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  CheckSimilarCase: {
                    Type: 'Choice',
                    Choices: [{ Variable: '$.similarCases.similarCaseFound', BooleanEquals: true, Next: 'GenerateCaseFromSimilar' }],
                    Default: 'AssessTicketPlan',
                  },
                  AssessTicketPlan: {
                    Type: 'Task',
                    Resource: '${AssessTicketAutomationArn}',
                    Parameters: { 'ticketId.$': '$.input.ticketId', 'tenantId.$': '$.input.tenantId', 'subject.$': '$.input.subject', 'description.$': '$.input.description', phase: 'plan' },
                    ResultPath: '$.plan',
                    Next: 'InitializeAttemptCounter',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  GenerateCaseFromSimilar: {
                    Type: 'Task',
                    Resource: '${GenerateCaseArn}',
                    Parameters: { 'ticket_id.$': '$.input.ticketId', 'tenant_id.$': '$.input.tenantId', 'subject.$': '$.input.subject', action: 'success', total_attempts: 1, 'plan_used.$': '$.similarCases.similarCase.plan_used', 'solution_applied.$': '$.similarCases.similarCase.solution_applied', 'similar_case_id.$': '$.similarCases.similarCase.case_id' },
                    ResultPath: '$.caseResult',
                    Next: 'EndCaseRegistered',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  InitializeAttemptCounter: {
                    Type: 'Pass',
                    Parameters: { 'ticketId.$': '$.input.ticketId', 'tenantId.$': '$.input.tenantId', 'subject.$': '$.input.subject', 'description.$': '$.input.description', attemptCount: 1, solutionApplied: null, attemptsLog: [], 'maxRiskLevel.$': '$.plan.maxRiskLevel' },
                    ResultPath: '$.state',
                    Next: 'CheckAttemptsRemaining',
                  },
                  CheckAttemptsRemaining: {
                    Type: 'Choice',
                    Choices: [
                      { Variable: '$.state.attemptCount', NumericLessThan: 3, Next: 'WaitForApproval' },
                      { Variable: '$.state.attemptCount', NumericGreaterThanEquals: 3, Next: 'RegisterFailedCase' },
                    ],
                    Default: 'RegisterFailedCase',
                  },
                  WaitForApproval: {
                    Type: 'Task',
                    Resource: 'arn:aws:states:::lambda:invoke.waitForTaskToken',
                    Parameters: {
                      FunctionName: '${WaitForApprovalArn}',
                      Payload: { 'ticketId.$': '$.state.ticketId', 'tenantId.$': '$.state.tenantId', 'taskToken.$': '$$.Task.Token', 'maxRiskLevel.$': '$.state.maxRiskLevel' },
                    },
                    ResultPath: '$.approval',
                    Next: 'CheckApproval',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  CheckApproval: {
                    Type: 'Choice',
                    Choices: [{ Variable: '$.approval.approved', BooleanEquals: true, Next: 'ExecuteTicketPlan' }],
                    Default: 'RegisterRejectedCase',
                  },
                  ExecuteTicketPlan: {
                    Type: 'Task',
                    Resource: '${AssessTicketAutomationArn}',
                    Parameters: { 'ticketId.$': '$.state.ticketId', 'tenantId.$': '$.state.tenantId', 'subject.$': '$.state.subject', 'description.$': '$.state.description', 'plan.$': '$.plan.plan', phase: 'execute' },
                    ResultPath: '$.execution',
                    Next: 'CheckTicketStatus',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  CheckTicketStatus: {
                    Type: 'Task',
                    Resource: '${CheckTicketStatusArn}',
                    Parameters: { 'ticketId.$': '$.state.ticketId', 'tenantId.$': '$.state.tenantId' },
                    ResultPath: '$.statusCheck',
                    Next: 'IsTicketResolved',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  IsTicketResolved: {
                    Type: 'Choice',
                    Choices: [{ Variable: '$.statusCheck.resolutionStatus', StringEquals: 'resolved', Next: 'RegisterSuccessfulCase' }],
                    Default: 'IncrementAttempt',
                  },
                  IncrementAttempt: {
                    Type: 'Pass',
                    Parameters: {
                      'ticketId.$': '$.state.ticketId',
                      'tenantId.$': '$.state.tenantId',
                      'subject.$': '$.state.subject',
                      'description.$': '$.state.description',
                      'attemptCount.$': 'States.MathAdd($.state.attemptCount, 1)',
                      'solutionApplied.$': '$.statusCheck.solutionApplied',
                      'attemptsLog.$': '$.state.attemptsLog',
                      'maxRiskLevel.$': '$.state.maxRiskLevel',
                    },
                    ResultPath: '$.state',
                    Next: 'CheckAttemptsRemaining',
                  },
                  RegisterSuccessfulCase: {
                    Type: 'Task',
                    Resource: '${GenerateCaseArn}',
                    Parameters: { 'ticket_id.$': '$.state.ticketId', 'tenant_id.$': '$.state.tenantId', 'subject.$': '$.state.subject', action: 'success', 'total_attempts.$': '$.state.attemptCount', 'solution_applied.$': '$.statusCheck.solutionApplied', 'plan_used.$': '$.plan.plan' },
                    ResultPath: '$.caseResult',
                    Next: 'EndCaseRegistered',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  RegisterFailedCase: {
                    Type: 'Task',
                    Resource: '${GenerateCaseArn}',
                    Parameters: { 'ticket_id.$': '$.state.ticketId', 'tenant_id.$': '$.state.tenantId', 'subject.$': '$.state.subject', action: 'failed_after_attempts', 'total_attempts.$': '$.state.attemptCount', 'solution_applied.$': '$.state.solutionApplied' },
                    ResultPath: '$.caseResult',
                    Next: 'EndCaseRegistered',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  RegisterRejectedCase: {
                    Type: 'Task',
                    Resource: '${GenerateCaseArn}',
                    Parameters: { 'ticket_id.$': '$.state.ticketId', 'tenant_id.$': '$.state.tenantId', 'subject.$': '$.state.subject', action: 'rejected', 'total_attempts.$': '$.state.attemptCount' },
                    ResultPath: '$.caseResult',
                    Next: 'EndCaseRegistered',
                    Retry: [{ ErrorEquals: ['Lambda.ServiceException', 'Lambda.SdkClientException'], IntervalSeconds: 2, MaxAttempts: 3, BackoffRate: 2 }],
                  },
                  EndCannotResolve: {
                    Type: 'Pass',
                    Result: { status: 'cannot_resolve', message: 'Victor Azure determined this ticket cannot be resolved automatically.' },
                    End: true,
                  },
                  EndCaseRegistered: {
                    Type: 'Pass',
                    Result: { status: 'case_registered', message: 'Case has been registered in CasesTable.' },
                    End: true,
                  },
                },
              }),
              {
                AssessTicketAutomationArn: { 'Fn::GetAtt': ['AssessTicketAutomationLambdaFunction', 'Arn'] },
                SearchSimilarCasesArn: { 'Fn::GetAtt': ['SearchSimilarCasesLambdaFunction', 'Arn'] },
                GenerateCaseArn: { 'Fn::GetAtt': ['GenerateCaseLambdaFunction', 'Arn'] },
                WaitForApprovalArn: { 'Fn::GetAtt': ['WaitForApprovalLambdaFunction', 'Arn'] },
                CheckTicketStatusArn: { 'Fn::GetAtt': ['CheckTicketStatusLambdaFunction', 'Arn'] },
              },
            ],
          },
          RoleArn: { 'Fn::GetAtt': ['AutomationWorkflowRole', 'Arn'] },
        },
      },
      AutomationWorkflowRole: {
        Type: 'AWS::IAM::Role',
        Properties: {
          AssumeRolePolicyDocument: {
            Version: '2012-10-17',
            Statement: [{ Effect: 'Allow', Principal: { Service: 'states.amazonaws.com' }, Action: 'sts:AssumeRole' }],
          },
          Policies: [
            {
              PolicyName: 'AutomationWorkflowPolicy',
              PolicyDocument: {
                Version: '2012-10-17',
                Statement: [
                    { Effect: 'Allow', Action: ['lambda:InvokeFunction'], Resource: [
                    { 'Fn::GetAtt': ['AssessTicketAutomationLambdaFunction', 'Arn'] },
                    { 'Fn::GetAtt': ['CheckTicketStatusLambdaFunction', 'Arn'] },
                    { 'Fn::GetAtt': ['SearchSimilarCasesLambdaFunction', 'Arn'] },
                    { 'Fn::GetAtt': ['GenerateCaseLambdaFunction', 'Arn'] },
                    { 'Fn::GetAtt': ['WaitForApprovalLambdaFunction', 'Arn'] },
                    { 'Fn::GetAtt': ['ApprovalCallbackLambdaFunction', 'Arn'] },
                  ]},
                  { Effect: 'Allow', Action: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:Query'], Resource: [
                    { 'Fn::GetAtt': ['AutomationCasesTable', 'Arn'] },
                    { 'Fn::Join': ['', [{ 'Fn::GetAtt': ['AutomationCasesTable', 'Arn'] }, '/index/*']] },
                  ]},
                  { Effect: 'Allow', Action: ['dynamodb:GetItem', 'dynamodb:UpdateItem'], Resource: [`arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-tickets-${stage}-tickets`] },
                ],
              },
            },
          ],
        },
      },
    },
    Outputs: {
      CasesTableArn: {
        Value: { 'Fn::GetAtt': ['AutomationCasesTable', 'Arn'] },
      },
      CasesTableName: {
        Value: `xoc-api-automation-${stage}-cases-v2`,
      },
      AutomationWorkflowStateMachineArn: {
        Value: { 'Fn::GetAtt': ['AutomationWorkflowStateMachine', 'Arn'] },
      },
      AutomationWorkflowStateMachineName: {
        Value: `xoc-api-automation-${stage}-workflow`,
      },
    },
  };
};
