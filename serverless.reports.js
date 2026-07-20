const { buildService, lambdaConfig, protectedRoute, publicRoute, commonEnvironment } = require('./serverless/services/lib/common');
const reportsResources = require('./serverless/services/reports-resources');

module.exports = buildService({
  service: 'xoc-api-reports',
  attachToSharedHttpApi: true,
  iam: { reports: true },
  environment: (stage) => ({
    ...commonEnvironment(stage),
    REPORTS_TABLE_NAME: `xoc-api-reports-${stage}-reports`,
    REPORT_REQUESTS_QUEUE_URL: `https://sqs.${'${aws:region}'}.amazonaws.com/${'${aws:accountId}'}/xoc-api-reports-${stage}-report-requests`,
    REPORT_WORKFLOW_STATE_MACHINE_ARN: `arn:aws:states:${'${aws:region}'}:${'${aws:accountId}'}:stateMachine:xoc-api-reports-${stage}-report-workflow`,
    REPORT_EVENT_BUS_NAME: `xoc-api-reports-${stage}-bus`,
  }),
  functions: (stage) => ({
    reportsApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/reports.handler',
      description: 'Documents domain API',
      include: [
        'src/handlers/domains/reports.py',
        'src/handlers/routes/reports.py',
        'src/reports/**',
        'src/shared/**',
        'src/persistence/**',
        'requirements.txt',
      ],
      events: [
        protectedRoute(stage, 'POST', '/documents'),
        protectedRoute(stage, 'GET', '/documents/{documentId}'),
        protectedRoute(stage, 'GET', '/documents'),
      ],
    }),
    reportOrchestrator: lambdaConfig(stage, {
      handler: 'src/handlers/processors/report_orchestrator.handler',
      description: 'Consumes SQS DocumentRequested events and starts Step Functions',
      timeout: 60,
      include: [
        'src/handlers/processors/report_orchestrator.py',
        'src/reports/**',
        'src/shared/**',
        'requirements.txt',
      ],
      events: [
        {
          sqs: {
            arn: { 'Fn::GetAtt': ['ReportRequestsQueue', 'Arn'] },
            batchSize: 1,
          },
        },
      ],
    }),
    collectReportData: lambdaConfig(stage, {
      handler: 'src/handlers/workers/report_collect.handler',
      description: 'Collects document data from sources',
      timeout: 120,
      memorySize: 1024,
      needsVpc: true,
      include: [
        'src/handlers/workers/report_collect.py',
        'src/reports/**',
        'src/shared/**',
        'src/persistence/**',
        'src/integrations/**',
        'requirements.txt',
      ],
    }),
    generateReportContent: lambdaConfig(stage, {
      handler: 'src/handlers/workers/report_generate_content.handler',
      description: 'Generates document content from collected data',
      timeout: 120,
      memorySize: 1024,
      needsVpc: true,
      include: [
        'src/handlers/workers/report_generate_content.py',
        'src/reports/**',
        'src/shared/**',
        'requirements.txt',
      ],
    }),
    validateReport: lambdaConfig(stage, {
      handler: 'src/handlers/workers/report_validate.handler',
      description: 'Validates generated document content',
      timeout: 60,
      include: [
        'src/handlers/workers/report_validate.py',
        'src/reports/**',
        'src/shared/**',
        'requirements.txt',
      ],
    }),
    generateDocx: lambdaConfig(stage, {
      handler: 'src/handlers/workers/report_generate_docx.handler',
      description: 'Generates document file from template and content',
      timeout: 180,
      memorySize: 1536,
      include: [
        'src/handlers/workers/report_generate_docx.py',
        'src/reports/**',
        'src/shared/**',
        'requirements.txt',
      ],
    }),
    completeReport: lambdaConfig(stage, {
      handler: 'src/handlers/workers/report_complete.handler',
      description: 'Finalizes document status and generates download URL',
      timeout: 30,
      include: [
        'src/handlers/workers/report_complete.py',
        'src/reports/**',
        'src/shared/**',
        'requirements.txt',
      ],
    }),
  }),
  resources: (stage) => reportsResources(stage),
});
