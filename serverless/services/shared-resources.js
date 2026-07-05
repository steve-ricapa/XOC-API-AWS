const { stageRef } = require('./lib/common');

module.exports = function sharedResources(stage) {
  return {
    Resources: {
      HttpApi: {
        Type: 'AWS::ApiGatewayV2::Api',
        Properties: {
          Name: `xoc-api-shared-${stage}`,
          ProtocolType: 'HTTP',
          CorsConfiguration: {
            AllowOrigins: stageRef(stage, 'corsAllowedOrigins'),
            AllowHeaders: ['Content-Type', 'Authorization', 'X-Request-Id', 'X-Superadmin-Confirm'],
            AllowMethods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
            ExposeHeaders: ['X-Request-Id'],
            AllowCredentials: true,
          },
        },
      },
      HttpApiStage: {
        Type: 'AWS::ApiGatewayV2::Stage',
        Properties: {
          ApiId: { Ref: 'HttpApi' },
          StageName: '$default',
          AutoDeploy: true,
        },
      },
      HttpApiAuthorizer: {
        Type: 'AWS::ApiGatewayV2::Authorizer',
        Properties: {
          ApiId: { Ref: 'HttpApi' },
          Name: 'jwtRequestAuthorizer',
          AuthorizerType: 'REQUEST',
          AuthorizerPayloadFormatVersion: '2.0',
          EnableSimpleResponses: true,
          IdentitySource: ['$request.header.Authorization'],
          AuthorizerUri: {
            'Fn::Sub': 'arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${JwtAuthorizerLambdaFunction.Arn}/invocations',
          },
        },
      },
      JwtAuthorizerInvokePermission: {
        Type: 'AWS::Lambda::Permission',
        Properties: {
          Action: 'lambda:InvokeFunction',
          FunctionName: { Ref: 'JwtAuthorizerLambdaFunction' },
          Principal: 'apigateway.amazonaws.com',
          SourceArn: {
            'Fn::Sub': 'arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*',
          },
        },
      },
    },
    Outputs: {
      HttpApiId: {
        Value: { Ref: 'HttpApi' },
      },
      HttpApiEndpoint: {
        Value: { 'Fn::GetAtt': ['HttpApi', 'ApiEndpoint'] },
      },
      HttpApiAuthorizerId: {
        Value: { Ref: 'HttpApiAuthorizer' },
      },
      JwtAuthorizerFunctionName: {
        Value: { Ref: 'JwtAuthorizerLambdaFunction' },
      },
    },
  };
};
