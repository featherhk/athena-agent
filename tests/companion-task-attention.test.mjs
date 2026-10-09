import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import { act, createElement as h } from 'react';
import * as continuity from '../src/task-continuity.ts';

const window = new Window({ url: 'https://athena.invalid' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url), module = { exports: {} };
const compiled = ts.transpileModule(readFileSync(new URL('../src/CompanionTaskAttention.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: 'CompanionTaskAttention.tsx' })(name => {
  if (name.endsWith('.css')) return {};
  if (name === './i18n') return { useI18n: () => ({ t: key => key }) };
  if (name === './task-continuity') return continuity;
  return require(name);
}, module, module.exports);
const Attention = module.exports.default;
after(() => window.happyDOM.abort());

for (const status of ['complete', 'waiting_user']) {
  test(`current work keeps its status while another conversation is ${status}`, async () => {
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host), calls = [];
    const items = [{ run_id: 'background', session_id: 'other', title: 'Other task', status,
      needs_user: status === 'waiting_user', attention_key: 'unread', created_at: '2026-10-09', updated_at: '2026-10-09' }];
    const original = JSON.stringify(items);
    const render = (busy, label) => act(async () => root.render(h(Attention, {
      items, currentSession: 'current', busy, open: () => calls.push('activity'),
    }, h('span', { className: 'companion-status', role: 'status', title: 'Make my workbook' }, label))));
    try {
      await render(false, 'Continue');
      const attentionLabel = status === 'complete' ? 'continuity.ready' : 'continuity.needsYou';
      assert.equal(host.querySelector('button').textContent, attentionLabel);
      for (const label of ['Thinking', 'Taking action', 'Writing a reply', 'Waiting for your answer']) {
        await render(true, label);
        assert.equal(host.querySelector('button'), null, 'an unrelated result must not replace the current status');
        assert.equal(host.querySelector('[role="status"]').textContent, label);
        assert.equal(host.querySelector('[role="status"]').title, 'Make my workbook');
      }
      assert.deepEqual(calls, [], 'status changes cannot open or acknowledge background work');
      assert.equal(JSON.stringify(items), original, 'background attention remains unread during current work');
      await render(false, 'Continue');
      assert.equal(host.querySelector('button').textContent, attentionLabel);
      await act(async () => host.querySelector('button').click());
      assert.deepEqual(calls, ['activity']);
    } finally { await act(async () => root.unmount()); host.remove(); }
  });
}
