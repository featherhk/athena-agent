import assert from 'node:assert/strict';
import test from 'node:test';
import { prepareMessageSource } from '../src/frontend-submission.ts';

const observed = (change = {}) => ({ client_id: 'window-a', session_id: 'chat-a', foreground: true, ...change });
const ready = () => ({ clientId: 'window-a', sessionId: 'chat-a' });
const base = { clientId: 'window-a', sessionId: 'chat-a', rendered: ready, current: () => true, report: async () => observed() };

test('first message waits for committed navigation before asking for a source observation', async () => {
  let committed = { clientId: 'window-a', sessionId: null }, frames = 0, reports = 0;
  const id = await prepareMessageSource({ ...base, rendered: () => committed,
    nextFrame: async () => { frames++; committed = ready(); },
    report: async () => { reports++; assert.deepEqual(committed, ready()); return observed(); } });
  assert.equal(id, 'window-a'); assert.equal(frames, 1); assert.equal(reports, 1);
});

test('navigation or reset during a pending observation cannot submit to the old conversation', async () => {
  let current = true, resolve;
  const pending = prepareMessageSource({ ...base, current: () => current, report: () => new Promise(done => { resolve = done; }) });
  current = false; resolve(observed());
  await assert.rejects(pending, error => error.code === 'frontend_conflict');
});

test('new account or revoked client while preparing never binds to its replacement', async () => {
  let source = ready(), resolve;
  const pending = prepareMessageSource({ ...base, rendered: () => source, report: () => new Promise(done => { resolve = done; }) });
  source = { ...source, clientId: 'window-b' }; resolve(observed({ client_id: 'window-b' }));
  await assert.rejects(pending, error => error.code === 'frontend_conflict');
});

test('missing, hidden, wrong-window and old-conversation observations never fall back to unbound send', async () => {
  for (const value of [null, observed({ foreground: false }), observed({ client_id: 'window-b' }), observed({ session_id: null })])
    await assert.rejects(prepareMessageSource({ ...base, report: async () => value }), error => error.code === 'frontend_unavailable');
});

test('an observation outage is bounded and does not replay submission or use another window', async () => {
  let reports = 0;
  await assert.rejects(prepareMessageSource({ ...base, timeoutMs: 10, report: () => { reports++; return new Promise(() => {}); } }), error => error.code === 'frontend_unavailable');
  assert.equal(reports, 1);
  await assert.rejects(prepareMessageSource({ ...base, report: async () => { throw Error('offline'); } }), error => error.code === 'frontend_unavailable');
});

test('timed-out render waits do not start an observation after they eventually wake', async () => {
  let source = null, wake, reports = 0;
  const pending = prepareMessageSource({ ...base, timeoutMs: 10, rendered: () => source,
    nextFrame: () => new Promise(resolve => { wake = resolve; }), report: async () => { reports++; return observed(); } });
  await assert.rejects(pending, error => error.code === 'frontend_unavailable');
  source = ready(); wake(); await Promise.resolve(); await Promise.resolve();
  assert.equal(reports, 0);
});
