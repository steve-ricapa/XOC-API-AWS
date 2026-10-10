// CFN properties checked against AWS TemplateReference, 2026-10-10.
const sub = (value) => ({ 'Fn::Sub': value });
const attr = (name, field = 'Arn') => ({ 'Fn::GetAtt': [name, field] });
const ref = (name) => ({ Ref: name });
const statement = (Action, Resource) => ({ Effect: 'Allow', Action, Resource });
const document = (Statement) => ({ Version: '2012-10-17', Statement });
const policy = (PolicyName, Statement) => ({ PolicyName, PolicyDocument: document(Statement) });

module.exports = (stage) => {
  if (!['dev', 'staging', 'prod'].includes(stage)) throw new Error('Unsupported AgentCore stage');
  const name = `xoc-api-agentcore-${stage}`;
  const prefix = 'arn:${AWS::Partition}:';
  const location = '${AWS::Region}:${AWS::AccountId}:';
  const functionArn = sub(prefix + 'lambda:' + location + `function:${name}-read`);
  const table = (service, suffix) => sub(prefix + 'dynamodb:' + location + `table/xoc-api-${service}-${stage}-${suffix}`);
  const tools = ['tickets-list', 'tickets-get', 'documents-list', 'notifications-unread-count'].map((Name) => ({
    Name, Description: `XOC authorized READ_ONLY ${Name}; backend-signed context required.`,
    InputSchema: { Type: 'object', Required: ['assertion', 'arguments'], Properties: {
      assertion: { Type: 'string', Description: 'Opaque short-lived KMS MAC assertion issued by XOC Chat, never the model.' },
      arguments: { Type: 'object', Properties: Name === 'tickets-get'
        ? { ticket_id: { Type: 'string' } }
        : Name === 'notifications-unread-count' ? {}
          : { limit: { Type: 'integer' }, status: { Type: 'string' } } },
    } },
  }));
  return {
    Resources: {
      ContextKey: {
        Type: 'AWS::KMS::Key', DeletionPolicy: 'Retain', UpdateReplacePolicy: 'Retain',
        Properties: {
          Description: 'Dedicated XOC Chat to AgentCore context MAC; no JWT or agent-key reuse',
          KeySpec: 'HMAC_256', KeyUsage: 'GENERATE_VERIFY_MAC',
          KeyPolicy: document([{
            Sid: 'EnableAccountIAMForThisKey', Effect: 'Allow',
            Principal: { AWS: sub('arn:${AWS::Partition}:iam::${AWS::AccountId}:root') },
            Action: ['kms:DescribeKey', 'kms:GetKeyPolicy', 'kms:PutKeyPolicy', 'kms:EnableKey',
              'kms:DisableKey', 'kms:UpdateKeyDescription', 'kms:ScheduleKeyDeletion', 'kms:CancelKeyDeletion',
              'kms:TagResource', 'kms:UntagResource', 'kms:ListResourceTags', 'kms:GenerateMac', 'kms:VerifyMac'],
            // In a KMS key policy '*' means THIS key, not all account keys.
            Resource: '*',
          }]),
        },
      },
      GatewayRole: {
        Type: 'AWS::IAM::Role', Properties: {
          AssumeRolePolicyDocument: document([{
            Effect: 'Allow', Principal: { Service: 'bedrock-agentcore.amazonaws.com' }, Action: 'sts:AssumeRole',
            Condition: { StringEquals: { 'aws:SourceAccount': ref('AWS::AccountId') },
              ArnLike: { 'aws:SourceArn': sub(prefix + 'bedrock-agentcore:' + location + `gateway/${name}-*`) } },
          }]),
          Policies: [policy('InvokeOnlyXocReadTarget', [statement(['lambda:InvokeFunction'], functionArn)])],
        },
      },
      ReadTargetRole: {
        Type: 'AWS::IAM::Role', Properties: {
          AssumeRolePolicyDocument: document([{
            Effect: 'Allow', Principal: { Service: 'lambda.amazonaws.com' }, Action: 'sts:AssumeRole',
          }]),
          Policies: [policy('XocReadOnly', [
            statement(['kms:VerifyMac'], attr('ContextKey')),
            statement(['dynamodb:GetItem'], table('tickets', 'tickets')),
            statement(['dynamodb:Query'], [table('tickets', 'tickets'), table('tickets', 'tickets/index/StatusIndex'),
              table('reports', 'reports'), table('reports', 'reports/index/StatusIndex'),
              table('ops', 'user-notification-inbox'), table('ops', 'user-notification-inbox/index/UserStatusCreatedAtIndex')]),
            statement(['logs:CreateLogStream', 'logs:PutLogEvents'],
              sub(prefix + 'logs:' + location + `log-group:/aws/lambda/${name}-read:*`)),
          ])],
        },
      },
      SophiaGateway: {
        Type: 'AWS::BedrockAgentCore::Gateway', Properties: {
          Name: name, AuthorizerType: 'AWS_IAM', ProtocolType: 'MCP', RoleArn: attr('GatewayRole'),
          ProtocolConfiguration: { Mcp: { SupportedVersions: ['2026-07-28'] } },
          Description: 'SOPHIA READ_ONLY transport; XOC Tool Gateway remains authorization authority',
        },
      },
      SophiaReadTarget: {
        Type: 'AWS::BedrockAgentCore::GatewayTarget', Properties: {
          Name: 'xoc-read', GatewayIdentifier: attr('SophiaGateway', 'GatewayIdentifier'),
          CredentialProviderConfigurations: [{ CredentialProviderType: 'GATEWAY_IAM_ROLE' }],
          TargetConfiguration: { Mcp: { Lambda: { LambdaArn: attr('AgentcoreReadTargetLambdaFunction'),
            ToolSchema: { InlinePayload: tools } } } },
        },
      },
    },
    Outputs: {
      GatewayArn: { Value: attr('SophiaGateway', 'GatewayArn') },
      GatewayUrl: { Value: attr('SophiaGateway', 'GatewayUrl') },
      ContextKeyArn: { Value: attr('ContextKey') },
      ReadTargetArn: { Value: attr('AgentcoreReadTargetLambdaFunction') },
    },
  };
};
