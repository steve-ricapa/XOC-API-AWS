// Packaging/CFN compile proof only, never deploy this profile. It contains no
// dependencies: Linux import/tests are verified separately in the AC-1 image.
if (!process.argv.includes('package')) throw new Error('AC-1 offline profile supports package only, never deploy');
const config = require('./serverless.agentcore');
config.service = 'xoc-api-agentcore-local-proof';
config.provider.region = 'us-east-1';
config.provider.logRetentionInDays = 14;
config.plugins = [];
delete config.custom;
config.package.artifact = undefined;
config.provider.deploymentBucket = { name: 'ac1-offline-no-deploy' };
// Avoid STS / credentials for the CFN account variable resolution.
config.provider.environment.AC1_PACKAGING_ONLY = 'true';
module.exports = config;
