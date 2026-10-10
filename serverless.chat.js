const { buildService, lambdaConfig, protectedRoute } = require('./serverless/services/lib/common');

module.exports = buildService({
  service: 'xoc-api-chat',
  attachToSharedHttpApi: true,
  iam: { database: true, vpc: true, jwt: true, dynamo: true, events: true },
  // Existing READ_ONLY gateway helpers: no writers, scans or wildcard indexes.
  // Tenant/user authorization remains in ToolContext + policy, not the runtime.
  additionalIamStatements: (stage) => ([
    {
      Sid: 'SophiaReadDocumentJobs',
      Effect: 'Allow',
      Action: ['dynamodb:Query'],
      Resource: [
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-reports-${stage}-reports`,
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-reports-${stage}-reports/index/StatusIndex`,
      ],
    },
    {
      Sid: 'SophiaCountUnreadNotifications',
      Effect: 'Allow',
      Action: ['dynamodb:Query'],
      Resource: [
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-ops-${stage}-user-notification-inbox`,
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-ops-${stage}-user-notification-inbox/index/UserStatusCreatedAtIndex`,
      ],
    },
  ]),
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
      // Local implementation only; activate after the matching runtime release.
      // No changes to the provider-wide environment or any VICTOR function.
      environment: {
        SOPHIA_TOOL_GATEWAY_ENABLED: "${env:SOPHIA_TOOL_GATEWAY_ENABLED, 'false'}",
        SOPHIA_THREAD_CAPABILITIES_ENABLED: "${env:SOPHIA_THREAD_CAPABILITIES_ENABLED, 'false'}",
        USER_NOTIFICATION_INBOX_TABLE_NAME: `xoc-api-ops-${stage}-user-notification-inbox`,
      },
    },
  }),
});

// AC-1 keeps the default Chat template byte-for-byte equivalent in behavior.
// AC-2 can provision the separate stack first, then opt into these output refs.
// Activation is separately controlled and defaults to unavailable, NOT fallback.
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
