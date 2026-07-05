const { buildService, lambdaConfig, protectedRoute, publicRoute } = require('./serverless/services/lib/common');

module.exports = buildService({
  service: 'xoc-api-auth',
  attachToSharedHttpApi: true,
  iam: { database: true, vpc: true },
  functions: (stage) => ({
    authApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/auth.handler',
      description: 'Auth & health domain API',
      needsVpc: true,
      events: [
        publicRoute('GET', '/health'),
        publicRoute('POST', '/auth/register'),
        publicRoute('POST', '/auth/login'),
        protectedRoute(stage, 'POST', '/auth/refresh'),
        publicRoute('POST', '/onboarding/tenant'),
      ],
    }),
  }),
});
