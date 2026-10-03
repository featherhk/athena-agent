import assert from 'node:assert/strict';
import test from 'node:test';
import { FrontendReporter, observeFrontend, observesAppAction } from '../src/frontend-observation.ts';

const component = (id, extra = {}) => ({ id, version: 2, title: 'Private title', html: '<input value="private">', ...extra });
const facts = (extra = {}) => ({ sessionId: 's1', components: [], preview: null, helpIds: [], choosingFiles: false,
  uploading: false, reconnecting: false, hasError: false, hasUnsentChanges: false, attachmentIds: [], ...extra });
const rect = (x = 10, y = 10, width = 320, height = 180) => ({ x, y, width, height, top: y, left: x, right: x + width, bottom: y + height });
function node(dataset, box = rect()) {
  return { dataset, getBoundingClientRect: () => box,
    get textContent() { assert.fail('Text must never be inspected'); },
    get value() { assert.fail('Form values must never be inspected'); },
    get contentWindow() { assert.fail('Generated iframe contents must never be inspected'); } };
}
function document({ widgets = [], views = [], workspace = null, password = false, hidden = false, width = 1200, height = 800 } = {}) {
  return { hidden, defaultView: { innerWidth: width, innerHeight: height },
    querySelectorAll(selector) { return selector === '[data-app-view]' ? views : selector === '.widget[data-component-id]' ? widgets : assert.fail(selector); },
    querySelector(selector) { return selector.startsWith('.workspace-panel') ? workspace : selector.startsWith('.account-profile') && password ? node({}) : null; } };
}
const semantic = () => observeFrontend(document(), facts());

test('observation contains only bounded host metadata, never user text or component source', () => {
  const state = observeFrontend(document({ widgets: [node({ componentId: 'c1', componentInstance: 'frame1', componentState: 'mounted', placement: 'inline' })],
    views: [node({ appView: 'workspace' })], workspace: node({ componentId: 'c1' }) }), facts({
    components: [component('c1', { placement: 'top-end', view_revision: 3 })], hasUnsentChanges: true, attachmentIds: ['file1'],
  }));
  assert.deepEqual(state.components[0], { id: 'c1', version: 2, view_revision: 3, instance: 'frame1', placement: 'inline',
    rect: { x: 10, y: 10, width: 320, height: 180 }, state: 'mounted', visible: true });
  assert.deepEqual(state.workspace, { id: 'c1', version: 2 });
  assert.equal(state.has_unsent_changes, true);
  assert.deepEqual(state.attachment_ids, ['file1']);
  assert.ok(!JSON.stringify(state).includes('Private'));
  assert.ok(!JSON.stringify(state).includes('private'));
  assert.deepEqual(Object.keys(state).sort(), ['attachment_ids', 'components', 'errors', 'foreground', 'has_unsent_changes', 'open_views', 'preview', 'session_id', 'viewport', 'waiting', 'workspace'].sort());
});

test('visibility accounts for offscreen, removed, hidden, covered and background components', () => {
  const widgets = [node({ componentId: 'a', componentState: 'mounted' }), node({ componentId: 'b' }, rect(10, 900)), node({ componentId: 'c' })];
  const input = facts({ components: [component('a'), component('b'), component('c', { view: { hidden: true } }), component('removed')] });
  const state = observeFrontend(document({ widgets }), input);
  assert.deepEqual(state.components.map(item => item.visible), [true, false, false, false]);
  assert.equal(state.components[3].state, 'unobserved');
  assert.ok(observeFrontend(document({ widgets, hidden: true }), input).components.every(item => !item.visible));
  assert.ok(observeFrontend(document({ widgets, views: [node({ appView: 'preview' })] }), input).components.every(item => !item.visible));
});

test('an opened preview is distinct from a loaded file; failures are codes without content', () => {
  const doc = document({ views: [node({ appView: 'preview' }), node({ appView: 'unsupported-private-name' })] });
  for (const status of ['loading', 'ready', 'failed']) {
    const state = observeFrontend(doc, facts({ preview: { file_id: 'file1', status, error_code: 'sensitive error details' } }));
    assert.deepEqual(state.open_views, ['preview']);
    assert.equal(state.preview.status, status);
    assert.equal(state.preview.error_code, status === 'failed' ? 'preview_unavailable' : undefined);
  }
  assert.equal(observeFrontend(document(), facts({ preview: { file_id: 'closed-file', status: 'ready' } })).preview, null);
});

test('bounds cap observations and omit unknown component error strings', () => {
  const components = Array.from({ length: 40 }, (_, i) => component(`c${i}`));
  const widgets = components.map(item => node({ componentId: item.id, componentState: 'failed', componentError: item.id === 'c0' ? 'private stacktrace' : 'script_error' }, rect(-1e7, 1e7, 1e7, 1e7)));
  const result = observeFrontend(document({ widgets, width: 1e7, height: 1e7, views: [node({ appView: 'account' })], password: true }), facts({ components,
    helpIds: Array.from({ length: 20 }, (_, i) => `help${i}`), choosingFiles: true, uploading: true, reconnecting: true, hasError: true,
    attachmentIds: Array.from({ length: 20 }, (_, i) => `file${i}`),
  }));
  assert.equal(result.components.length, 32); assert.equal(result.waiting.length, 8); assert.equal(result.errors.length, 8); assert.equal(result.attachment_ids.length, 10);
  assert.deepEqual(result.viewport, { width: 16384, height: 16384 });
  assert.deepEqual(result.components[0].rect, { x: -32768, y: 32768, width: 32768, height: 32768 });
  assert.equal(result.components[0].error_code, undefined);
  assert.ok(result.open_views.includes('password'));
  assert.ok(!JSON.stringify(result).includes('stacktrace'));
});

test('revisions change only for semantics; reads do not masquerade as delivered reports', async () => {
  let state = semantic(); const sent = [];
  const reporter = new FrontendReporter('client1', () => state, async item => { sent.push(item); });
  assert.equal(reporter.snapshot().revision, 1);
  await reporter.report(); await reporter.report(); await reporter.report(true);
  assert.deepEqual(sent.map(item => item.revision), [1, 1]);
  state = { ...state, session_id: 's2' };
  const next = await reporter.report();
  assert.equal(next.revision, 2); assert.equal(next.session_id, 's2');
});

test('simultaneous reports deduplicate and persist changing revisions in order', async () => {
  let state = semantic(), release;
  const first = new Promise(resolve => { release = resolve; }), sent = [];
  const reporter = new FrontendReporter('client', () => state, async item => { sent.push(item.revision); if (item.revision === 1) await first; });
  const one = reporter.report(), duplicate = reporter.report(true);
  assert.equal(one, duplicate);
  await Promise.resolve();
  state = { ...state, foreground: false }; const two = reporter.report();
  await Promise.resolve(); assert.deepEqual(sent, [1]);
  release(); await Promise.all([one, two]); assert.deepEqual(sent, [1, 2]);
});

test('slow delivery keeps only the latest queued state and resolves all coalesced callers with it', async () => {
  let state = semantic(), release;
  const barrier = new Promise(resolve => { release = resolve; }), sent = [];
  const reporter = new FrontendReporter('client', () => state, async item => { sent.push(item); if (sent.length === 1) await barrier; });
  const first = reporter.report(); await Promise.resolve();
  const waiting = [];
  for (let i = 0; i < 50; i++) {
    state = { ...state, session_id: `s${i + 2}`, viewport: { width: i + 1000, height: 800 } };
    waiting.push(reporter.report());
  }
  assert.ok(waiting.every(promise => promise === waiting[0]));
  assert.equal(sent.length, 1);
  release(); const results = await Promise.all([first, ...waiting]);
  assert.deepEqual(sent.map(item => item.revision), [1, 51]);
  assert.equal(results[0].revision, 1);
  assert.ok(results.slice(1).every(item => item.revision === 51 && item.session_id === 's51'));
});

test('stopping during a slow request discards the latest unsent state without another upload', async () => {
  let state = semantic(), release;
  const barrier = new Promise(resolve => { release = resolve; }), sent = [];
  const reporter = new FrontendReporter('client', () => state, async item => { sent.push(item); await barrier; });
  const first = reporter.report(); await Promise.resolve();
  state = { ...state, session_id: 's2' }; const latest = reporter.report();
  reporter.stop(); assert.equal(await latest, null);
  release(); assert.equal(await first, null); assert.equal(sent.length, 1);
});

test('failed uploads retry the same revision and cleanup prevents queued old identity reports', async () => {
  let attempts = 0;
  const reporter = new FrontendReporter('client', semantic, async () => { if (++attempts === 1) throw Error('offline'); });
  await assert.rejects(reporter.report(), /offline/);
  assert.equal((await reporter.report()).revision, 1); assert.equal(attempts, 2);
  reporter.stop(); assert.equal(await reporter.report(), null); assert.equal(attempts, 2);
  const stopped = new FrontendReporter('old-account', semantic, async () => assert.fail('disposed identity sent'));
  const pending = stopped.report(); stopped.stop(); assert.equal(await pending, null);
});

test('UI receipts verify effects in the current client without requiring origin conversation', () => {
  const state = { client_id: 'client', revision: 4, ...semantic(), session_id: 'destination', open_views: ['preview'], preview: { file_id: 'file1', status: 'loading' }, attachment_ids: ['file1'] };
  const action = (kind, payload) => ({ id: 'a', session_id: 'origin', action: kind, status: 'claimed', payload });
  assert.equal(observesAppAction(action('navigate', { session_id: 'destination' }), state), true);
  assert.equal(observesAppAction(action('preview', { file_id: 'file1' }), state), true);
  assert.equal(observesAppAction(action('preview', { file_id: 'other' }), state), false);
  assert.equal(observesAppAction(action('open', { target: 'history' }), state), false);
  assert.equal(observesAppAction(action('close', { target: 'preview' }), state), false);
  assert.equal(observesAppAction(action('attach', { file_id: 'file1' }), state), true);
  assert.equal(observesAppAction(action('attach', {}), state), false);
  assert.equal(observesAppAction(action('attach', {}), { ...state, waiting: [{ kind: 'file_selection' }] }), true);
  assert.equal(observesAppAction(action('detach', { file_id: 'file1' }), state), false);
});
