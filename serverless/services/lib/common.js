function stageRef(stage, key) {
  return `\${file(./serverless/stages/${stage}.yml):${key}}`;
}

function cfRef(stackName, output) {
  return `\${cf:${stackName}.${output}}`;
}

function sharedStackName(stage) {
  return `xoc-api-shared-${stage}`;
}

function sharedOutput(stage, output) {
  return cfRef(sharedStackName(stage), output);
}

function commonEnvironment(stage) {
  return {
    APP_STAGE: stage,
    APP_REGION: '${aws:region}',
    POWERTOOLS_SERVICE_NAME: '${self:service}',
    POWERTOOLS_LOG_LEVEL: stageRef(stage, 'logLevel'),
    POWERTOOLS_METRICS_NAMESPACE: 'XOC/API',
    JWT_SECRET_KEY: stageRef(stage, 'jwtSecretKey'),
    DATABASE_SECRET_ARN: stageRef(stage, 'databaseSecretArn'),
    SNAPSHOTS_BUCKET_NAME: stageRef(stage, 'snapshotsBucketName'),
    CORS_ALLOWED_ORIGINS: stageRef(stage, 'corsAllowedOriginsCsv'),
    AGENTS_FUNCTION_BASE_URL: stageRef(stage, 'agentsFunctionBaseUrl'),
    AGENTS_FUNCTION_ROUTE_SOPHIA: stageRef(stage, 'agentsFunctionRouteSophia'),
    AGENTS_FUNCTION_ROUTE_SOPHIA_HISTORY: stageRef(stage, 'agentsFunctionRouteSophiaHistory'),
    AGENTS_FUNCTION_ROUTE_SOPHIA_DELETE: stageRef(stage, 'agentsFunctionRouteSophiaDelete'),
    AGENTS_FUNCTION_ROUTE_VICTOR: stageRef(stage, 'agentsFunctionRouteVictor'),
    EVENT_BUS_NAME: `xoc-api-tickets-${stage}-bus`,
    TICKETS_TABLE_NAME: `xoc-api-tickets-${stage}-tickets`,
    AGENT_KEY_ENCRYPTION_KEY: "${env:AGENT_KEY_ENCRYPTION_KEY, ''}",
  };
}

function commonPackage() {
  return {
    individually: true,
    patterns: [
      '!node_modules/**',
      '!package-lock.json',
      '!README.md',
      '!tests/**',
      '!txdxai_Flask/**',
      '!.git/**',
      '!.opencode/**',
      '!__pycache__/**',
      '!**/__pycache__/**',
      '!*.pyc',
      '!**/*.pyc',
    ],
  };
}

function iamStatements(stage, capabilities = {}) {
  const statements = [];
  if (capabilities.database) {
    statements.push({
      Effect: 'Allow',
      Action: ['secretsmanager:GetSecretValue'],
      Resource: [stageRef(stage, 'databaseSecretArn')],
    });
  }
  if (capabilities.snapshots) {
    statements.push({
      Effect: 'Allow',
      Action: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      Resource: [`${stageRef(stage, 'snapshotsBucketArn')}/*`],
    });
    statements.push({
      Effect: 'Allow',
      Action: ['s3:ListBucket'],
      Resource: [stageRef(stage, 'snapshotsBucketArn')],
    });
  }
  if (capabilities.events) {
    statements.push({
      Effect: 'Allow',
      Action: ['events:PutEvents'],
      Resource: [`arn:aws:events:${'${aws:region}'}:${'${aws:accountId}'}:event-bus/xoc-api-tickets-${stage}-bus`],
    });
  }
  if (capabilities.dynamo) {
    statements.push({
      Effect: 'Allow',
      Action: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem', 'dynamodb:Query', 'dynamodb:Scan'],
      Resource: [
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-tickets-${stage}-tickets`,
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-tickets-${stage}-tickets/index/*`,
      ],
    });
  }
  if (capabilities.vpc) {
    statements.push({
      Effect: 'Allow',
      Action: [
        'ec2:CreateNetworkInterface',
        'ec2:DescribeNetworkInterfaces',
        'ec2:DeleteNetworkInterface',
        'ec2:AssignPrivateIpAddresses',
        'ec2:UnassignPrivateIpAddresses',
      ],
      Resource: '*',
    });
  }
  return statements;
}

function protectedRoute(stage, method, path) {
  return {
    httpApi: {
      method,
      path,
      authorizer: {
        id: sharedOutput(stage, 'HttpApiAuthorizerId'),
      },
    },
  };
}

function publicRoute(method, path) {
  return {
    httpApi: {
      method,
      path,
    },
  };
}

function lambdaConfig(stage, config) {
  const lambda = {
    handler: config.handler,
    description: config.description,
    memorySize: config.memorySize || 512,
    timeout: config.timeout || 20,
    package: {
      patterns: ['src/**', 'requirements.txt'],
    },
  };
  if (config.events) {
    lambda.events = config.events;
  }
  if (config.needsVpc && stage === 'prod') {
    lambda.vpc = stageRef(stage, 'apiVpc');
  }
  return lambda;
}

function buildService(options) {
  return async ({ options: cliOptions }) => {
    const stage = (cliOptions && cliOptions.stage) || 'dev';
    const provider = {
      name: 'aws',
      runtime: 'python3.12',
      architecture: 'x86_64',
      stage,
      region: stageRef(stage, 'region'),
      logRetentionInDays: stageRef(stage, 'logRetentionInDays'),
      tracing: { lambda: true },
      environment: options.environment ? options.environment(stage) : commonEnvironment(stage),
      iam: {
        role: {
          statements: iamStatements(stage, options.iam || {}),
        },
      },
    };

    if (options.attachToSharedHttpApi) {
      provider.httpApi = {
        id: sharedOutput(stage, 'HttpApiId'),
      };
    }

    return {
      service: options.service,
      frameworkVersion: '4',
      provider,
      package: commonPackage(),
      functions: options.functions(stage),
      resources: options.resources ? options.resources(stage) : undefined,
    };
  };
}

module.exports = {
  buildService,
  commonEnvironment,
  lambdaConfig,
  protectedRoute,
  publicRoute,
  sharedOutput,
  stageRef,
};
