const { buildService, lambdaConfig } = require('./serverless/services/lib/common');
const sharedResources = require('./serverless/services/shared-resources');

module.exports = buildService({
  service: 'xoc-api-shared',
  iam: {},
  attachToSharedHttpApi: false,
  functions: (stage) => ({
    jwtAuthorizer: lambdaConfig(stage, {
      handler: 'src/handlers/authorizers/jwt_authorizer.handler',
      description: 'Custom JWT request authorizer for XOC API',
      memorySize: 256,
      timeout: 10,
      needsVpc: false,
    }),
  }),
  resources: (stage) => sharedResources(stage),
});
