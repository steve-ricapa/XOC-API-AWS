module.exports = function ticketsResources(stage) {
  const pitrEnabled = stage === 'prod';

  return {
    Resources: {
      ApplicationEventBus: {
        Type: 'AWS::Events::EventBus',
        Properties: {
          Name: `xoc-api-tickets-${stage}-bus`,
        },
      },
      TicketsTable: {
        Type: 'AWS::DynamoDB::Table',
        Properties: {
          TableName: `xoc-api-tickets-${stage}-tickets`,
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
              IndexName: 'StatusIndex',
              KeySchema: [
                { AttributeName: 'gsi1pk', KeyType: 'HASH' },
                { AttributeName: 'gsi1sk', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
            {
              IndexName: 'TicketLookupIndex',
              KeySchema: [
                { AttributeName: 'gsi2pk', KeyType: 'HASH' },
                { AttributeName: 'gsi2sk', KeyType: 'RANGE' },
              ],
              Projection: { ProjectionType: 'ALL' },
            },
            {
              IndexName: 'GlobalCreatedIndex',
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
      TicketWorkflowStateMachine: {
        Type: 'AWS::StepFunctions::StateMachine',
        Properties: {
          StateMachineName: `xoc-api-tickets-${stage}-ticket-workflow`,
          StateMachineType: 'STANDARD',
          DefinitionString: '{"Comment":"Event-driven workflow for ticket.created, ticket.updated, and ticket.status_changed events. Passes through to acknowledge events. Full orchestration lives in the automation stack.","StartAt":"AcknowledgeEvent","States":{"AcknowledgeEvent":{"Type":"Pass","End":true}}}',
          RoleArn: { 'Fn::GetAtt': ['TicketWorkflowRole', 'Arn'] },
        },
      },
      TicketWorkflowRole: {
        Type: 'AWS::IAM::Role',
        Properties: {
          AssumeRolePolicyDocument: {
            Version: '2012-10-17',
            Statement: [{ Effect: 'Allow', Principal: { Service: 'states.amazonaws.com' }, Action: 'sts:AssumeRole' }],
          },
          Policies: [
            {
              PolicyName: 'TicketWorkflowPolicy',
              PolicyDocument: {
                Version: '2012-10-17',
                Statement: [
                  { Effect: 'Allow', Action: ['events:PutEvents'], Resource: [{ 'Fn::GetAtt': ['ApplicationEventBus', 'Arn'] }] },
                  { Effect: 'Allow', Action: ['dynamodb:GetItem', 'dynamodb:UpdateItem'], Resource: [{ 'Fn::GetAtt': ['TicketsTable', 'Arn'] }] },
                  { Effect: 'Allow', Action: ['states:StartExecution'], Resource: [`arn:aws:states:${'${aws:region}'}:${'${aws:accountId}'}:stateMachine:xoc-api-automation-${stage}-workflow`] },
                  { Effect: 'Allow', Action: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:Query'], Resource: [`arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-automation-${stage}-cases`, `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-automation-${stage}-cases/index/*`] },
                ],
              },
            },
          ],
        },
      },
      TicketEventRule: {
        Type: 'AWS::Events::Rule',
        Properties: {
          Name: `xoc-api-tickets-${stage}-ticket-events`,
          EventBusName: { Ref: 'ApplicationEventBus' },
          EventPattern: {
            source: ['xoc.ticket'],
            'detail-type': ['ticket.created', 'ticket.updated', 'ticket.status_changed'],
          },
          Targets: [
            {
              Arn: { Ref: 'TicketWorkflowStateMachine' },
              RoleArn: { 'Fn::GetAtt': ['TicketEventToStepFunctionsRole', 'Arn'] },
              Id: 'TicketWorkflowTarget',
              InputTransformer: {
                InputPathsMap: {
                  detail: '$.detail',
                  source: '$.source',
                  time: '$.time',
                },
                InputTemplate: '{"input": <detail>, "metadata": {"source": <source>, "time": <time>}}',
              },
            },
          ],
        },
      },
      TicketEventToStepFunctionsRole: {
        Type: 'AWS::IAM::Role',
        Properties: {
          AssumeRolePolicyDocument: {
            Version: '2012-10-17',
            Statement: [{ Effect: 'Allow', Principal: { Service: 'events.amazonaws.com' }, Action: 'sts:AssumeRole' }],
          },
          Policies: [
            {
              PolicyName: 'TicketEventToStepFunctionsPolicy',
              PolicyDocument: {
                Version: '2012-10-17',
                Statement: [{ Effect: 'Allow', Action: ['states:StartExecution'], Resource: [{ Ref: 'TicketWorkflowStateMachine' }] }],
              },
            },
          ],
        },
      },
    },
    Outputs: {
      EventBusName: {
        Value: `xoc-api-tickets-${stage}-bus`,
      },
      TicketsTableName: {
        Value: `xoc-api-tickets-${stage}-tickets`,
      },
      TicketsTableArn: {
        Value: { 'Fn::GetAtt': ['TicketsTable', 'Arn'] },
      },
      TicketWorkflowStateMachineArn: {
        Value: { 'Fn::GetAtt': ['TicketWorkflowStateMachine', 'Arn'] },
      },
      TicketWorkflowStateMachineName: {
        Value: `xoc-api-tickets-${stage}-ticket-workflow`,
      },
    },
  };
};
