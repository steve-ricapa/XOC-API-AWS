const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const JSZip = createRequire(require.resolve('serverless-python-requirements'))('jszip');
const Plugin = require('../../serverless/plugins/agentcore-posix-package');

async function fixture(t, entries) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'xoc-ac2-package-test-'));
  const artifact = path.join(dir, 'fixture.zip');
  t.after(async () => { await fs.unlink(artifact); await fs.rmdir(dir); });
  const zip = new JSZip();
  for (const [name, body] of entries) zip.file(name, body, { createFolders: false });
  await fs.writeFile(artifact, await zip.generateAsync({ type: 'nodebuffer' }));
  return artifact;
}

test('Windows injected names become Linux paths; bytes preserved; second run no-op', async t => {
  const artifact = await fixture(t, [['sqlalchemy\\__init__.py', 'payload'], ['src/handler.py', 'handler']]);
  assert.equal(await Plugin.normalizeArtifact(artifact), true);
  const zip = await JSZip.loadAsync(await fs.readFile(artifact));
  assert.equal(await zip.file('sqlalchemy/__init__.py').async('string'), 'payload');
  assert.equal(await zip.file('src/handler.py').async('string'), 'handler');
  assert.equal(await Plugin.normalizeArtifact(artifact), false);
});
test('reject normalized path collisions', async t => {
  const artifact = await fixture(t, [['pkg\\file.py', 'a'], ['pkg/file.py', 'b']]);
  await assert.rejects(Plugin.normalizeArtifact(artifact), /colliding/);
});
test('reject traversal paths rather than sanitize silently', async t => {
  const artifact = await fixture(t, [['..\\secret', 'a']]);
  await assert.rejects(Plugin.normalizeArtifact(artifact), /unsafe/);
});
test('cannot attach plugin to another service', async () => {
  const plugin = new Plugin({ service: { service: 'xoc-api-chat' } });
  await assert.rejects(plugin.hooks['after:package:createDeploymentArtifacts'](), /service-scoped/);
});
