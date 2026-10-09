import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Window } from 'happy-dom';
import * as loader from '../src/desktop-widget-loader.ts';

const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/DesktopWidgetPage.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;

async function fixture({ user = { id: 'owner' }, role = 'widget' } = {}) {
  const window = new Window({ url: 'https://local.example/?desktop=widget&session=chat&component=collector&version=2&scope=owner:chat' });
  const document = window.document;
  const globals = new Map();
  for (const [key, value] of Object.entries({ window, document, navigator: window.navigator, IS_REACT_ACT_ENVIRONMENT: true })) {
    globals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  let widget, mounts = 0, saveFailure = null;
  const calls = [], native = [], applied = [];
  const component = { id: 'collector', version: 2, title: 'Collector', html: '<textarea>Draft</textarea>', placement: 'desktop', view_revision: 3 };
  async function api(path, body) {
    calls.push({ path, body });
    if (path === '/auth/me') return { user };
    if (path === '/sessions/chat') return { components: [component], preferences: { ui_language: 'fr' } };
    if (path.endsWith('/view')) {
      if (saveFailure) throw saveFailure;
      return { version: 2, view_revision: 4, view: body.view };
    }
    throw new Error('Unexpected request');
  }
  const applyPreferences = value => applied.push(value);
  const fake = name => {
    if (name === './api') return { api };
    if (name === './i18n') return { useI18n: () => ({ t: key => key, applyPreferences }) };
    if (name === './desktop.css') return {};
    if (name === './desktop-widget-loader') return loader;
    if (name === './desktop-bridge') return { desktopRole: () => role, desktopPost: value => { native.push(value); return true; } };
    if (name === './Widget') return { default: props => {
      widget = props;
      React.useEffect(() => { mounts++; props.onHealth({ state: 'mounted', has_content: true, error_code: null }); }, []);
      return React.createElement('textarea', { defaultValue: 'Unsent draft' });
    } };
    return require(name);
  };
  const module = { exports: {} };
  vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`, { window, AbortController })(fake, module, module.exports);
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => { root.render(React.createElement(module.exports.default)); });
  return { window, document, host, native, applied, calls, get widget() { return widget; }, get mounts() { return mounts; },
    failSave: error => { saveFailure = error; },
    async close() {
      await act(async () => root.unmount());
      await window.happyDOM.abort();
      for (const [key, descriptor] of globals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
      }
    } };
}

test('native widget page renders one authenticated widget and relays interactions without a new message request', async () => {
  const f = await fixture();
  try {
    assert.equal(f.host.querySelectorAll('textarea').length, 1);
    assert.equal(f.document.documentElement.dataset.desktop, 'widget');
    assert.deepEqual(f.applied, [{ ui_language: 'fr' }]);
    assert.equal(f.native[0].action, 'widgetHealth'); assert.equal(f.native[0].state, 'mounted');
    f.widget.submit('Inspect these files'); f.widget.onNavigate('another-chat');
    f.widget.onHitRegions([{ x: 1, y: 2, width: 30, height: 40 }]);
    assert.deepEqual(JSON.parse(JSON.stringify(f.native.slice(1))), [
      { action: 'submit', text: 'Inspect these files' }, { action: 'navigate', session_id: 'another-chat' },
      { action: 'hitRegions', regions: [{ x: 1, y: 2, width: 30, height: 40 }] },
    ]);
    assert.ok(f.calls.every(item => item.body === undefined));
  } finally { await f.close(); }
});

test('a failed layout write preserves the live widget, unsent draft and selection without automatic replay', async () => {
  const f = await fixture();
  try {
    const input = f.host.querySelector('textarea'); input.value = 'Keep this draft'; input.setSelectionRange(2, 7);
    f.failSave(new Error('network unavailable'));
    await act(async () => { await assert.rejects(f.widget.onView(f.widget.component, { hidden: true }), /network unavailable/); });
    assert.equal(f.host.querySelector('textarea'), input); assert.equal(f.mounts, 1);
    assert.equal(input.value, 'Keep this draft'); assert.equal(input.selectionStart, 2); assert.equal(input.selectionEnd, 7);
    assert.equal(f.calls.filter(item => item.body !== undefined).length, 1);
    assert.equal(f.host.querySelector('[role="status"]').textContent, 'componentUnavailable');
  } finally { await f.close(); }
});

test('no authentication or ordinary web role never renders the registered component', async () => {
  for (const options of [{ user: null }, { user: { id: 'other' } }, { role: null }]) {
    const f = await fixture(options);
    try {
      assert.equal(f.host.querySelector('textarea'), null); assert.equal(f.mounts, 0);
      assert.equal(f.native.at(-1).state, 'failed');
      assert.ok(f.calls.every(item => item.path === '/auth/me'));
      if (options.role === null) assert.equal(f.calls.length, 0);
    } finally { await f.close(); }
  }
});
