import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import { act, createElement as h } from 'react';
import { widgetDocument } from '../src/widget-document.ts';
import * as appearance from '../src/appearance.ts';
import * as effects from '../src/effects.ts';
import * as layout from '../src/component-layout.ts';
import * as forms from '../src/component-forms.ts';
import * as interactions from '../src/surface-interactions.ts';
import * as anchors from '../src/text-anchors.ts';
import * as runtime from '../src/surface-runtime.ts';
import { readSurfaceFile } from '../src/surface-files.ts';

// Every document, request and clock here is synthetic. No browser profile,
// application origin, account, network service or provider is accessed.
const settings = { disableCSSFileLoading: true, disableJavaScriptFileLoading: true, disableIframePageLoading: true };
const windows = [];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const rejects = (promise, message) => assert.rejects(promise, error => error.message === message);
function bridge(surface = true, instance = 'surface-fixture') {
  const frame = new Window({ url: 'https://surface-fixture.invalid', settings }); windows.push(frame);
  const sent = [], timers = new Map(); let timerId = 0, clock = 0;
  const parent = { postMessage: (data, target) => sent.push({ data, target }) };
  Object.defineProperty(frame, 'parent', { configurable: true, value: parent });
  frame.setTimeout = (callback, delay) => { timers.set(++timerId, { callback, at: clock + delay }); return timerId; };
  frame.clearTimeout = id => timers.delete(id);
  frame.document.write(widgetDocument('<main><h1>A custom work surface</h1><textarea aria-label="Draft"></textarea></main>', 'en', instance, undefined, false, surface));
  // Execute the real generated bridge in a real DOM, without relying on source
  // matching or allowing Happy DOM to navigate the iframe anywhere.
  frame.eval(frame.document.querySelector('script').textContent);
  const receive = (data, source = parent) => frame.dispatchEvent(new frame.MessageEvent('message', { source, data: { instance, ...data } }));
  const requests = () => sent.map(item => item.data).filter(item => item.type === 'athena:surface-request');
  return { frame, parent, sent, receive, requests, surface: frame.Athena.surface,
    advance(milliseconds) {
      clock += milliseconds;
      for (const [id, timer] of [...timers]) if (timer.at <= clock) { timers.delete(id); timer.callback(); }
    },
  };
}

test('only replacement surfaces expose the frozen surface API; ordinary components keep their existing bridge', () => {
  const ordinary = bridge(false), replacement = bridge();
  assert.equal('surface' in ordinary.frame.Athena, false);
  assert.equal(typeof ordinary.frame.Athena.submit, 'function');
  assert.equal(Object.isFrozen(replacement.surface), true);
  assert.deepEqual(Object.keys(replacement.surface).sort(), ['draft', 'file', 'messages', 'request', 'snapshot', 'subscribe', 'writeDraft']);
  ordinary.receive({ type: 'athena:surface-response', id: 1, value: { private: true } });
  assert.equal(ordinary.requests().length, 0);
});

test('surface RPC responses require the actual parent, current instance and matching request ID', async () => {
  const fixture = bridge();
  let settled = false;
  const pending = fixture.surface.snapshot().then(value => { settled = true; return value; });
  const [request] = fixture.requests();
  assert.equal(request.method, 'snapshot'); assert.equal(request.id, 1);
  fixture.receive({ type: 'athena:surface-response', id: 1, value: 'wrong source' }, {});
  fixture.receive({ type: 'athena:surface-response', instance: 'old-frame', id: 1, value: 'old instance' });
  fixture.receive({ type: 'athena:surface-response', id: 2, value: 'wrong ID' });
  await Promise.resolve(); assert.equal(settled, false);
  fixture.receive({ type: 'athena:surface-response', id: 1, value: { revision: 'revision-1', phase: 'idle' } });
  assert.deepEqual(await pending, { revision: 'revision-1', phase: 'idle' });
  fixture.advance(30000);
  assert.equal(fixture.requests().length, 1, 'a completed receipt never sends another request');
});

test('restoring an inner focus requires the current parent, frame and active document', () => {
  const fixture = bridge();
  const input = fixture.frame.document.querySelector('textarea');
  input.focus();
  let focused = true, calls = 0;
  fixture.frame.document.hasFocus = () => focused;
  input.focus = options => { assert.equal(options.preventScroll, true); calls++; };
  fixture.receive({ type: 'athena:restore-focus' }, {});
  fixture.receive({ type: 'athena:restore-focus', instance: 'old-frame' });
  assert.equal(calls, 0);
  fixture.receive({ type: 'athena:restore-focus' }); assert.equal(calls, 1);
  focused = false;
  fixture.receive({ type: 'athena:restore-focus' }); assert.equal(calls, 1, 'a late receipt cannot steal focus back');
  focused = true; input.remove();
  fixture.receive({ type: 'athena:restore-focus' }); assert.equal(calls, 1, 'a removed inner control stays retired');
});

test('surface methods send their complete arguments and host rejections reach their callers', async () => {
  const fixture = bridge(), longText = 'Untruncated draft '.repeat(1700);
  const operations = [
    ['messages', { before: 'message-51', limit: 50 }], ['draft', undefined],
    ['writeDraft', { revision: 'draft-1', text: longText }],
    ['request', { action: 'send', text: 'Please search the itinerary.' }],
  ];
  for (const [method, args] of operations) {
    const pending = args === undefined ? fixture.surface[method]() : fixture.surface[method](args);
    const request = fixture.requests().at(-1);
    assert.equal(request.method, method);
    assert.deepEqual(JSON.parse(JSON.stringify(request.args)), args ?? {});
    fixture.receive({ type: 'athena:surface-response', id: request.id, value: { accepted: true } });
    assert.deepEqual(await pending, { accepted: true });
  }
  const pending = fixture.surface.writeDraft({ revision: 'stale', text: 'An old draft' });
  const rejection = rejects(pending, 'draft_changed');
  fixture.receive({ type: 'athena:surface-response', id: fixture.requests().at(-1).id, error: 'draft_changed' });
  await rejection;
});

test('surface file reads use a file identifier and receive data only through the frame RPC', async () => {
  const fixture = bridge(), pending = fixture.surface.file('owned-photo');
  const request = fixture.requests().at(-1);
  assert.equal(request.method, 'file'); assert.deepEqual(JSON.parse(JSON.stringify(request.args)), { file_id: 'owned-photo' });
  const value = { id: 'owned-photo', kind: 'image', data_url: 'data:image/png;base64,AAAA' };
  fixture.receive({ type: 'athena:surface-response', id: request.id, value });
  assert.deepEqual(await pending, value);
  const rejected = fixture.surface.file('too-large'), failure = rejects(rejected, 'image_too_large');
  fixture.receive({ type: 'athena:surface-response', id: fixture.requests().at(-1).id, error: 'image_too_large' });
  await failure;
});

test('timed-out mutations report unknown delivery and never automatically retry, even after a late response', async () => {
  const fixture = bridge();
  const mutation = fixture.surface.request({ action: 'send', text: 'Create one item only.' });
  const mutationRejected = rejects(mutation, 'delivery_unknown');
  const read = fixture.surface.snapshot(), readRejected = rejects(read, 'surface_timeout');
  fixture.advance(29999); await Promise.resolve(); assert.equal(fixture.requests().length, 2);
  fixture.advance(1);
  await Promise.all([mutationRejected, readRejected]);
  for (const request of fixture.requests()) fixture.receive({ type: 'athena:surface-response', id: request.id, value: { accepted: true } });
  fixture.advance(120000); await Promise.resolve();
  assert.equal(fixture.requests().length, 2);
  const explicitNewRequest = fixture.surface.snapshot();
  assert.equal(fixture.requests().at(-1).id, 3);
  fixture.receive({ type: 'athena:surface-response', id: 3, value: 'A new read requested by the caller' });
  assert.equal(await explicitNewRequest, 'A new read requested by the caller');
});

test('frame backpressure caps pending requests and releases a slot only on a receipt or timeout', async () => {
  const fixture = bridge();
  const pending = Array.from({ length: 32 }, () => fixture.surface.snapshot());
  await rejects(fixture.surface.snapshot(), 'surface_busy');
  assert.equal(fixture.requests().length, 32);
  fixture.receive({ type: 'athena:surface-response', id: 1, value: 'first' });
  assert.equal(await pending.shift(), 'first');
  const last = fixture.surface.messages({ limit: 10 });
  assert.equal(fixture.requests().at(-1).id, 33);
  for (let id = 2; id <= 33; id++) fixture.receive({ type: 'athena:surface-response', id, value: { id } });
  const results = await Promise.all([...pending, last]);
  assert.deepEqual(results.map(item => item.id), Array.from({ length: 32 }, (_, index) => index + 2));
});

test('subscriptions reject foreign events, unsubscribe cleanly and isolate a failing callback', () => {
  const fixture = bridge(), revisions = [];
  const unsubscribe = fixture.surface.subscribe(value => revisions.push(value.revision));
  fixture.receive({ type: 'athena:surface-changed', revision: 'foreign' }, {});
  fixture.receive({ type: 'athena:surface-changed', instance: 'old-frame', revision: 'old' });
  assert.deepEqual(revisions, []);
  fixture.receive({ type: 'athena:surface-changed', revision: 'one' });
  unsubscribe();
  fixture.receive({ type: 'athena:surface-changed', revision: 'two' });
  assert.deepEqual(revisions, ['one']);
  const broken = fixture.surface.subscribe(() => { throw new Error('synthetic component failure'); });
  const valid = fixture.surface.subscribe(value => revisions.push(value.revision));
  fixture.receive({ type: 'athena:surface-changed', revision: 'three' });
  assert.deepEqual(revisions, ['one', 'three']);
  assert.equal(fixture.sent.some(item => item.data.type === 'athena:health' && item.data.error_code === 'script_error'), true);
  broken(); valid();
  assert.throws(() => fixture.surface.subscribe(null), /invalid_subscriber/);
  const stops = Array.from({ length: 32 }, () => fixture.surface.subscribe(() => {}));
  assert.throws(() => fixture.surface.subscribe(() => {}), /invalid_subscriber/);
  stops.forEach(stop => stop());
  const stop = fixture.surface.subscribe(() => {}); stop();
});

// Real React mounting verifies the host listener, not a reimplementation of it.
const window = new Window({ url: 'https://host-fixture.invalid', settings }); windows.push(window);
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'MutationObserver', 'ResizeObserver', 'IntersectionObserver']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
for (const name of ['getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) globalThis[name] = window[name].bind(window);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const i18n = { useI18n: () => ({ locale: 'en', t: key => key, preferences: { ui_palette: 'dream' }, reduced: true }) };
const apiCalls = [];
function compile(name, overrides = {}) {
  const compiled = ts.transpileModule(readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} };
  vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: name })(
    name => name.endsWith('.css') ? {} : name in overrides ? overrides[name] : require(name), module, module.exports);
  return module.exports;
}
const Widget = compile('Widget.tsx', {
  './api': { api: async (...args) => { apiCalls.push(args); return {}; } }, './i18n': i18n,
  './widget-document': { widgetDocument }, './appearance': appearance, './effects': effects,
  './component-layout': layout, './component-forms': forms, './surface-interactions': interactions,
  './text-anchors': anchors, './surface-runtime': runtime,
  './ComponentControls': compile('ComponentControls.tsx', { './i18n': i18n, './component-layout': layout }),
  './CanvasActions': compile('CanvasActions.tsx'),
}).default;

async function mountWidget(surfaceHost, placement = 'surface') {
  const host = document.createElement('main'); host.className = 'app'; document.body.append(host);
  const root = createRoot(host);
  const props = { component: { id: 'synthetic-surface', title: 'Synthetic surface', html: '<p>Content</p>', version: 1, placement },
    sessionId: 'synthetic-session', bounds: { x: 0, y: 0, width: 1000, height: 700 },
    submit() {}, onView: async () => {}, onDraft() {}, surfaceHost };
  await act(async () => root.render(h(Widget, props)));
  const iframe = host.querySelector('iframe'), sent = [];
  const frame = { postMessage: (data, target) => sent.push({ data, target }) };
  Object.defineProperty(iframe, 'contentWindow', { configurable: true, value: frame });
  const instance = host.querySelector('[data-component-instance]').dataset.componentInstance;
  const send = async (data, source = frame) => { await act(async () => window.dispatchEvent(new window.MessageEvent('message', {
    source, data: { type: 'athena:surface-request', instance, id: 1, method: 'snapshot', args: {}, ...data },
  }))); };
  return { host, frame, sent, instance, send,
    render: async patch => { Object.assign(props, patch); await act(async () => root.render(h(Widget, props))); },
    close: async () => { await act(async () => root.unmount()); host.remove(); },
  };
}

test('mounted ordinary Widget ignores forged surface requests without a host capability', async () => {
  const fixture = await mountWidget(undefined, 'inline');
  try {
    await fixture.send({ method: 'request', args: { action: 'send', text: 'Must not run' } });
    assert.equal(fixture.sent.filter(item => item.data.type === 'athena:surface-response').length, 0);
    assert.equal(apiCalls.length, 0);
  } finally { await fixture.close(); }
});

test('mounted surface Widget authenticates frame identity and sends a real RPC response once per caller ID', async () => {
  const calls = [];
  const fixture = await mountWidget({ revision: 'revision-1', call: async (method, args) => { calls.push({ method, args }); return { revision: 'actual' }; } });
  try {
    await fixture.send({}, {}); await fixture.send({ instance: 'stale-instance' });
    assert.equal(calls.length, 0); assert.equal(fixture.sent.length, 0);
    await fixture.send({}); await fixture.send({});
    assert.deepEqual(calls, [{ method: 'snapshot', args: {} }]);
    const replies = fixture.sent.filter(item => item.data.type === 'athena:surface-response');
    assert.equal(replies.length, 2, 'both delivery attempts receive the same single-execution receipt');
    assert.deepEqual(replies.map(item => item.data.value), [{ revision: 'actual' }, { revision: 'actual' }]);
    await fixture.render({ surfaceHost: { revision: 'revision-2', call: async () => ({ revision: 'new' }) } });
    assert.equal(fixture.sent.at(-1).data.type, 'athena:surface-changed');
    assert.equal(fixture.sent.at(-1).data.revision, 'revision-2');
  } finally { await fixture.close(); }
});

test('unmounted Widget sends no late success receipt and never dispatches stale messages', async () => {
  const gate = deferred(); let calls = 0;
  const fixture = await mountWidget({ revision: 'revision-1', call: () => { calls++; return gate.promise; } });
  await fixture.send({ method: 'request', args: { action: 'send', text: 'A single request' } });
  assert.equal(calls, 1);
  await fixture.close();
  await act(async () => gate.resolve({ run_id: 'already-admitted' }));
  assert.equal(fixture.sent.filter(item => item.data.type === 'athena:surface-response').length, 0);
  await fixture.send({ id: 2 });
  assert.equal(calls, 1);
});

test('mounted Widget reports only explicit surface errors and cannot execute after its host is withdrawn', async () => {
  let calls = 0;
  const fixture = await mountWidget({ revision: 'revision-1', call: async () => {
    calls++; if (calls === 1) throw new Error('Private server details must not cross the bridge.');
    throw new runtime.SurfaceError('draft_changed');
  } });
  try {
    await fixture.send({ id: 1 });
    assert.equal(fixture.sent.at(-1).data.error, 'operation_failed');
    await fixture.send({ id: 2 });
    assert.equal(fixture.sent.at(-1).data.error, 'draft_changed');
    await fixture.render({ surfaceHost: undefined });
    const count = fixture.sent.length;
    await fixture.send({ id: 3 });
    assert.equal(calls, 2); assert.equal(fixture.sent.length, count);
    assert.equal(JSON.stringify(fixture.sent).includes('Private server details'), false);
  } finally { await fixture.close(); }
});

test('unmount discards an asynchronous failed receipt without sending into another frame', async () => {
  const gate = deferred();
  const fixture = await mountWidget({ revision: 'revision-1', call: () => gate.promise });
  await fixture.send({});
  await fixture.close();
  await act(async () => gate.reject(new runtime.SurfaceError('request_failed')));
  assert.equal(fixture.sent.filter(item => item.data.type === 'athena:surface-response').length, 0);
});

test('a component version change retires old async receipts and rejects stale-frame messages', async () => {
  const gate = deferred(); let calls = 0;
  const fixture = await mountWidget({ revision: 'revision-1', call: () => { calls++; return calls === 1 ? gate.promise : Promise.resolve('New version'); } });
  try {
    await fixture.send({}); assert.equal(calls, 1);
    await fixture.render({ component: { id: 'synthetic-surface', title: 'Synthetic surface', html: '<p>New content</p>', version: 2, placement: 'surface' } });
    const nextInstance = fixture.host.querySelector('[data-component-instance]').dataset.componentInstance;
    assert.notEqual(nextInstance, fixture.instance);
    await act(async () => gate.resolve('Old version response'));
    assert.equal(fixture.sent.filter(item => item.data.type === 'athena:surface-response').length, 0);
    await fixture.send({ id: 2 }); assert.equal(calls, 1);
    await fixture.send({ id: 1, instance: nextInstance });
    assert.equal(calls, 2);
    const replies = fixture.sent.filter(item => item.data.type === 'athena:surface-response');
    assert.deepEqual(replies.map(item => item.data.value), ['New version']);
    assert.equal(replies[0].data.instance, nextInstance);
  } finally { await fixture.close(); }
});

test('the generated surface script and mounted React host complete a full paginated bridge roundtrip', async () => {
  const messages = Array.from({ length: 73 }, (_, index) => ({ id: `message-${index}`, role: index % 2 ? 'assistant' : 'user',
    content: `Message ${index}: ` + 'Retained words '.repeat(1000), status: 'complete' }));
  const methods = [];
  const call = async (method, args) => {
    methods.push(method);
    if (method === 'messages') return runtime.messagePage(messages, args);
    if (method === 'snapshot') return { revision: 'one', message_count: messages.length };
    throw new runtime.SurfaceError('invalid_request');
  };
  const host = await mountWidget({ revision: 'one', call });
  const frame = bridge(true, host.instance), revisions = [];
  const parentSend = frame.parent.postMessage, hostSend = host.frame.postMessage;
  frame.parent.postMessage = (data, target) => {
    parentSend(data, target);
    window.dispatchEvent(new window.MessageEvent('message', { source: host.frame, data }));
  };
  host.frame.postMessage = (data, target) => { hostSend(data, target); frame.receive(data); };
  try {
    let snapshot, newest, oldest;
    await act(async () => { snapshot = await frame.surface.snapshot(); });
    assert.deepEqual(snapshot, { revision: 'one', message_count: 73 });
    await act(async () => { newest = await frame.surface.messages({ limit: 50 }); });
    assert.equal(newest.items.length, 50);
    await act(async () => { oldest = await frame.surface.messages({ before: newest.before, limit: 50 }); });
    assert.equal(oldest.items.length, 23); assert.equal(oldest.has_more, false);
    assert.deepEqual([...oldest.items, ...newest.items].map(item => [item.id, item.content]), messages.map(item => [item.id, item.content]));
    frame.surface.subscribe(value => revisions.push(value.revision));
    await host.render({ surfaceHost: { revision: 'two', call } });
    assert.deepEqual(revisions, ['two']);
    assert.deepEqual(methods, ['snapshot', 'messages', 'messages']);
  } finally { await host.close(); }
});

test('a replacement renderer places real file RPC image, Word and Excel data in its own DOM', async () => {
  const snapshot = { messages: [], files: ['photo', 'word', 'sheet'].map(id => ({ id, name: id, size: 4, media_type: id === 'photo' ? 'image/png' : 'application/octet-stream' })) };
  const canonical = structuredClone(snapshot), requests = [];
  const transport = async url => {
    requests.push(url);
    if (url.endsWith('/photo/content')) return new Response(new Uint8Array([1, 2, 3, 4]), { headers: { 'content-type': 'image/png' } });
    const preview = url.includes('/photo/') ? { kind: 'image' } : url.includes('/word/')
      ? { kind: 'document', blocks: [{ kind: 'paragraph', text: 'Estimate <not markup>', heading: 1 }], truncated: false }
      : { kind: 'spreadsheet', sheets: [{ name: 'Budget', rows: [['Paint', '180']], row_numbers: [1], truncated: false }], truncated: false };
    return new Response(JSON.stringify(preview));
  };
  const host = await mountWidget({ revision: 'files-one', call: (method, args) => {
    assert.equal(method, 'file'); return readSurfaceFile(() => snapshot, args.file_id, () => true, transport);
  } });
  const frame = bridge(true, host.instance), parentSend = frame.parent.postMessage, hostSend = host.frame.postMessage;
  frame.parent.postMessage = (data, target) => { parentSend(data, target); window.dispatchEvent(new window.MessageEvent('message', { source: host.frame, data })); };
  host.frame.postMessage = (data, target) => { hostSend(data, target); frame.receive(data); };
  try {
    let photo, word, sheet;
    await act(async () => { photo = await frame.surface.file('photo'); word = await frame.surface.file('word'); sheet = await frame.surface.file('sheet'); });
    const image = frame.frame.document.createElement('img'); image.src = photo.data_url;
    Object.assign(image.style, { position: 'absolute', left: '83px', top: '120px', transform: 'rotate(17deg)', opacity: '0.6' });
    const title = frame.frame.document.createElement('h1'); title.textContent = word.blocks[0].text;
    const table = frame.frame.document.createElement('table');
    for (const values of sheet.sheets[0].rows) { const row = table.insertRow(); for (const value of values) row.insertCell().textContent = value; }
    frame.frame.document.body.append(image, title, table);
    assert.equal(image.getAttribute('src'), 'data:image/png;base64,AQIDBA=='); assert.equal(image.style.left, '83px');
    assert.equal(image.style.transform, 'rotate(17deg)'); assert.equal(image.style.opacity, '0.6');
    assert.equal(title.textContent, 'Estimate <not markup>'); assert.equal(title.children.length, 0);
    assert.deepEqual([...table.querySelectorAll('td')].map(cell => cell.textContent), ['Paint', '180']);
    assert.deepEqual(snapshot, canonical); assert.equal(host.host.querySelector('table'), null, 'The table belongs to the replacement document, not a host preview panel');
    assert.deepEqual(requests.sort(), ['/api/files/photo/content', '/api/files/photo/preview', '/api/files/sheet/preview', '/api/files/word/preview']);
  } finally { await host.close(); }
});

after(async () => { for (const instance of windows) await instance.happyDOM.abort(); });
