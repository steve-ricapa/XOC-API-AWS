const { buildService, commonEnvironment, lambdaConfig, protectedRoute } = require('./serverless/services/lib/common');
const ticketsResources = require('./serverless/services/tickets-resources');

module.exports = buildService({
  service: 'xoc-api-tickets',
  attachToSharedHttpApi: true,
  iam: { dynamo: true, events: true },
  environment: (stage) => ({
    ...commonEnvironment(stage),
    NOTIFICATION_EVENT_BUS_NAME: `xoc-api-ops-${stage}-notifications-bus`,
  }),
  additionalIamStatements: (stage) => [
    {
      Effect: 'Allow',
      Action: ['events:PutEvents'],
      Resource: `arn:aws:events:${'${aws:region}'}:${'${aws:accountId}'}:event-bus/xoc-api-ops-${stage}-notifications-bus`,
    },
    {
      Effect: 'Allow',
      Action: ['states:StartExecution', 'states:SendTaskSuccess'],
      Resource: [
        `arn:aws:states:${'${aws:region}'}:${'${aws:accountId}'}:stateMachine:xoc-api-automation-${stage}-workflow`,
        `arn:aws:states:${'${aws:region}'}:${'${aws:accountId}'}:execution:xoc-api-automation-${stage}-workflow:*`,
      ],
    },
  ],
  functions: (stage) => ({
    ticketsDynamoApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/tickets_dynamo.handler',
      description: 'Tickets domain API (DynamoDB-backed)',
      include: [
        'src/handlers/domains/tickets_dynamo.py',
        'src/notifications/**',
        'src/shared/**',
        'src/persistence/**',
        'requirements.txt',
      ],
      events: [
        protectedRoute(stage, 'GET', '/tickets'),
        protectedRoute(stage, 'POST', '/tickets'),
        protectedRoute(stage, 'POST', '/tickets/agent'),
        protectedRoute(stage, 'GET', '/tickets/{ticketId}'),
        protectedRoute(stage, 'PUT', '/tickets/{ticketId}'),
        protectedRoute(stage, 'DELETE', '/tickets/{ticketId}'),
        protectedRoute(stage, 'PATCH', '/tickets/{ticketId}/approve'),
        protectedRoute(stage, 'PATCH', '/tickets/{ticketId}/reject'),
        protectedRoute(stage, 'PATCH', '/tickets/{ticketId}/decision/select'),
        protectedRoute(stage, 'PATCH', '/tickets/{ticketId}/decision/custom'),
      ],
    }),
    startAutomation: lambdaConfig(stage, {
      handler: 'src/handlers/workers/start_automation.handler',
      description: 'Starts the automation workflow when a ticket is created',
      timeout: 30,
    }),
  }),
  resources: (stage) => ticketsResources(stage),
});
