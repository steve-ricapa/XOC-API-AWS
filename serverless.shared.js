const { buildService, lambdaConfig, commonEnvironment } = require('./serverless/services/lib/common');
const sharedResources = require('./serverless/services/shared-resources');

module.exports = buildService({
  service: 'xoc-api-shared',
  iam: { jwt: [{ 'Fn::GetAtt': ['JwtSecret', 'Arn'] }], agentEncryption: [{ 'Fn::GetAtt': ['AgentKeyEncryptionKeySecret', 'Arn'] }] },
  attachToSharedHttpApi: false,
  environment: (stage) => ({
    ...commonEnvironment(stage),
    JWT_SECRET_ARN: { Ref: 'JwtSecret' },
    AGENT_KEY_ENCRYPTION_KEY_ARN: { Ref: 'AgentKeyEncryptionKeySecret' },
  }),
  functions: (stage) => ({
    jwtAuthorizer: lambdaConfig(stage, {
      handler: 'src/handlers/authorizers/jwt_authorizer.handler',
      description: 'Custom JWT request authorizer for XOC API',
      memorySize: 256,
      timeout: 10,
      needsVpc: false,
      include: [
        'src/handlers/authorizers/**',
        'src/shared/config.py',
        'src/shared/logging.py',
        'src/shared/errors.py',
        'requirements.txt',
      ],
    }),
  }),
  resources: (stage) => sharedResources(stage),
});
