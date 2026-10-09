import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Window } from 'happy-dom';

const require = createRequire(import.meta.url);
function compile(path, overrides) {
  const text = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(`(function(require,module,exports){${text}\n})`)(name => overrides[name] ?? require(name), module, module.exports);
  return module.exports;
}
async function fixture() {
  const window = new Window({ url: 'http://127.0.0.1:4317' });
  const document = window.document, originals = new Map();
  for (const [key, value] of Object.entries({ window, document, navigator: window.navigator, IS_REACT_ACT_ENVIRONMENT: true })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  const calls = [], saved = [];
  let failure = null, resolveSave;
  const initial = { revision: 0, configured: false, profile: { id: 'default', provider: 'DeepSeek', model: 'deepseek-flash', api_base: 'https://api.deepseek.com' } };
  const api = async (path, body) => {
    calls.push({ path, body });
    if (body === undefined) return initial;
    await new Promise(resolve => { resolveSave = resolve; });
    if (failure) throw failure;
    return { ...initial, revision: 1, configured: true };
  };
  const helper = compile('../src/local-setup.ts', { './api': { api } });
  const { LocalSetup } = compile('../src/LocalSetup.tsx', {
    './api': { api }, './i18n': { useI18n: () => ({ t: key => key }) }, './local-setup': helper, './local-setup.css': {},
  });
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(React.createElement(LocalSetup, { sessionId: 'active-chat', onSaved: value => saved.push(value) })));
  const inputs = () => Array.from(host.querySelectorAll('input'));
  // Drive React's native input handler, including rerenders while a request is pending.
  const change = async (input, value) => {
    await act(async () => input[Object.keys(input).find(key => key.startsWith('__reactProps'))].onChange({ target: { value } }));
  };
  return { window, host, calls, saved, inputs, change,
    fail: value => { failure = value; },
    async submit() { await act(async () => host.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))); },
    async finish() { await act(async () => resolveSave()); },
    async close() {
      await act(async () => root.unmount()); await window.happyDOM.abort();
      for (const [key, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
      }
    },
  };
}

test('BYOK form clears key during save and sends settings only to the fixed endpoint', async () => {
  const f = await fixture();
  try {
    const key = f.host.querySelector('input[type=password]');
    await f.change(key, 'fixture-private-key');
    await f.submit();
    assert.equal(key.value, '');
    assert.equal(f.calls.length, 2);
    assert.equal(f.calls[1].path, '/local/provider');
    assert.equal(f.calls[1].body.api_key, 'fixture-private-key');
    assert.equal(f.calls[1].body.session_id, 'active-chat');
    assert.equal(f.window.localStorage.length, 0);
    assert.equal(f.window.sessionStorage.length, 0);
    assert.equal(f.host.querySelector('button[type=submit]').disabled, true);
    assert.equal(f.saved.length, 0);
    await f.finish();
    assert.equal(f.saved.length, 1);
    assert.equal(f.saved[0].revision, 1);
    assert.equal(key.value, '');
    assert.equal(f.calls.some(call => call.path.includes('messages')), false);
  } finally { await f.close(); }
});

test('failed or stale settings do not leak response text or restore the entered key', async () => {
  const f = await fixture();
  try {
    f.fail(Object.assign(new Error('fixture-private-key'), { code: 'local_setup_conflict' }));
    const key = f.host.querySelector('input[type=password]');
    await f.change(key, 'fixture-private-key');
    await f.submit(); await f.finish();
    assert.equal(key.value, '');
    assert.equal(f.saved.length, 0);
    assert.match(f.host.querySelector('[role=alert]').textContent, /localSetup.conflict/);
    assert.ok(!f.host.textContent.includes('fixture-private-key'));
    assert.equal(f.window.localStorage.length, 0);
    assert.equal(f.host.querySelector('button[type=submit]').disabled, false);
  } finally { await f.close(); }
});
