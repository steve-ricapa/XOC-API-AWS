// AC-1 LOCAL: declarative only. AC-2 deploys DEV through the existing framework.
const { buildService } = require('./serverless/services/lib/common');
const resources = require('./serverless/services/agentcore-resources');

const config = buildService({
  service: 'xoc-api-agentcore',
  environment: (stage) => ({
    APP_STAGE: stage,
    POWERTOOLS_SERVICE_NAME: 'xoc-agentcore-target',
    SOPHIA_AGENTCORE_GATEWAY_ARN: { 'Fn::GetAtt': ['SophiaGateway', 'GatewayArn'] },
    SOPHIA_AGENTCORE_CONTEXT_KEY_ARN: { 'Fn::GetAtt': ['ContextKey', 'Arn'] },
    TICKETS_TABLE_NAME: `xoc-api-tickets-${stage}-tickets`,
    REPORTS_TABLE_NAME: `xoc-api-reports-${stage}-reports`,
    REPORTS_STORE_MODE: 'dynamodb',
    USER_NOTIFICATION_INBOX_TABLE_NAME: `xoc-api-ops-${stage}-user-notification-inbox`,
    AWS_MAX_ATTEMPTS: '1',
  }),
  pythonRequirements: { fileName: 'requirements.crypto.txt', dockerizePip: true,
    dockerImage: 'public.ecr.aws/sam/build-python3.11:latest',
    // Retain distribution metadata for runtime imports and artifact provenance.
    slimPatternsAppendDefaults: false,
    slimPatterns: ['**/*.py[c|o]', '**/__pycache__*', '**/tests/**', '**/testing/**'] },
  functions: (stage) => ({
    agentcoreReadTarget: {
      name: `xoc-api-agentcore-${stage}-read`,
      handler: 'src/handlers/agentcore_target.handler',
      role: { 'Fn::GetAtt': ['ReadTargetRole', 'Arn'] },
      memorySize: 512, timeout: 5, reservedConcurrency: 4,
    },
  }),
  resources,
});
// The common DEV builder grants SSM '*'. This isolated target must not inherit it.
delete config.provider.iam;
config.provider.tracing = { lambda: false };
config.provider.deploymentBucket = { blockPublicAccess: true };
config.package.patterns = ['!**', 'src/**', '!src/**/__pycache__/**', '!src/**/*.pyc', 'requirements.crypto.txt'];
// Runs after python-requirements injection; scoped to this service only.
config.plugins.push('./serverless/plugins/agentcore-posix-package.js');
module.exports = config;
