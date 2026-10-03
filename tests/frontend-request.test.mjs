import assert from 'node:assert/strict';
import test from 'node:test';
import { frontendRequest } from '../src/frontend-request.ts';
import { FrontendReporter } from '../src/frontend-observation.ts';
import { prepareMessageSource } from '../src/frontend-submission.ts';

const aborted = error => error.name === 'AbortError';
const untilAbort = signal => new Promise((_resolve, reject) => {
  signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
});

test('a deadline aborts a stalled transport rather than only abandoning its promise', async () => {
  let transportSignal;
  await assert.rejects(frontendRequest(new AbortController().signal, signal => {
    transportSignal = signal;
    return untilAbort(signal);
  }, 10), aborted);
  assert.equal(transportSignal.aborted, true);
});

test('the response body and subsequent identity lookup share the same request deadline', async () => {
  for (const stalledAt of ['body', 'identity']) {
    let transportSignal, phase;
    await assert.rejects(frontendRequest(new AbortController().signal, async signal => {
      transportSignal = signal;
      await Promise.resolve({ status: 200 });
      phase = 'body';
      if (stalledAt === 'body') return untilAbort(signal);
      await Promise.resolve({ code: 'frontend_identity_changed' });
      phase = 'identity';
      return untilAbort(signal);
    }, 10), aborted);
    assert.equal(phase, stalledAt);
    assert.equal(transportSignal.aborted, true);
  }
});

test('cleanup aborts an active transport and an already-aborted owner never starts one', async () => {
  const owner = new AbortController(); let transportSignal, entered;
  const started = new Promise(resolve => { entered = resolve; });
  const pending = frontendRequest(owner.signal, signal => {
    transportSignal = signal; entered(); return untilAbort(signal);
  });
  await started; owner.abort();
  await assert.rejects(pending, aborted);
  assert.equal(transportSignal.aborted, true);
  let calls = 0;
  await assert.rejects(frontendRequest(owner.signal, async () => { calls++; }), aborted);
  assert.equal(calls, 0);
});

test('finished requests remove owner listeners and clear the timer without later aborting their transport', async () => {
  const owner = new AbortController();
  const originalAdd = owner.signal.addEventListener.bind(owner.signal), originalRemove = owner.signal.removeEventListener.bind(owner.signal);
  let listening = 0, transportSignal;
  owner.signal.addEventListener = (...args) => { listening++; return originalAdd(...args); };
  owner.signal.removeEventListener = (...args) => { listening--; return originalRemove(...args); };
  assert.equal(await frontendRequest(owner.signal, async signal => { transportSignal = signal; return 'acknowledged'; }, 10), 'acknowledged');
  assert.equal(listening, 0);
  owner.abort();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(transportSignal.aborted, false);
});

test('a failed transport also releases the parent listener', async () => {
  const owner = new AbortController(); let removed = 0;
  const remove = owner.signal.removeEventListener.bind(owner.signal);
  owner.signal.removeEventListener = (...args) => { removed++; return remove(...args); };
  await assert.rejects(frontendRequest(owner.signal, async () => { throw Error('offline'); }), /offline/);
  assert.equal(removed, 1);
});

test('a reporter recovers after a black hole and the next source preparation performs a new acknowledged request', async () => {
  let calls = 0, firstSignal;
  const owner = new AbortController();
  const state = { session_id: 'chat', foreground: true, viewport: { width: 1000, height: 800 }, open_views: [],
    preview: null, workspace: null, components: [], waiting: [], errors: [], has_unsent_changes: true, attachment_ids: [] };
  const reporter = new FrontendReporter('client', () => state, () => frontendRequest(owner.signal, signal => {
    calls++;
    if (calls === 1) { firstSignal = signal; return untilAbort(signal); }
    return Promise.resolve({ accepted: true });
  }, 15));
  const source = timeoutMs => prepareMessageSource({ clientId: 'client', sessionId: 'chat',
    rendered: () => ({ clientId: 'client', sessionId: 'chat' }), current: () => true,
    report: () => reporter.report(true), timeoutMs });
  // The user's source wait can end before transport cancellation completes.
  await assert.rejects(source(5), error => error.code === 'frontend_unavailable');
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(firstSignal.aborted, true);
  assert.equal(await source(100), 'client');
  assert.equal(calls, 2);
  reporter.stop();
});

test('a non-cooperating transport cannot keep the reporter occupied after it has been aborted', async () => {
  let transportSignal;
  await assert.rejects(frontendRequest(new AbortController().signal, signal => {
    transportSignal = signal;
    return new Promise(() => {});
  }, 10), aborted);
  assert.equal(transportSignal.aborted, true);
});
