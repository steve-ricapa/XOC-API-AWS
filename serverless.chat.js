const { buildService, lambdaConfig, protectedRoute } = require('./serverless/services/lib/common');

module.exports = buildService({
  service: 'xoc-api-chat',
  attachToSharedHttpApi: true,
  iam: { database: true, vpc: true, jwt: true },
  functions: (stage) => ({
    chatAgentsApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/chat_agents.handler',
      description: 'Chat & Agents domain API',
      needsVpc: true,
      include: [
        'src/handlers/domains/chat_agents.py',
        'src/handlers/routes/chat.py',
        'src/handlers/routes/agents.py',
        'src/shared/**',
        'src/persistence/**',
        'requirements.txt',
      ],
      events: [
        protectedRoute(stage, 'GET', '/chat/sessions'),
        protectedRoute(stage, 'GET', '/chat/history'),
        protectedRoute(stage, 'DELETE', '/chat/sessions/{sessionId}'),
        protectedRoute(stage, 'POST', '/chat'),
        protectedRoute(stage, 'POST', '/agents/auth/token'),
        protectedRoute(stage, 'POST', '/agents/auth/token-from-user'),
        protectedRoute(stage, 'GET', '/agents/instance/{instanceId}'),
      ],
    }),
  }),
});
