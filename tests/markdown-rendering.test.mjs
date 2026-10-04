import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import React, { act, createContext, createElement as h, useContext } from 'react';
import Markdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkReadableAutolinks } from '../src/markdown-autolinks.ts';
import * as traceFormat from '../src/trace-format.ts';
import * as messageOrigin from '../src/message-origin.ts';

// A standalone DOM, never a browser profile or an application endpoint.
const window = new Window({ url: 'https://athena.invalid', settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true } });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'MutationObserver']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const Locale = createContext('en');
const i18n = { useI18n: () => { const locale = useContext(Locale); return { t: key => `${locale}:${key}` }; }, errorMessage: code => code };
function compile(name, overrides = {}) {
  const compiled = ts.transpileModule(readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} };
  vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: name })(
    name => name.endsWith('.css') ? {} : name in overrides ? overrides[name] : require(name), module, module.exports,
  );
  return module.exports;
}
const styledText = compile('StyledText.tsx');
const imageResults = compile('ImageResult.tsx', { './i18n': i18n });
let parses = 0;
const components = compile('MessageBody.tsx', {
  './i18n': i18n, './StyledText': styledText, './ImageResult': imageResults, 'remark-gfm': { default: remarkGfm, __esModule: true },
  './markdown-autolinks': { remarkReadableAutolinks },
  'react-markdown': { __esModule: true, defaultUrlTransform, default: props => { parses++; return h(Markdown, props); } },
});
const { MessageBody } = components;
async function mount() {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  return { host, render: async element => { await act(async () => root.render(element)); },
    close: async () => { await act(async () => root.unmount()); host.remove(); } };
}
after(() => window.happyDOM.abort());

test('typing and unrelated parent updates do not reparse historical Markdown', async () => {
  const fixture = await mount();
  const messages = Array.from({ length: 60 }, (_, i) => ({ id: String(i), content: `### Note ${i}\n\nA **durable** answer with [a link](https://example.invalid).\n\n| Item | Cost |\n|---|---|\n| Material | 180 |` }));
  const view = (draft, rows = messages) => h('main', null,
    h('textarea', { value: draft, onChange() {}, 'aria-label': 'draft' }),
    ...rows.map(row => h(MessageBody, { key: row.id, content: row.content })));
  parses = 0;
  try {
    await fixture.render(view('')); assert.equal(parses, 60);
    const field = fixture.host.querySelector('textarea'); field.focus();
    for (let i = 1; i <= 20; i++) await fixture.render(view('正在输入'.repeat(i)));
    assert.equal(parses, 60, 'the controlled draft must not make the full transcript parse again');
    assert.equal(document.activeElement, field);
    assert.equal(field.value, '正在输入'.repeat(20));
    await fixture.render(view(field.value, messages.map(row => ({ ...row }))));
    assert.equal(parses, 60, 'snapshot hydration with new objects but identical content is also cheap');
    assert.equal(fixture.host.querySelectorAll('table').length, 60);
    assert.equal(fixture.host.querySelector('a').getAttribute('rel'), 'noopener noreferrer');
  } finally { await fixture.close(); }
});

test('each streamed change and the final tail render without reparsing unchanged messages', async () => {
  const fixture = await mount(); parses = 0;
  const stable = Array.from({ length: 25 }, (_, i) => h(MessageBody, { key: i, content: `Stable ${i}` }));
  try {
    for (const [index, text] of ['A', 'A **new', 'A **new answer**', 'A **new answer**\n\nFinal tail: 完整。'].entries()) {
      await fixture.render(h('main', null, ...stable, h(MessageBody, { key: 'live', content: text, streaming: index < 3 })));
      assert.equal(parses, 25 + index + 1);
    }
    assert.equal(fixture.host.lastElementChild.lastElementChild.textContent, 'A new answer\nFinal tail: 完整。');
  } finally { await fixture.close(); }
});

test('text effects, reasoning, locale and owned-image rules stay live across memo boundaries', async () => {
  const fixture = await mount(); parses = 0;
  const content = 'Hello ![owned](/api/files/file_1/content) ![remote](https://example.invalid/pixel.png)';
  const view = (locale, extra = {}) => h(Locale.Provider, { value: locale }, h(MessageBody, { content, reasoning: 'Thinking **first**', ...extra }));
  try {
    await fixture.render(view('en', { streaming: true })); assert.equal(parses, 2);
    assert.equal(fixture.host.querySelector('summary').textContent, 'en:message.reasoningStreaming');
    assert.equal(fixture.host.querySelectorAll('img').length, 1);
    await fixture.render(view('fr')); assert.equal(parses, 2);
    assert.equal(fixture.host.querySelector('summary').textContent, 'fr:message.reasoning');
    const effect = { message_id: 'm1', unit: 'character', styles: [{ color: 'red', transform: 'translateY(4px)' }] };
    await fixture.render(view('fr', { effect })); assert.equal(parses, 3);
    assert.equal(fixture.host.querySelector('[data-message-text] span').style.color, 'red');
    await fixture.render(view('fr', { effect, reasoning: 'Thinking **first**. Final reasoning.' })); assert.equal(parses, 4);
    assert.match(fixture.host.querySelector('details').textContent, /Final reasoning\./);
    await fixture.render(view('fr')); assert.equal(parses, 6);
    assert.equal(Boolean(fixture.host.querySelector('[data-message-text] span[style*="inline-block"]')), false, 'reset can remove a text effect without removing image controls');
  } finally { await fixture.close(); }
});

test('streaming after an owned image preserves the loaded element without opening a separate view', async () => {
  const fixture = await mount();
  const content = '![Finished picture](/api/files/file_1/content)';
  try {
    await fixture.render(h(MessageBody, { content, streaming: true }));
    const image = fixture.host.querySelector('img');
    await act(async () => image.dispatchEvent(new window.Event('load')));
    assert.equal(fixture.host.querySelector('[data-image-state]').dataset.imageState, 'ready');
    for (const suffix of ['\n\nHere', '\n\nHere is the', '\n\nHere is the completed result.']) {
      await fixture.render(h(MessageBody, { content: content + suffix, streaming: true }));
      assert.equal(fixture.host.querySelector('img') === image, true);
      assert.equal(fixture.host.querySelector('[data-image-state]').dataset.imageState, 'ready');
      assert.equal(document.querySelectorAll('dialog').length, 0);
    }
  } finally { await fixture.close(); }
});

test('a Markdown link wrapping an owned image cannot cause nested navigation or nested download anchors', async () => {
  const fixture = await mount();
  try {
    await fixture.render(h(MessageBody, { content: '[![Picture](/api/files/file_1/content)](https://elsewhere.invalid)' }));
    assert.equal(fixture.host.querySelectorAll('a a, a button').length, 0);
    assert.equal(fixture.host.querySelectorAll('a').length, 1);
    assert.equal(fixture.host.querySelector('a').getAttribute('href'), '/api/files/file_1/content?download=1');
  } finally { await fixture.close(); }
});

test('completed activity stops polling unrelated cursor changes but preserves pagination and reconnect recovery', async () => {
  let calls = [], fail = false;
  const entry = { id: 'a1', seq: 1, role: 'assistant', status: 'complete', created_at: '', content: 'Final answer', content_chars: 12,
    reasoning: 'Complete reasoning', reasoning_chars: 18, tool_calls: '', tool_calls_chars: 0 };
  const { RunTrace } = compile('RunTrace.tsx', { './i18n': i18n, './MessageBody': components, './trace-format': traceFormat, './message-origin': messageOrigin,
    './api': { api: async url => { calls.push(url); if (fail) throw new Error('offline'); return { run: { status: 'complete' }, entries: [entry], total: 2, next_after: 1, has_more: true }; } } });
  const fixture = await mount(), view = revision => h(RunTrace, { sessionId: 's', runId: 'r', status: 'complete', phase: 'responding', revision });
  try {
    await fixture.render(view(1));
    const details = fixture.host.querySelector('details');
    await act(async () => { details.open = true; details.dispatchEvent(new window.Event('toggle')); });
    // Initial open can schedule one catch-up load; let that settle first.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 630)); });
    const initial = calls.length;
    for (let i = 2; i < 12; i++) await fixture.render(view(i));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 630)); });
    assert.equal(calls.length, initial);
    assert.match(details.textContent, /Complete reasoning/);
    const next = [...fixture.host.querySelectorAll('button')].find(button => button.textContent.endsWith('trace.next'));
    fail = true;
    await act(async () => { next.click(); await new Promise(resolve => setTimeout(resolve, 30)); });
    assert.equal(calls.length, initial + 1); assert.match(details.textContent, /offline/);
    fail = false;
    await act(async () => { window.dispatchEvent(new window.Event('online')); await new Promise(resolve => setTimeout(resolve, 630)); });
    assert.equal(calls.length, initial + 2);
    assert.doesNotMatch(details.textContent, /offline/);
  } finally { await fixture.close(); }
});

test('running activity fetches the final status even when no new cursor arrives', async () => {
  let calls = 0;
  const { RunTrace } = compile('RunTrace.tsx', { './i18n': i18n, './MessageBody': components, './trace-format': traceFormat, './message-origin': messageOrigin,
    './api': { api: async () => { calls++; return { run: { status: calls > 1 ? 'complete' : 'running' }, entries: [], total: 0, next_after: 0, has_more: false }; } } });
  const fixture = await mount(), view = status => h(RunTrace, { sessionId: 's', runId: 'r', status, phase: 'responding', revision: 3 });
  try {
    await fixture.render(view('running'));
    const details = fixture.host.querySelector('details');
    await act(async () => { details.open = true; details.dispatchEvent(new window.Event('toggle')); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 630)); });
    const initial = calls;
    await fixture.render(view('complete'));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 630)); });
    assert.equal(calls, initial + 1);
  } finally { await fixture.close(); }
});

test('surface layout observes only while a floating component needs bounds, then disconnects', async () => {
  const pending = new Map(); let frames = 0, created = 0, disconnected = 0, measured = 0;
  globalThis.requestAnimationFrame = callback => { pending.set(++frames, callback); return frames; };
  globalThis.cancelAnimationFrame = id => pending.delete(id);
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.ResizeObserver = class {
    constructor() { created++; }
    observe() {}
    disconnect() { disconnected++; }
  };
  const { useSurfaceBounds } = compile('use-surface-bounds.ts');
  const fixture = await mount(); fixture.host.className = 'app';
  function Probe({ enabled }) {
    const bounds = useSurfaceBounds(enabled);
    return h(React.Fragment, null, h('header', { className: 'topbar' }), h('div', { className: 'conversation' }),
      h('footer', { className: 'composer-area' }), h('output', null, JSON.stringify(bounds)));
  }
  const settleMutations = () => new Promise(resolve => setTimeout(resolve, 5));
  try {
    await fixture.render(h(Probe, { enabled: false }));
    fixture.host.querySelector('.topbar').getBoundingClientRect = () => { measured++; return { bottom: 80 }; };
    fixture.host.querySelector('.composer-area').getBoundingClientRect = () => { measured++; return { top: 600 }; };
    fixture.host.querySelector('.conversation').getBoundingClientRect = () => { measured++; return { width: 600, x: 100 }; };
    fixture.host.querySelector('.conversation').append(document.createTextNode('A streamed answer'));
    await settleMutations(); assert.equal(created, 0); assert.equal(frames, 0); assert.equal(measured, 0);
    await fixture.render(h(Probe, { enabled: true }));
    assert.equal(created, 1); assert.equal(measured, 3);
    assert.equal(JSON.parse(fixture.host.querySelector('output').textContent).y, 92);
    await settleMutations();
    await act(async () => { for (const [id, callback] of [...pending]) { pending.delete(id); callback(); } });
    fixture.host.querySelector('.conversation').append(document.createElement('p'));
    await settleMutations(); assert.ok(pending.size > 0, 'new workspace content still schedules geometry while needed');
    await fixture.render(h(Probe, { enabled: false }));
    assert.equal(disconnected, 1); assert.equal(pending.size, 0);
    const stoppedAt = frames;
    fixture.host.querySelector('.conversation').append(document.createElement('p'));
    window.dispatchEvent(new window.Event('resize'));
    await settleMutations(); assert.equal(frames, stoppedAt);
    await fixture.render(h(Probe, { enabled: true }));
    assert.equal(created, 2, 'a later registered floating component can reactivate measurement');
  } finally { await fixture.close(); }
  assert.equal(disconnected, 2);
});
