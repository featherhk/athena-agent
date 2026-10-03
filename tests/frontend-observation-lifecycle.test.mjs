import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { FrontendReporter } from '../src/frontend-observation.ts';
import { frontendRequest } from '../src/frontend-request.ts';
import { prepareMessageSource } from '../src/frontend-submission.ts';

// Exercise the real effect and transport lifecycle. Layout and React commit timing
// are accepted separately in the browser, not simulated as successful here.
const compiled = ts.transpileModule(readFileSync(new URL('../src/useFrontendObservation.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function fixture() {
  const references = [], effects = [], cleanups = [], requests = [], sources = [], timers = new Map(), intervals = new Map();
  let refIndex = 0, effectIndex = 0, timerId = 0, status = 'accepted', revoked = 0, identityLost = 0, authOwner = 'owner', authFailure = false, authChecks = 0;
  const document = Object.assign(new EventTarget(), { hidden: false, querySelector: () => null });
  const window = Object.assign(new EventTarget(), { visualViewport: null });
  let state = { session_id: 's1', foreground: true, viewport: { width: 1000, height: 700 }, open_views: [], preview: null,
    workspace: null, components: [], waiting: [], errors: [], has_unsent_changes: false, attachment_ids: [] };
  const received = [], module = { exports: {} };
  class Source extends EventTarget {
    static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
    constructor(url) { super(); this.url = url; this.closed = false; this.readyState = Source.CONNECTING; sources.push(this); }
    close() { this.closed = true; this.readyState = Source.CLOSED; }
    open() { this.readyState = Source.OPEN; this.onopen?.(); }
    error(state = Source.CLOSED) { this.readyState = state; this.onerror?.(); }
    action(client) { this.onmessage?.({ data: JSON.stringify({ type: 'app_action', payload: { id: 'a', target_client_id: client } }) }); }
  }
  const hooks = {
    useRef(value) { const index = refIndex++; return references[index] ||= { current: value }; },
    useCallback(callback) { return callback; },
    useEffect(callback, dependencies) {
      const index = effectIndex++, previous = effects[index];
      if (dependencies && previous && dependencies.every((value, i) => value === previous[i])) return;
      cleanups[index]?.(); effects[index] = dependencies; cleanups[index] = callback();
    },
  };
  hooks.useLayoutEffect = hooks.useEffect;
  vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`, {
    document, window, AbortController, EventSource: Source,
    MutationObserver: class { observe() {} disconnect() {} }, Element: class {},
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; }, clearTimeout: id => timers.delete(id),
    setInterval: callback => { intervals.set(++timerId, callback); return timerId; }, clearInterval: id => intervals.delete(id),
    fetch: async (url, init) => {
      if (url === '/api/auth/me') { authChecks++; if (authFailure) throw Error('offline'); return { ok: true, json: async () => ({ user: authOwner ? { id: authOwner } : null }) }; }
      requests.push({ url, body: JSON.parse(init.body), signal: init.signal });
      return { ok: status === 'accepted', json: async () => status === 'accepted' ? { accepted: true } : { code: status } };
    },
  })(name => name === 'react' ? hooks : { FrontendReporter, frontendRequest, prepareMessageSource, observeFrontend: (_, facts) => ({ ...state, foreground: !document.hidden, errors: facts.reconnecting ? [{ source: 'connection', code: 'connection_lost' }] : state.errors }) }, module, module.exports);
  const render = (owner = 'owner', client = 'client1') => {
    refIndex = 0; effectIndex = 0;
    return module.exports.useFrontendObservation(owner, client, {}, action => received.push(action), () => { revoked++; }, () => { identityLost++; });
  };
  const tick = async (delay) => { const pending = [...timers.entries()].filter(([, timer]) => delay === undefined || timer.delay === delay); pending.forEach(([id, timer]) => { timers.delete(id); timer.callback(); }); await flush(); };
  return { render, document, window, requests, sources, received, intervals, tick,
    state: change => { state = { ...state, ...change }; }, status: value => { status = value; }, revoked: () => revoked,
    authOwner: value => { authOwner = value; }, authFailure: value => { authFailure = value; }, authChecks: () => authChecks, identityLost: () => identityLost,
    timerDelays: () => [...timers.values()].map(timer => timer.delay),
    cleanup: () => cleanups.forEach(cleanup => cleanup?.()) };
}

test('the client event stream starts only after successful registration and rejects other targets', async () => {
  const f = fixture(); f.render();
  assert.equal(f.sources.length, 0);
  await flush(); assert.equal(f.sources.length, 1);
  f.sources[0].action('other'); assert.equal(f.received.length, 0);
  f.sources[0].action('client1'); assert.equal(f.received.length, 1);
  f.cleanup(); f.sources[0].action('client1'); assert.equal(f.received.length, 1);
});

test('hiding a tab immediately reports background, closes actions, and pauses heartbeat', async () => {
  const f = fixture(); f.render(); await flush();
  f.document.hidden = true; f.document.dispatchEvent(new Event('visibilitychange')); await flush();
  assert.equal(f.requests.at(-1).body.foreground, false); assert.equal(f.sources[0].closed, true);
  const count = f.requests.length; f.intervals.forEach(callback => callback()); await flush();
  assert.equal(f.requests.length, count);
  f.document.hidden = false; f.document.dispatchEvent(new Event('visibilitychange')); await flush();
  assert.equal(f.requests.at(-1).body.foreground, true); assert.equal(f.sources.length, 2);
  f.cleanup();
});

test('page departure reports unavailable and return restores the same client identity', async () => {
  const f = fixture(); f.render(); await flush();
  f.window.dispatchEvent(new Event('pagehide')); await flush();
  assert.equal(f.requests.at(-1).body.foreground, false);
  f.window.dispatchEvent(new Event('pageshow')); await flush();
  assert.equal(f.requests.at(-1).body.foreground, true);
  assert.ok(f.requests.every(item => item.body.client_id === 'client1'));
  f.cleanup();
});

test('reset revocation rotates once; ordinary connectivity errors cannot churn identity', async () => {
  const f = fixture(); const observation = f.render(); await flush();
  f.status('frontend_unavailable'); await assert.rejects(observation.report()); assert.equal(f.revoked(), 0);
  f.status('frontend_identity_revoked'); await assert.rejects(observation.report()); assert.equal(f.revoked(), 1);
  assert.equal(await observation.report(), null); assert.equal(f.revoked(), 1);
  assert.equal(f.sources[0].closed, true); f.cleanup();
});

test('logout aborts outstanding identity work and cannot receive or upload late state', async () => {
  const f = fixture(); const observation = f.render(); await flush();
  f.render(null); const count = f.requests.length;
  assert.equal(f.sources[0].closed, true);
  assert.equal(await observation.report(), null); await f.tick();
  assert.equal(f.requests.length, count); f.sources[0].action('client1'); assert.equal(f.received.length, 0);
  f.cleanup();
});

test('same-account cookie renewal verifies the owner before requesting one fresh client identity', async () => {
  const f = fixture(); const observation = f.render(); await flush();
  f.status('frontend_identity_changed'); await assert.rejects(observation.report());
  assert.equal(f.authChecks(), 1); assert.equal(f.revoked(), 1); assert.equal(f.identityLost(), 0);
  assert.equal(await observation.report(), null); assert.equal(f.revoked(), 1); f.cleanup();
});

test('a cookie belonging to another account or signed-out user expires the old tab instead of rebinding it', async () => {
  for (const owner of ['other-owner', null]) {
    const f = fixture(); const observation = f.render(); await flush();
    f.status('frontend_identity_changed'); f.authOwner(owner);
    await assert.rejects(observation.report());
    assert.equal(f.revoked(), 0); assert.equal(f.identityLost(), 1); assert.equal(f.sources[0].closed, true);
    assert.equal(await observation.report(), null); f.cleanup();
  }
});

test('a failed identity lookup leaves renewal retryable without silently changing accounts', async () => {
  const f = fixture(); const observation = f.render(); await flush();
  f.status('frontend_identity_changed'); f.authFailure(true);
  await assert.rejects(observation.report()); assert.equal(f.revoked(), 0); assert.equal(f.identityLost(), 0);
  f.authFailure(false); await assert.rejects(observation.report());
  assert.equal(f.authChecks(), 2); assert.equal(f.revoked(), 1); f.cleanup();
});

test('streaming renders do not starve semantic changes or create reports for unchanged text', async () => {
  const f = fixture(); f.render(); await flush();
  const count = f.requests.length;
  for (let i = 0; i < 100; i++) f.render();
  await f.tick(); assert.equal(f.requests.length, count);
  f.state({ open_views: ['history'] });
  for (let i = 0; i < 100; i++) f.render();
  await f.tick(); assert.equal(f.requests.length, count + 1);
  assert.deepEqual(f.requests.at(-1).body.open_views, ['history']); f.cleanup();
});

test('a deployment 502 closes SSE; a fresh report after backoff reconnects and receives the pending action', async () => {
  const f = fixture(); f.render(); await flush();
  f.sources[0].open(); f.sources[0].error();
  assert.equal(f.sources[0].closed, true); assert.ok(f.timerDelays().includes(5000));
  await f.tick(200);
  assert.equal(f.sources.length, 1); assert.equal(f.requests.at(-1).body.errors[0].code, 'connection_lost');
  const reportsBeforeRetry = f.requests.length;
  await f.tick(5000);
  assert.ok(f.requests.length > reportsBeforeRetry); assert.equal(f.sources.length, 2);
  f.sources[1].open(); f.sources[1].action('client1'); assert.equal(f.received.length, 1);
  f.sources[0].action('client1'); assert.equal(f.received.length, 1);
  await f.tick(200); assert.deepEqual(f.requests.at(-1).body.errors, []);
  assert.equal(f.revoked(), 0); f.cleanup();
});

test('CONNECTING remains native browser retry and does not spawn competing streams', async () => {
  const f = fixture(); f.render(); await flush();
  f.sources[0].error(0); await f.tick(200);
  f.window.dispatchEvent(new Event('online')); await flush();
  f.intervals.forEach(callback => callback()); await flush();
  assert.equal(f.sources.length, 1); assert.equal(f.sources[0].closed, false);
  assert.ok(!f.timerDelays().some(delay => delay >= 5000)); f.cleanup();
});

test('repeated terminal failures use bounded backoff; online cannot bypass it or change identity', async () => {
  const f = fixture(); f.render(); await flush();
  for (const delay of [5000, 10000, 20000, 30000, 30000]) {
    f.sources.at(-1).error();
    const count = f.sources.length;
    f.window.dispatchEvent(new Event('online')); await flush(); await f.tick(200);
    assert.equal(f.sources.length, count); assert.ok(f.timerDelays().includes(delay));
    await f.tick(delay); assert.equal(f.sources.length, count + 1);
  }
  assert.equal(f.revoked(), 0); f.cleanup();
});

test('heartbeat discovers a missed CLOSED event; failed refresh retries and background cancels retry', async () => {
  const f = fixture(); f.render(); await flush();
  f.sources[0].readyState = 2;
  f.intervals.forEach(callback => callback()); await flush();
  assert.equal(f.sources[0].closed, true); assert.ok(f.timerDelays().includes(5000));
  f.status('frontend_unavailable'); await f.tick(5000);
  assert.equal(f.sources.length, 1); assert.ok(f.timerDelays().includes(10000));
  f.document.hidden = true; f.document.dispatchEvent(new Event('visibilitychange')); await flush();
  assert.ok(!f.timerDelays().some(delay => delay >= 5000));
  f.status('accepted'); await f.tick(); assert.equal(f.sources.length, 1);
  f.document.hidden = false; f.document.dispatchEvent(new Event('visibilitychange')); await flush();
  assert.ok(f.timerDelays().includes(20000)); await f.tick(20000); assert.equal(f.sources.length, 2);
  f.sources[1].error(); f.cleanup(); assert.ok(!f.timerDelays().some(delay => delay >= 5000));
});
