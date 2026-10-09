import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const module = { exports: {} };
const compiled = ts.transpileModule(readFileSync(new URL('../src/local-identity.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`, {})(
  name => name === './api' ? { api() { throw Error('Unexpected request'); } } : require(name), module, module.exports,
);
const { loadIdentity } = module.exports;
const account = { user: null, configured: false, has_users: false };
const local = { user: { id: 'local-user' }, preferences: {} };
function source(mode, auth = account, configured = false) {
  const calls = [];
  return { calls, request: async (path, body) => {
    calls.push(path);
    if (path === '/auth/me') return auth;
    if (path === '/local/setup') return { mode };
    if (path === '/local/bootstrap') { assert.deepEqual(structuredClone(body), {}); return local; }
    if (path === '/local/provider') return { configured };
    throw Error('Unexpected path');
  } };
}
test('fresh local start creates an internal identity and opens write-only provider setup', async () => {
  const f = source('local');
  const result = await loadIdentity(f.request);
  assert.deepEqual(f.calls, ['/auth/me', '/local/setup', '/local/bootstrap', '/local/provider']);
  assert.equal(result.user.id, 'local-user');
  assert.equal(result.local, true); assert.equal(result.needsSetup, true);
});
test('hosted and existing account deployments never bootstrap a local owner', async () => {
  for (const mode of ['account', 'hosted']) {
    const f = source(mode, { ...account, has_users: true });
    const result = await loadIdentity(f.request);
    assert.deepEqual(f.calls, ['/auth/me', '/local/setup']);
    assert.equal(result.local, false); assert.equal(result.user, null);
  }
});
test('configured local start keeps its signed-in identity and does not reopen onboarding', async () => {
  const f = source('local', { ...account, ...local }, true);
  const result = await loadIdentity(f.request);
  assert.deepEqual(f.calls, ['/auth/me', '/local/setup', '/local/provider']);
  assert.equal(result.needsSetup, false); assert.equal(result.user.id, 'local-user');
});
test('only a missing endpoint permits old-server fallback; a denied or failed request never bootstraps', async () => {
  for (const status of [404, 403, 500]) {
    const f = source('local');
    const request = async path => path === '/local/setup' ? Promise.reject({ status }) : f.request(path);
    if (status === 404) assert.equal((await loadIdentity(request)).local, false);
    else await assert.rejects(() => loadIdentity(request));
    assert.deepEqual(f.calls, ['/auth/me']);
  }
});
test('a changed authentication epoch stops the pending local bootstrap path', async () => {
  const f = source('local'); let live = true;
  const result = await loadIdentity(async (path, body) => {
    const value = await f.request(path, body);
    if (path === '/local/setup') live = false;
    return value;
  }, () => live);
  assert.equal(result, null); assert.deepEqual(f.calls, ['/auth/me', '/local/setup']);
});
