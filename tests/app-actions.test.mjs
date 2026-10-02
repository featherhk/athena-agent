import test from 'node:test';
import assert from 'node:assert/strict';
import { consumeAppAction } from '../src/app-actions.ts';

const action = { id: 'request', session_id: 'source', action: 'navigate', status: 'pending', payload: { session_id: 'target' } };

test('one browser executes a durable action even if several receive it', async () => {
  let claimed = false, effects = 0, receipts = 0;
  const transport = async (path) => {
    if (path.endsWith('/claim')) { const accepted = !claimed; claimed = true; return { accepted, request: action }; }
    receipts++; return { completed: true };
  };
  const results = await Promise.all(['one', 'two'].map(client => consumeAppAction(action, client, transport,
    async () => { effects++; return { status: 'applied' }; }, () => true)));
  assert.equal(effects, 1); assert.equal(receipts, 1); assert.equal(results.filter(Boolean).length, 1);
});

test('a lost acknowledgement retries only the receipt, never the action', async () => {
  let effects = 0, receipts = 0;
  await consumeAppAction(action, 'one', async path => {
    if (path.endsWith('/claim')) return { accepted: true, request: action };
    if (++receipts === 1) throw new Error('disconnect');
    return { completed: true };
  }, async () => { effects++; return { status: 'applied' }; }, () => true);
  assert.equal(effects, 1); assert.equal(receipts, 2);
});

test('switching account or conversation during claim dismisses without applying stale UI', async () => {
  let current = true, receipt;
  const applied = await consumeAppAction(action, 'one', async (path, body) => {
    if (path.endsWith('/claim')) { current = false; return { accepted: true, request: action }; }
    receipt = body; return {};
  }, async () => { assert.fail('stale action executed'); }, () => current);
  assert.equal(applied, false); assert.equal(receipt.status, 'dismissed'); assert.equal(receipt.error_code, 'interrupted');
});

test('a picker requiring a user gesture remains an explicit failed receipt', async () => {
  let receipt;
  const applied = await consumeAppAction(action, 'one', async (path, body) => {
    if (path.endsWith('/claim')) return { accepted: true, request: action };
    receipt = body; return {};
  }, async () => ({ status: 'failed', error_code: 'user_gesture_required' }), () => true);
  assert.equal(applied, false); assert.equal(receipt.error_code, 'user_gesture_required');
});

test('navigation away and back invalidates an old generation before any UI write', async () => {
  const { scopeGuard } = await import('../src/app-actions.ts');
  let scope = { owner: 'alice', session: 'source', generation: 1, control: 0 };
  const guard = scopeGuard(() => scope);
  let effects = 0, receipt;
  await consumeAppAction(action, 'one', async (path, body) => {
    if (path.endsWith('/claim')) return { accepted: true, request: action };
    receipt = body; return { completed: false };
  }, async (_, isCurrent) => {
    await Promise.resolve();
    scope = { ...scope, session: 'other', generation: 2 };
    scope = { ...scope, session: 'source', generation: 3 };
    if (!isCurrent()) return { status: 'dismissed', error_code: 'interrupted' };
    effects++; return { status: 'applied' };
  }, guard);
  assert.equal(effects, 0); assert.equal(receipt.status, 'dismissed');
});

test('stop/reset invalidates a pending async effect even in the same account and session', async () => {
  const { scopeGuard } = await import('../src/app-actions.ts');
  for (const operation of ['stop', 'reset']) {
    const scope = { owner: 'alice', session: 'source', generation: 1, control: 0 };
    const guard = scopeGuard(() => scope);
    let effects = 0, receipt;
    const applied = await consumeAppAction(action, 'one', async (path, body) => {
      if (path.endsWith('/claim')) return { accepted: true, request: action };
      receipt = body; return { completed: false };
    }, async (_, isCurrent) => {
      await Promise.resolve(); scope.control++;
      if (!isCurrent()) return { status: 'dismissed', error_code: 'interrupted' };
      effects++; return { status: 'applied' };
    }, guard);
    assert.equal(effects, 0, operation); assert.equal(applied, false, operation); assert.equal(receipt.status, 'dismissed', operation);
  }
});

test('uncertain sign-out reconciles authentication without replaying ACK or the action', async () => {
  const logout = { ...action, action: 'sign_out', payload: {} };
  let effects = 0, receipts = 0, checks = 0;
  const applied = await consumeAppAction(logout, 'one', async path => {
    if (path.endsWith('/claim')) return { accepted: true, request: logout };
    if (path.endsWith('/ack')) { receipts++; throw Error('response lost after cookie revoked'); }
    assert.equal(path, '/auth/me'); checks++; return { user: null };
  }, async () => { effects++; return { status: 'applied' }; }, () => true);
  assert.equal(applied, true); assert.equal(effects, 1); assert.equal(receipts, 1); assert.equal(checks, 1);
});

test('uncertain sign-out must not clear an account that remains authenticated', async () => {
  const logout = { ...action, action: 'sign_out', payload: {} };
  const applied = await consumeAppAction(logout, 'one', async path => {
    if (path.endsWith('/claim')) return { accepted: true, request: logout };
    if (path.endsWith('/ack')) throw Error('request lost before server');
    return { user: { id: 'alice' } };
  }, async () => ({ status: 'applied' }), () => true);
  assert.equal(applied, false);
});

test('auth reconciliation ignores a stale result after a fresh login to the same account', async () => {
  const { scopeGuard } = await import('../src/app-actions.ts');
  let scope = { owner: 'alice', session: 'source', generation: 1, control: 0 };
  const guard = scopeGuard(() => scope), logout = { ...action, action: 'sign_out', payload: {} };
  const applied = await consumeAppAction(logout, 'one', async path => {
    if (path.endsWith('/claim')) return { accepted: true, request: logout };
    if (path.endsWith('/ack')) throw Error('unknown ACK');
    scope = { ...scope, generation: 2 };
    return { user: null };
  }, async () => ({ status: 'applied' }), guard);
  assert.equal(applied, false);
});

test('a tab hidden before execution never claims or executes an action', async () => {
  let calls = 0;
  const applied = await consumeAppAction(action, 'one', async () => { calls++; throw Error('unexpected'); }, async () => { calls++; throw Error('unexpected'); }, () => false);
  assert.equal(applied, false); assert.equal(calls, 0);
});

test('logout can reconcile after a visibility/session change without mistaking it for a new login', async () => {
  const logout = { ...action, action: 'sign_out', payload: {} };
  let current = true;
  const applied = await consumeAppAction(logout, 'one', async path => {
    if (path.endsWith('/claim')) return { accepted: true, request: logout };
    if (path.endsWith('/ack')) { current = false; throw Error('reply lost'); }
    return { user: null };
  }, async () => ({ status: 'applied' }), () => current, () => true);
  assert.equal(applied, true);
});
