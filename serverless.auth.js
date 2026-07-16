const { buildService, lambdaConfig, protectedRoute, publicRoute } = require('./serverless/services/lib/common');

module.exports = buildService({
  service: 'xoc-api-auth',
  attachToSharedHttpApi: true,
  iam: { database: true, vpc: true, jwt: true },
  functions: (stage) => ({
    authApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/auth.handler',
      description: 'Auth & health domain API',
      needsVpc: true,
      include: [
        'src/handlers/domains/auth.py',
        'src/handlers/routes/auth.py',
        'src/handlers/routes/health.py',
        'src/shared/**',
        'src/persistence/**',
        'requirements.txt',
      ],
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
