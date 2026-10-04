import assert from 'node:assert/strict';
import test from 'node:test';
import { messagePage, selectedSurface, surfaceKey, surfaceRecovery, SurfaceDispatcher, SurfaceError } from '../src/surface-runtime.ts';

const component = (id, changes = {}) => ({ id, version: 1, title: id, html: '<p>Surface</p>', placement: 'surface', created_at: '2026-10-04T00:00:00+00:00', ...changes });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const rejects = (promise, code) => assert.rejects(promise, error => error instanceof SurfaceError && error.message === code);

test('surface selection follows persisted visibility, placement and newest activation without mutating source', () => {
  const rows = [component('default-slot', { placement: 'workspace' }), component('initial'),
    component('hidden', { view: { hidden: true }, created_at: '2026-10-05T00:00:00+00:00' }),
    component('moved-out', { view: { placement: 'inline' }, created_at: '2026-10-06T00:00:00+00:00' }),
    component('reopened', { placement: 'workspace', view: { placement: 'surface' }, view_updated_at: '2026-10-07T00:00:00+00:00', view_revision: 3 }),
  ];
  const original = structuredClone(rows);
  assert.equal(selectedSurface(rows)?.id, 'reopened');
  assert.deepEqual(rows, original);
  assert.equal(surfaceKey(rows[4]), 'reopened:1:3');
  assert.equal(surfaceKey(rows[1]), 'initial:1:0');
  assert.equal(selectedSurface([]), undefined);
  assert.equal(selectedSurface([rows[0], rows[2], rows[3]]), undefined);
  assert.equal(selectedSurface([component('updated-source', { created_at: '2026-10-08T00:00:00+00:00', view_updated_at: '2026-10-01T00:00:00+00:00' }), rows[4]])?.id, 'updated-source');
});

test('surface tie-breaking matches backend ASCII ordering regardless of locale or array order', () => {
  const rows = ['A', 'a', 'a_', 'a-', 'Z', 'z'].map(id => component(id));
  for (let length = 2; length <= rows.length; length++) {
    const subset = rows.slice(0, length), expected = subset.map(item => item.id).sort().at(-1);
    assert.equal(selectedSurface(subset)?.id, expected);
    assert.equal(selectedSurface(subset.toReversed())?.id, expected);
  }
});

test('message pages recover every visible message and all long text without duplication or truncation', () => {
  const messages = Array.from({ length: 137 }, (_, index) => ({ id: `message-${index}`, role: index % 2 ? 'assistant' : 'user',
    content: `Full message ${index}. ` + 'Long retained content. '.repeat(2000), reasoning: 'Visible reasoning. '.repeat(1000),
    status: index === 136 ? 'streaming' : 'complete', run_id: `run-${index}`, internal_payload: 'Do not expose a provider snapshot.',
    attachments: [{ id: `file-${index}`, name: 'Notes.md' }], help_reply: { id: 'receipt', reason: 'Missing date', items: [] },
    origin: { kind: 'schedule', task_id: 'task-1' },
  }));
  const original = structuredClone(messages), recovered = [];
  let before;
  do {
    const page = messagePage(messages, { limit: 50, ...(before === undefined ? {} : { before }) });
    assert.ok(page.items.length <= 50);
    assert.equal(page.has_more, page.before !== null);
    recovered.unshift(...page.items);
    before = page.before;
  } while (before !== null);
  assert.deepEqual(recovered.map(item => item.id), messages.map(item => item.id));
  for (let index = 0; index < recovered.length; index++) {
    const actual = recovered[index], expected = messages[index];
    assert.equal(actual.content, expected.content);
    assert.equal(actual.reasoning, expected.reasoning);
    assert.deepEqual(actual.attachments, expected.attachments);
    assert.deepEqual(actual.help_reply, expected.help_reply);
    assert.deepEqual(actual.origin, expected.origin);
    assert.equal('internal_payload' in actual, false);
  }
  assert.deepEqual(messages, original);
});

test('message cursors remain stable when streaming appends new messages and invalid cursors are explicit', () => {
  const messages = Array.from({ length: 35 }, (_, index) => ({ id: `id-${index}`, role: 'user', content: `Message ${index}`, status: 'complete' }));
  const first = messagePage(messages, {});
  assert.equal(first.items.length, 30);
  assert.equal(first.before, 'id-5');
  messages.push({ id: 'new', role: 'assistant', content: 'New text', status: 'streaming' });
  const next = messagePage(messages, { before: first.before });
  assert.deepEqual(next.items.map(item => item.id), ['id-0', 'id-1', 'id-2', 'id-3', 'id-4']);
  assert.deepEqual(messagePage(messages, { before: 'id-0' }), { items: [], before: null, has_more: false });
  assert.deepEqual(messagePage([], {}), { items: [], before: null, has_more: false });
  for (const limit of [0, 51, -1, 1.5, '10', null, Infinity, NaN, true]) assert.throws(() => messagePage(messages, { limit }), /invalid_request/);
  for (const before of ['missing', null, 4, {}]) assert.throws(() => messagePage(messages, { before }), /message_not_found/);
});

test('message paging never admits raw tool or system transcripts through content or cursors', () => {
  const messages = [{ id: 'user', role: 'user', content: 'My task', status: 'complete' },
    { id: 'system', role: 'system', content: 'Internal instructions', status: 'complete' },
    { id: 'tool', role: 'tool', content: 'Internal tool transcript', status: 'complete' },
    { id: 'assistant', role: 'assistant', content: 'My response', status: 'complete' }];
  assert.deepEqual(messagePage(messages, {}).items.map(item => item.id), ['user', 'assistant']);
  assert.equal(messagePage(messages, { limit: 1 }).before, 'assistant');
  assert.deepEqual(messagePage(messages, { before: 'assistant' }).items.map(item => item.id), ['user']);
  for (const before of ['system', 'tool']) assert.throws(() => messagePage(messages, { before }), /message_not_found/);
});

test('surface recovery is explicit and does not activate from unrelated query content', () => {
  assert.equal(surfaceRecovery('?recovery=1'), true);
  assert.equal(surfaceRecovery('?session=one&recovery=1'), true);
  for (const query of ['', '?recovery=0', '?recovery=true', '?query=recovery%3D1']) assert.equal(surfaceRecovery(query), false);
});

test('in-flight, completed and failed RPC receipts execute each mutation at most once', async () => {
  const dispatcher = new SurfaceDispatcher(), gate = deferred();
  const calls = [];
  const call = (method, args) => { calls.push({ method, args }); return gate.promise; };
  const first = dispatcher.dispatch(1, 'send', { text: 'One request' }, call);
  const duplicate = dispatcher.dispatch(1, 'send', { text: 'One request' }, call);
  await Promise.resolve();
  assert.equal(calls.length, 1);
  gate.resolve({ run_id: 'run-1' });
  assert.deepEqual(await first, { run_id: 'run-1' });
  assert.deepEqual(await duplicate, { run_id: 'run-1' });
  assert.deepEqual(await dispatcher.dispatch(1, 'send', { text: 'Changed text cannot reuse a mutation ID' }, call), { run_id: 'run-1' });
  assert.equal(calls.length, 1);
  let failures = 0;
  const failure = () => { failures++; throw new SurfaceError('request_failed'); };
  await rejects(dispatcher.dispatch(2, 'send', {}, failure), 'request_failed');
  await rejects(dispatcher.dispatch(2, 'send', {}, failure), 'request_failed');
  assert.equal(failures, 1);
});

test('evicted, skipped and invalid RPC identifiers cannot be replayed', async () => {
  const dispatcher = new SurfaceDispatcher();
  let calls = 0;
  const call = () => ++calls;
  for (let id = 1; id <= 65; id++) assert.equal(await dispatcher.dispatch(id, 'observe', {}, call), id);
  await rejects(dispatcher.dispatch(1, 'observe', {}, call), 'receipt_expired');
  assert.equal(await dispatcher.dispatch(2, 'observe', {}, call), 2);
  assert.equal(await dispatcher.dispatch(100, 'observe', {}, call), 66);
  await rejects(dispatcher.dispatch(99, 'observe', {}, call), 'receipt_expired');
  for (const id of [0, -1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, 1.5, '101', true, null, {}]) await rejects(dispatcher.dispatch(id, 'observe', {}, call), 'invalid_request');
  for (const args of [undefined, null, false, 'text', [], 1]) await rejects(dispatcher.dispatch(101, 'observe', args, call), 'invalid_request');
  for (const method of [undefined, null, 1, {}]) await rejects(dispatcher.dispatch(101, method, {}, call), 'invalid_request');
  assert.equal(calls, 66);
  assert.equal(await dispatcher.dispatch(101, 'observe', {}, call), 67);
});

test('RPC backpressure bounds concurrent work and never queues a rejected mutation later', async () => {
  const dispatcher = new SurfaceDispatcher(), gate = deferred();
  let calls = 0;
  const call = () => { calls++; return gate.promise; };
  const admitted = Array.from({ length: 32 }, (_, index) => dispatcher.dispatch(index + 1, 'send', {}, call));
  await rejects(dispatcher.dispatch(33, 'send', {}, call), 'surface_busy');
  await Promise.resolve();
  assert.equal(calls, 32);
  const duplicate = dispatcher.dispatch(1, 'send', {}, call);
  gate.resolve('accepted');
  assert.deepEqual(await Promise.all(admitted), Array(32).fill('accepted'));
  assert.equal(await duplicate, 'accepted');
  await rejects(dispatcher.dispatch(33, 'send', {}, call), 'receipt_expired');
  assert.equal(await dispatcher.dispatch(34, 'send', {}, call), 'accepted');
  assert.equal(calls, 33);
});

test('a long-running mutation remains single-execution after receipt-cache eviction', async () => {
  const dispatcher = new SurfaceDispatcher(), gate = deferred();
  let mutations = 0;
  const pending = dispatcher.dispatch(1, 'send', {}, () => { mutations++; return gate.promise; });
  for (let id = 2; id < 72; id++) await dispatcher.dispatch(id, 'observe', {}, () => 'observed');
  await rejects(dispatcher.dispatch(1, 'send', {}, () => { mutations++; }), 'receipt_expired');
  gate.resolve('durable-run-id');
  assert.equal(await pending, 'durable-run-id');
  assert.equal(mutations, 1);
});

test('disposing a frame prevents queued work and future calls without replaying already started work', async () => {
  const notStarted = new SurfaceDispatcher();
  let starts = 0;
  const queued = notStarted.dispatch(1, 'send', {}, () => { starts++; });
  notStarted.dispose();
  await rejects(queued, 'surface_closed');
  await rejects(notStarted.dispatch(2, 'send', {}, () => { starts++; }), 'surface_closed');
  assert.equal(starts, 0);
  const active = new SurfaceDispatcher(), gate = deferred();
  const pending = active.dispatch(1, 'send', {}, () => { starts++; return gate.promise; });
  await Promise.resolve();
  assert.equal(starts, 1);
  active.dispose();
  gate.resolve('A durable request may already have started.');
  assert.equal(await pending, 'A durable request may already have started.');
  await rejects(active.dispatch(1, 'send', {}, () => { starts++; }), 'surface_closed');
  assert.equal(starts, 1);
});

test('file receipts deduplicate only in flight and release completed large payloads without replaying their IDs', async () => {
  const dispatcher = new SurfaceDispatcher(), gate = deferred(); let calls = 0;
  const payload = { kind: 'image', data_url: 'data:image/png;base64,' + 'A'.repeat(1024 * 1024) };
  const call = () => { calls++; return gate.promise; };
  const first = dispatcher.dispatch(1, 'file', { file_id: 'owned' }, call);
  const duplicate = dispatcher.dispatch(1, 'file', { file_id: 'must not replace original' }, call);
  await Promise.resolve(); assert.equal(calls, 1);
  gate.resolve(payload); assert.equal(await first, payload); assert.equal(await duplicate, payload);
  assert.equal(dispatcher.fileReceipts.size, 0); assert.equal(dispatcher.receipts.has(1), false, 'The dispatcher retains no resolved image promise');
  await rejects(dispatcher.dispatch(1, 'file', { file_id: 'owned' }, call), 'receipt_expired'); assert.equal(calls, 1);
  assert.equal(await dispatcher.dispatch(2, 'file', { file_id: 'owned' }, call), payload); assert.equal(calls, 2);
  assert.equal(dispatcher.fileReceipts.size, 0);
});

test('at most two file reads run while other RPCs retain the overall 32-request budget', async () => {
  const dispatcher = new SurfaceDispatcher(), firstGate = deferred(), secondGate = deferred(), otherGate = deferred();
  let files = 0, others = 0;
  const first = dispatcher.dispatch(1, 'file', {}, () => { files++; return firstGate.promise; });
  const second = dispatcher.dispatch(2, 'file', {}, () => { files++; return secondGate.promise; });
  await rejects(dispatcher.dispatch(3, 'file', {}, () => { files++; }), 'surface_busy');
  const other = Array.from({ length: 30 }, (_, index) => dispatcher.dispatch(index + 4, 'request', {}, () => { others++; return otherGate.promise; }));
  await rejects(dispatcher.dispatch(34, 'request', {}, () => { others++; }), 'surface_busy');
  assert.equal(files, 2); assert.equal(others, 30);
  const duplicate = dispatcher.dispatch(1, 'file', {}, () => { files++; });
  firstGate.resolve('first'); assert.equal(await first, 'first'); assert.equal(await duplicate, 'first');
  await rejects(dispatcher.dispatch(3, 'file', {}, () => { files++; }), 'receipt_expired');
  assert.equal(await dispatcher.dispatch(35, 'file', {}, () => { files++; return 'explicit new read'; }), 'explicit new read');
  assert.equal(files, 3);
  secondGate.resolve('second'); otherGate.resolve('other'); await Promise.all([second, ...other]);
  assert.equal(dispatcher.fileReceipts.size, 0); assert.equal(dispatcher.pending, 0);
});

test('file failures and disposal release capacity without weakening in-flight deduplication during receipt churn', async () => {
  const dispatcher = new SurfaceDispatcher(), gate = deferred(); let files = 0;
  const first = dispatcher.dispatch(1, 'file', {}, () => { files++; return gate.promise; });
  for (let id = 2; id < 72; id++) await dispatcher.dispatch(id, 'snapshot', {}, () => 'state');
  const duplicate = dispatcher.dispatch(1, 'file', {}, () => { files++; });
  const failed = rejects(first, 'file_changed'), duplicateFailed = rejects(duplicate, 'file_changed');
  gate.reject(new SurfaceError('file_changed')); await Promise.all([failed, duplicateFailed]);
  assert.equal(files, 1); assert.equal(dispatcher.fileReceipts.size, 0);
  await rejects(dispatcher.dispatch(1, 'file', {}, () => { files++; }), 'receipt_expired');
  const waiting = deferred(), pending = dispatcher.dispatch(72, 'file', {}, () => waiting.promise);
  await Promise.resolve(); dispatcher.dispose(); assert.equal(dispatcher.fileReceipts.size, 0);
  waiting.resolve('caller owns its response'); await pending;
  assert.equal(dispatcher.pending, 0);
  await rejects(dispatcher.dispatch(73, 'file', {}, () => { files++; }), 'surface_closed');
});
