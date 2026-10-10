'use strict';
// python-requirements 6.x injects path.join() names into ZIPs on Windows.
// Lambda is Linux: normalize the generated artifact before Serverless hashes it.
const fs = require('node:fs/promises');
const path = require('node:path');
const { createRequire } = require('node:module');
const pluginRequire = createRequire(require.resolve('serverless-python-requirements'));
const JSZip = pluginRequire('jszip');

async function normalizeArtifact(artifact) {
  const source = await JSZip.loadAsync(await fs.readFile(artifact));
  const output = new JSZip();
  const seen = new Set();
  let changed = false;
  for (const file of Object.values(source.files)) {
    const original = file.unsafeOriginalName || file.name;
    const name = original.replace(/\\/g, '/');
    if (name.startsWith('/') || name.includes(':') || name.split('/').includes('..')) {
      throw new Error('AgentCore artifact contains an unsafe path');
    }
    if (seen.has(name)) throw new Error('AgentCore artifact contains colliding paths');
    seen.add(name);
    changed ||= name !== original;
    output.file(name, file.dir ? null : await file.async('nodebuffer'), {
      dir: file.dir, date: file.date, unixPermissions: file.unixPermissions,
      dosPermissions: file.dosPermissions, createFolders: false,
    });
  }
  if (changed) {
    const bytes = await output.generateAsync({ type: 'nodebuffer', platform: 'UNIX',
      compression: 'DEFLATE', compressionOptions: { level: 9 } });
    await fs.writeFile(artifact, bytes);
  }
  return changed;
}

class AgentCorePosixPackage {
  constructor(serverless) {
    this.hooks = { 'after:package:createDeploymentArtifacts': async () => {
      if (serverless.service.service !== 'xoc-api-agentcore') {
        throw new Error('AgentCore packaging plugin must remain service-scoped');
      }
      for (const functionName of serverless.service.getAllFunctions()) {
        const config = serverless.service.getFunction(functionName);
        const artifact = path.resolve(serverless.serviceDir, config.package.artifact);
        await normalizeArtifact(artifact);
      }
    } };
  }
}
module.exports = AgentCorePosixPackage;
module.exports.normalizeArtifact = normalizeArtifact;
