const { buildService, lambdaConfig, protectedRoute } = require('./serverless/services/lib/common');
const automationResources = require('./serverless/services/automation-resources');

module.exports = buildService({
  service: 'xoc-api-automation',
  attachToSharedHttpApi: true,
  iam: { automation: true, database: true, jwt: true, vpc: true },
  functions: (stage) => ({
    casesApi: lambdaConfig(stage, {
      handler: 'src/handlers/domains/automation.handler',
      timeout: 30,
      events: [
        protectedRoute(stage, 'GET', '/cases'),
        protectedRoute(stage, 'GET', '/cases/{caseId}'),
        protectedRoute(stage, 'GET', '/cases/ticket/{ticketId}'),
        protectedRoute(stage, 'POST', '/automation/approval/callback'),
      ],
    }),
    assessTicketAutomation: lambdaConfig(stage, {
      handler: 'src/handlers/workers/assess_ticket_automation.handler',
      timeout: 120,
      memorySize: 512,
    }),
    checkTicketStatus: lambdaConfig(stage, {
      handler: 'src/handlers/workers/check_ticket_status.handler',
      timeout: 30,
    }),
    searchSimilarCases: lambdaConfig(stage, {
      handler: 'src/handlers/workers/search_similar_cases.handler',
      timeout: 30,
    }),
    generateCase: lambdaConfig(stage, {
      handler: 'src/handlers/workers/generate_case.handler',
      timeout: 30,
    }),
    waitForApproval: lambdaConfig(stage, {
      handler: 'src/handlers/workers/wait_for_approval.handler',
      timeout: 30,
    }),
    approvalCallback: lambdaConfig(stage, {
      handler: 'src/handlers/workers/approval_callback.handler',
      timeout: 30,
    }),
  }),
  resources: (stage) => automationResources(stage),
});
