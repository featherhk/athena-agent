import assert from 'node:assert/strict';
import test from 'node:test';
import { requestActivity } from '../src/task-continuity.ts';
const response = (status, value) => ({ status, ok: status >= 200 && status < 300, json: async () => value });
function fixture(auth) {
  const requests = []; let lost = 0;
  const transport = async (path, options) => { requests.push({ path, options }); return path === '/api/auth/me' ? typeof auth === 'function' ? auth() : auth : response(401, {}); };
  const controller = new AbortController();
  return { requests, controller, lost: () => lost,
    run: (body = undefined) => requestActivity('david', body ? '/activity/ack' : '/activity?limit=30', controller.signal, () => lost++, body, transport) };
}

test('same-account cookie renewal is rechecked after an old 401 without signing out or replaying the request', async () => {
  for (const body of [undefined, { run_id: 'r1', attention_key: 'key' }]) {
    const f = fixture(response(200, { user: { id: 'david' } }));
    await assert.rejects(f.run(body), /activity_reauthenticated/);
    assert.equal(f.lost(), 0); assert.equal(f.requests.length, 2);
    assert.equal(f.requests[1].path, '/api/auth/me'); assert.equal(f.requests[1].options.method, undefined);
  }
});

test('a confirmed different account or signed-out cookie invalidates the old identity', async () => {
  for (const user of [null, { id: 'wendy' }]) {
    const f = fixture(response(200, { user }));
    await assert.rejects(f.run(), /login_required/); assert.equal(f.lost(), 1);
  }
});

test('unavailable or malformed identity lookup is retryable, not evidence that the user signed out', async () => {
  for (const auth of [response(503, {}), response(200, {}), response(200, { user: {} }), response(200, null)]) {
    const f = fixture(auth); await assert.rejects(f.run(), /identity_unavailable/); assert.equal(f.lost(), 0);
  }
});

test('aborted old-account responses cannot trigger identity lookup or affect a new login', async () => {
  const f = fixture(response(200, { user: { id: 'wendy' } }));
  const pending = f.run(); f.controller.abort(); await assert.rejects(pending, /aborted/);
  assert.equal(f.lost(), 0); assert.equal(f.requests.length, 1);
});

test('logout while auth recheck is pending ignores its late result', async () => {
  let resolve; const f = fixture(() => new Promise(done => { resolve = done; }));
  const pending = f.run(); for (let n = 0; n < 5; n++) await Promise.resolve();
  f.controller.abort(); resolve(response(200, { user: null }));
  await assert.rejects(pending, /aborted/); assert.equal(f.lost(), 0);
});
