const { buildService, lambdaConfig, protectedRoute } = require('./serverless/services/lib/common');

module.exports = buildService({
  service: 'xoc-api-chat',
  attachToSharedHttpApi: true,
  iam: { database: true, vpc: true, jwt: true, dynamo: true, events: true },
  pythonRequirements: {
    fileName: 'requirements.crypto.txt',
    dockerizePip: true,
    dockerImage: 'public.ecr.aws/sam/build-python3.11:latest',
  },
  functions: (stage) => ({
    chatAgentsApi: {
      ...lambdaConfig(stage, {
      handler: 'src/handlers/domains/chat_agents.handler',
      description: 'Chat & Agents domain API',
      needsVpc: true,
      include: [
        'src/handlers/domains/chat_agents.py',
        'src/handlers/routes/chat.py',
        'src/handlers/routes/live_voice.py',
        'src/handlers/routes/agents.py',
        'src/shared/**',
        'src/persistence/**',
        'requirements.crypto.txt',
      ],
      events: [
        protectedRoute(stage, 'GET', '/chat/sessions'),
        protectedRoute(stage, 'GET', '/chat/history'),
        protectedRoute(stage, 'DELETE', '/chat/sessions/{sessionId}'),
        protectedRoute(stage, 'POST', '/chat'),
        protectedRoute(stage, 'POST', '/chat/tickets/confirm'),
        protectedRoute(stage, 'POST', '/live-voice-sessions'),
        protectedRoute(stage, 'GET', '/live-voice-sessions'),
        protectedRoute(stage, 'GET', '/live-voice-sessions/{sessionId}'),
        protectedRoute(stage, 'POST', '/live-voice-sessions/{sessionId}/messages'),
        protectedRoute(stage, 'PATCH', '/live-voice-sessions/{sessionId}'),
        protectedRoute(stage, 'POST', '/agents/auth/token'),
        protectedRoute(stage, 'POST', '/agents/auth/token-from-user'),
        protectedRoute(stage, 'GET', '/agents/instance/{instanceId}'),
      ],
      }),
      environment: {
        SOPHIA_TOOL_GATEWAY_ENABLED: "${env:SOPHIA_TOOL_GATEWAY_ENABLED, 'false'}",
        SOPHIA_THREAD_CAPABILITIES_ENABLED: "${env:SOPHIA_THREAD_CAPABILITIES_ENABLED, 'false'}",
        SOPHIA_GOVERNED_BASE_URL: "${env:SOPHIA_GOVERNED_BASE_URL, ''}",
      },
    },
  }),
});

// AC-2R uses the accredited deployed dependency ZIP with a source-only overlay.
// No dependency rebuild may incidentally alter other routes in this Lambda.
if (process.env.SOPHIA_CHAT_RELEASE_ARTIFACT) {
  module.exports.functions.chatAgentsApi.package.artifact = process.env.SOPHIA_CHAT_RELEASE_ARTIFACT;
  module.exports.plugins = module.exports.plugins.filter((name) => name !== 'serverless-python-requirements');
}

// Reuse AC-2 resources; neither direct DynamoDB grants nor another gateway.
if (process.env.SOPHIA_AGENTCORE_PROVISIONED === 'true') {
  const stage = module.exports.provider.stage;
  const output = (key) => `\${cf:xoc-api-agentcore-${stage}.${key}}`;
  Object.assign(module.exports.functions.chatAgentsApi.environment, {
    SOPHIA_TOOL_EXECUTION: "${env:SOPHIA_TOOL_EXECUTION, 'disabled'}",
    SOPHIA_AGENTCORE_GATEWAY_ARN: output('GatewayArn'),
    SOPHIA_AGENTCORE_GATEWAY_URL: output('GatewayUrl'),
    SOPHIA_AGENTCORE_CONTEXT_KEY_ARN: output('ContextKeyArn'),
  });
  module.exports.resources = { Resources: { SophiaAgentCoreCallerPolicy: {
    Type: 'AWS::IAM::Policy', Properties: {
      PolicyName: `xoc-chat-agentcore-${stage}`,
      Roles: [{ Ref: 'IamRoleLambdaExecution' }],
      PolicyDocument: { Version: '2012-10-17', Statement: [
        { Effect: 'Allow', Action: ['bedrock-agentcore:InvokeGateway'], Resource: output('GatewayArn') },
        { Effect: 'Allow', Action: ['kms:GenerateMac'], Resource: output('ContextKeyArn') },
      ] },
    },
  } } };
}
