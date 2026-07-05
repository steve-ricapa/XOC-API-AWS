const { buildService, lambdaConfig, protectedRoute } = require('./serverless/services/lib/common');
const ticketsResources = require('./serverless/services/tickets-resources');

module.exports = buildService({
  service: 'xoc-api-tickets',
  attachToSharedHttpApi: true,
  iam: { dynamo: true, events: true },
  functions: (stage) => ({
    ticketsDynamoApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/tickets_dynamo.handler',
      description: 'Tickets domain API (DynamoDB-backed)',
      events: [
        protectedRoute(stage, 'GET', '/tickets'),
        protectedRoute(stage, 'POST', '/tickets'),
        protectedRoute(stage, 'GET', '/tickets/{ticketId}'),
        protectedRoute(stage, 'PUT', '/tickets/{ticketId}'),
        protectedRoute(stage, 'DELETE', '/tickets/{ticketId}'),
        protectedRoute(stage, 'PATCH', '/tickets/{ticketId}/approve'),
        protectedRoute(stage, 'PATCH', '/tickets/{ticketId}/reject'),
        protectedRoute(stage, 'PATCH', '/tickets/{ticketId}/decision/select'),
      ],
    }),
  }),
  resources: (stage) => ticketsResources(stage),
});
