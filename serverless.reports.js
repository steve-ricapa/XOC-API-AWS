const { buildService, lambdaConfig, protectedRoute, publicRoute, commonEnvironment } = require('./serverless/services/lib/common');
const reportsResources = require('./serverless/services/reports-resources');

module.exports = buildService({
  service: 'xoc-api-reports',
  attachToSharedHttpApi: true,
  iam: { reports: true },
  environment: (stage) => ({
    ...commonEnvironment(stage),
    REPORTS_TABLE_NAME: `xoc-api-reports-${stage}-reports`,
    REPORT_REQUESTS_QUEUE_URL: { Ref: 'ReportRequestsQueue' },
    REPORT_WORKFLOW_STATE_MACHINE_ARN: { Ref: 'ReportWorkflowStateMachine' },
    REPORT_EVENT_BUS_NAME: { Ref: 'ReportsEventBus' },
  }),
  functions: (stage) => ({
    reportsApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/reports.handler',
      description: 'Reports domain API',
      include: [
        'src/handlers/domains/reports.py',
        'src/handlers/routes/reports.py',
        'src/reports/**',
        'src/shared/**',
        'src/persistence/**',
        'requirements.txt',
      ],
      events: [
        protectedRoute(stage, 'POST', '/reports'),
        protectedRoute(stage, 'GET', '/reports/{reportId}'),
        protectedRoute(stage, 'GET', '/reports'),
      ],
    }),
    reportOrchestrator: lambdaConfig(stage, {
      handler: 'src/handlers/processors/report_orchestrator.handler',
      description: 'Consumes SQS ReportRequested events and starts Step Functions',
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
      description: 'Collects report data from sources',
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
      description: 'Generates report content from collected data',
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
      description: 'Validates generated report content',
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
      description: 'Generates DOCX from template and content',
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
      description: 'Finalizes report status and generates download URL',
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
