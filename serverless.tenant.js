const { buildService, lambdaConfig, protectedRoute } = require('./serverless/services/lib/common');

module.exports = buildService({
  service: 'xoc-api-tenant',
  attachToSharedHttpApi: true,
  iam: { database: true, vpc: true },
  functions: (stage) => ({
    tenantApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/tenant.handler',
      description: 'Tenant domain API',
      needsVpc: true,
      events: [
        protectedRoute(stage, 'GET', '/tenant'),
        protectedRoute(stage, 'PUT', '/tenant'),
        protectedRoute(stage, 'GET', '/tenant/agent-keys'),
        protectedRoute(stage, 'POST', '/tenant/agent-keys'),
        protectedRoute(stage, 'GET', '/tenant/agent-keys/{keyId}'),
        protectedRoute(stage, 'DELETE', '/tenant/agent-keys/{keyId}'),
        protectedRoute(stage, 'POST', '/tenant/agent-keys/{keyId}/regenerate'),
        protectedRoute(stage, 'POST', '/tenant/agent-keys/{keyId}/toggle'),
        protectedRoute(stage, 'GET', '/tenant/runtime-settings'),
        protectedRoute(stage, 'PUT', '/tenant/runtime-settings'),
        protectedRoute(stage, 'GET', '/users'),
        protectedRoute(stage, 'POST', '/users'),
        protectedRoute(stage, 'GET', '/users/{userId}'),
        protectedRoute(stage, 'PUT', '/users/{userId}'),
        protectedRoute(stage, 'DELETE', '/users/{userId}'),
        protectedRoute(stage, 'POST', '/audit'),
      ],
    }),
  }),
});
