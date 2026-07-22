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
    JWT_SECRET_KEY_SSM_PATH: stageRef(stage, 'jwtSecretKeySsmPath'),
    JWT_SECRET_ARN: stageRef(stage, 'jwtSecretArn'),
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
    REPORTS_TABLE_NAME: `xoc-api-reports-${stage}-reports`,
    AGENT_KEY_ENCRYPTION_KEY: "${env:AGENT_KEY_ENCRYPTION_KEY, ''}",
    AGENT_KEY_ENCRYPTION_KEY_ARN: stageRef(stage, 'agentKeyEncryptionKeyArn'),
    PUBLIC_REGISTRATION_ENABLED: stageRef(stage, 'publicRegistrationEnabled'),
  };
}

function commonPackage() {
  return {
    individually: true,
    patterns: [
      '!node_modules/**',
      '!package-lock.json',
      '!README.md',
      '!experiments/**',
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

function isRelaxedStage(stage) {
  return stage === 'dev';
}

function iamStatements(stage, capabilities = {}) {
  const statements = [];
  const relaxed = isRelaxedStage(stage);

  if (relaxed) {
    statements.push({
      Effect: 'Allow',
      Action: ['ssm:GetParameter'],
      Resource: '*',
    });
  }

  if (capabilities.database) {
    statements.push({
      Effect: 'Allow',
      Action: ['secretsmanager:GetSecretValue'],
      Resource: relaxed ? '*' : [stageRef(stage, 'databaseSecretArn')],
    });
  }
  if (capabilities.snapshots) {
    statements.push({
      Effect: 'Allow',
      Action: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      Resource: relaxed ? '*' : [`${stageRef(stage, 'snapshotsBucketArn')}/*`],
    });
    statements.push({
      Effect: 'Allow',
      Action: ['s3:ListBucket'],
      Resource: relaxed ? '*' : [stageRef(stage, 'snapshotsBucketArn')],
    });
  }
  if (capabilities.events) {
    statements.push({
      Effect: 'Allow',
      Action: ['events:PutEvents'],
      Resource: relaxed ? '*' : [`arn:aws:events:${'${aws:region}'}:${'${aws:accountId}'}:event-bus/xoc-api-tickets-${stage}-bus`],
    });
  }
  if (capabilities.dynamo) {
    statements.push({
      Effect: 'Allow',
      Action: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem', 'dynamodb:BatchWriteItem', 'dynamodb:Query', 'dynamodb:Scan'],
      Resource: relaxed
        ? '*'
        : [
            `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-tickets-${stage}-tickets`,
            `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-tickets-${stage}-tickets/index/*`,
          ],
    });
  }
  if (capabilities.stepFunctions) {
    statements.push({
      Effect: 'Allow',
      Action: ['states:StartExecution', 'states:DescribeExecution'],
      Resource: relaxed ? '*' : [`arn:aws:states:${'${aws:region}'}:${'${aws:accountId}'}:stateMachine:xoc-api-tickets-${stage}-ticket-workflow`],
    });
  }
  if (capabilities.jwt) {
    const resource = capabilities.jwt === true ? [stageRef(stage, 'jwtSecretArn')] : capabilities.jwt;
    statements.push({
      Effect: 'Allow',
      Action: ['secretsmanager:GetSecretValue'],
      Resource: relaxed ? '*' : resource,
    });
  }
  if (capabilities.agentEncryption) {
    const resource = capabilities.agentEncryption === true ? [stageRef(stage, 'agentKeyEncryptionKeyArn')] : capabilities.agentEncryption;
    statements.push({
      Effect: 'Allow',
      Action: ['secretsmanager:GetSecretValue'],
      Resource: relaxed ? '*' : resource,
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
  if (capabilities.reports) {
    statements.push({
      Effect: 'Allow',
      Action: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem', 'dynamodb:BatchWriteItem', 'dynamodb:Query'],
      Resource: relaxed ? '*' : [
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-reports-${stage}-reports`,
        `arn:aws:dynamodb:${'${aws:region}'}:${'${aws:accountId}'}:table/xoc-api-reports-${stage}-reports/index/*`,
      ],
    });
    statements.push({
      Effect: 'Allow',
      Action: ['events:PutEvents'],
      Resource: relaxed ? '*' : [`arn:aws:events:${'${aws:region}'}:${'${aws:accountId}'}:event-bus/xoc-api-reports-${stage}-bus`],
    });
    statements.push({
      Effect: 'Allow',
      Action: ['states:StartExecution', 'states:DescribeExecution', 'states:StopExecution'],
      Resource: relaxed ? '*' : [`arn:aws:states:${'${aws:region}'}:${'${aws:accountId}'}:stateMachine:xoc-api-reports-${stage}-*`],
    });
    statements.push({
      Effect: 'Allow',
      Action: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      Resource: relaxed ? '*' : [`${stageRef(stage, 'snapshotsBucketArn')}/reports/*`, `${stageRef(stage, 'snapshotsBucketArn')}/documents/*`, `${stageRef(stage, 'snapshotsBucketArn')}/document-templates/*`, `${stageRef(stage, 'snapshotsBucketArn')}/report-templates/*`],
    });
    statements.push({
      Effect: 'Allow',
      Action: ['s3:ListBucket'],
      Resource: relaxed ? '*' : [`${stageRef(stage, 'snapshotsBucketArn')}`],
    });
    statements.push({
      Effect: 'Allow',
      Action: ['sqs:ReceiveMessage', 'sqs:DeleteMessage', 'sqs:GetQueueAttributes'],
      Resource: relaxed ? '*' : [`arn:aws:sqs:${'${aws:region}'}:${'${aws:accountId}'}:xoc-api-reports-${stage}-*`],
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
        type: 'request',
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
  };
  if (config.events) {
    lambda.events = config.events;
  }
  if (config.needsVpc) {
    lambda.vpc = stageRef(stage, 'apiVpc');
  }
  if (config.include) {
    lambda.package = { include: config.include };
  }
  return lambda;
}

function detectStage() {
  const idx = process.argv.indexOf('--stage');
  if (idx !== -1 && idx + 1 < process.argv.length) return process.argv[idx + 1];
  return process.env.STAGE || 'dev';
}

function buildService(options) {
  const stage = detectStage();
  const additionalIamStatements = typeof options.additionalIamStatements === 'function'
    ? options.additionalIamStatements(stage)
    : (options.additionalIamStatements || []);
  const pythonRequirements = {
    dockerizePip: false,
    useDownloadCache: true,
    useStaticCache: false,
    pipCmdExtraArgs: ['--only-binary', ':all:'],
    slim: true,
    noDeploy: ['boto3', 'botocore', 's3transfer', 'jmespath'],
    ...(options.pythonRequirements || {}),
  };
  const provider = {
    name: 'aws',
      runtime: 'python3.11',
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
  if (additionalIamStatements.length > 0) {
    provider.iam.role.statements.push(...additionalIamStatements);
  }

  if (options.attachToSharedHttpApi) {
    provider.httpApi = {
      id: sharedOutput(stage, 'HttpApiId'),
    };
  }

  return {
    service: options.service,
    frameworkVersion: '3',
    provider,
    package: commonPackage(),
    functions: options.functions(stage),
    resources: options.resources ? options.resources(stage) : undefined,
    plugins: ['serverless-python-requirements'],
    custom: {
      pythonRequirements,
    },
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
  detectStage,
  iamStatements,
};
