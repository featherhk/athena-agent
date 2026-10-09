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

async function fixture({ user = { id: 'owner' }, role = 'widget', file = null } = {}) {
  const window = new Window({ url: 'https://local.example/?desktop=widget&session=chat&component=collector&version=2&scope=owner:chat' });
  const document = window.document;
  const globals = new Map();
  for (const [key, value] of Object.entries({ window, document, navigator: window.navigator, IS_REACT_ACT_ENVIRONMENT: true })) {
    globals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  let widget, artifact, mounts = 0, artifactMounts = 0, saveFailure = null;
  const calls = [], native = [], applied = [];
  const component = { id: 'collector', version: 2, title: 'Collector', html: '<textarea>Draft</textarea>', placement: 'desktop', view_revision: 3 };
  if (file) { component.file_id = file.id; component.file = file; component.html = ''; }
  async function api(path, body) {
    calls.push({ path, body });
    if (path === '/auth/me') return { user };
    if (path === '/sessions/chat') return { components: [{ ...component }], preferences: { ui_language: 'fr' } };
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
    if (name === './DesktopArtifact') return { default: props => {
      artifact = props;
      React.useEffect(() => { artifactMounts++; }, []);
      return React.createElement('section', { 'data-file-id': props.file.id }, props.file.name);
    } };
    return require(name);
  };
  const module = { exports: {} };
  vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`, { window, AbortController })(fake, module, module.exports);
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => { root.render(React.createElement(module.exports.default)); });
  return { window, document, host, native, applied, calls, component, get widget() { return widget; }, get artifact() { return artifact; }, get mounts() { return mounts; }, get artifactMounts() { return artifactMounts; },
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
    assert.equal(f.native.find(item => item.action === 'widgetHealth').state, 'mounted');
    const beforeInteraction = f.native.length;
    f.widget.submit('Inspect these files'); f.widget.onNavigate('another-chat');
    f.widget.onHitRegions([{ x: 1, y: 2, width: 30, height: 40 }]);
    assert.deepEqual(JSON.parse(JSON.stringify(f.native.slice(beforeInteraction))), [
      { action: 'submit', text: 'Inspect these files' }, { action: 'navigate', session_id: 'another-chat' },
      { action: 'hitRegions', regions: [{ x: 1, y: 2, width: 30, height: 40 }] },
    ]);
    assert.ok(f.calls.every(item => item.body === undefined));
  } finally { await f.close(); }
});

test('registered file uses the canonical preview host and reports real file health without an HTML widget', async () => {
  const file = { id: 'budget', session_id: 'chat', name: 'Budget.xlsx', size: 1200, content_url: '/api/files/budget/content', preview_url: '/api/files/budget/preview', content_revision: 's1-current' };
  const f = await fixture({ file });
  try {
    assert.equal(f.host.querySelector('[data-file-id="budget"]').textContent, 'Budget.xlsx');
    assert.equal(f.mounts, 0); assert.equal(f.artifactMounts, 1);
    assert.equal(f.artifact.file, file);
    for (const status of ['loading', 'ready', 'failed']) f.artifact.onObservation({ file_id: 'budget', status });
    assert.deepEqual(JSON.parse(JSON.stringify(f.native.filter(item => item.action === 'widgetHealth'))), [
      { action: 'widgetHealth', state: 'loading', file_id: 'budget', content_revision: 's1-current' },
      { action: 'widgetHealth', state: 'mounted', file_id: 'budget', content_revision: 's1-current' },
      { action: 'widgetHealth', state: 'failed', file_id: 'budget', content_revision: 's1-current', error_code: 'preview_unavailable' },
    ]);
    f.artifact.onObservation({ file_id: 'another', status: 'ready' });
    assert.equal(f.native.filter(item => item.action === 'widgetHealth').length, 3);
    assert.ok(f.calls.every(item => item.body === undefined));
  } finally { await f.close(); }
});

test('parent refresh is scope bound, fetches current preferences and preserves the file preview instance', async () => {
  const file = { id: 'budget', session_id: 'chat', name: 'Budget.xlsx', size: 1200, content_url: '/api/files/budget/content', preview_url: '/api/files/budget/preview', content_revision: 's1-current' };
  const f = await fixture({ file });
  try {
    const node = f.host.querySelector('[data-file-id]');
    await act(async () => f.window.dispatchEvent(new f.window.CustomEvent('athena:widget-refresh', { detail: { scope: 'other:chat', component_id: 'collector', version: 2 } })));
    assert.equal(f.calls.length, 2);
    f.component.file = { ...file, name: 'Current title.xlsx' };
    await act(async () => f.window.dispatchEvent(new f.window.CustomEvent('athena:widget-refresh', { detail: { scope: 'owner:chat', component_id: 'collector', version: 2 } })));
    assert.equal(f.host.querySelector('[data-file-id]'), node);
    assert.equal(node.textContent, 'Current title.xlsx'); assert.equal(f.artifactMounts, 1);
    assert.equal(f.calls.length, 4); assert.equal(f.applied.length, 2);
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
    assert.equal(f.host.querySelector('[role="status"]').textContent, 'componentUnavailableretry');
  } finally { await f.close(); }
});

test('no authentication or ordinary web role never renders the registered component', async () => {
  for (const options of [{ user: null }, { user: { id: 'other' } }, { role: null }]) {
    const f = await fixture(options);
    try {
      assert.equal(f.host.querySelector('textarea'), null); assert.equal(f.mounts, 0);
      assert.equal(f.native.filter(item => item.action === 'widgetHealth').at(-1).state, 'failed');
      assert.ok(f.native.some(item => item.action === 'hitRegions' && item.regions[0]?.width > 0), 'Retry must remain clickable after an initial load failure');
      assert.ok(f.calls.every(item => item.path === '/auth/me'));
      if (options.role === null) assert.equal(f.calls.length, 0);
    } finally { await f.close(); }
  }
});
