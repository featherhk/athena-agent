import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeSnapshot } from '../src/snapshot-sync.ts';

const snapshot = (cursor, messages = [], components = []) => ({ cursor, messages, components, runs: [], tasks: [], files: [] });
const message = (id, content, status = 'streaming') => ({ id, role: 'assistant', content, status });

test('new component survives a snapshot racing a newer streamed text event', () => {
  const current = snapshot(11, [message('reply', 'newer stream')]);
  const incoming = snapshot(10, [message('reply', 'older prefix')], [{ id: 'weather', version: 1 }]);
  const result = mergeSnapshot(current, incoming, new Map([['reply', 11]]), 10);
  assert.equal(result.messages[0].content, 'newer stream');
  assert.equal(result.components[0].id, 'weather');
  assert.equal(result.cursor, 11);
  assert.equal(current.components.length, 0);
  assert.equal(incoming.messages[0].content, 'older prefix');
});

test('continuing message events do not make structural hydration spin or discard', () => {
  const incoming = snapshot(10, [message('reply', 'prefix')], [{ id: 'weather', version: 1 }]);
  for (let seq = 11; seq <= 100; seq++) {
    const current = snapshot(seq, [message('reply', `prefix ${seq}`)]);
    const result = mergeSnapshot(current, incoming, new Map([['reply', seq]]), 10);
    assert.ok(result, `stream event ${seq} must not request another snapshot`);
    assert.equal(result.messages[0].content, `prefix ${seq}`);
    assert.equal(result.components[0].version, 1);
  }
});

test('retain newly streamed rows missing from an older snapshot but drop non-live stale rows', () => {
  const current = snapshot(12, [message('old', 'stale'), message('new', 'latest')]);
  const incoming = snapshot(10, [message('known', 'saved', 'complete')]);
  const result = mergeSnapshot(current, incoming, new Map([['new', 12]]), 10);
  assert.deepEqual(result.messages.map(row => row.id), ['known', 'new']);
});

test('durable completion replaces earlier streaming state and genuinely stale structure is rejected', () => {
  const current = snapshot(11, [message('reply', 'prefix')], [{ id: 'weather', version: 1 }]);
  const incoming = snapshot(12, [message('reply', 'final answer', 'complete')], [{ id: 'weather', version: 2 }]);
  const result = mergeSnapshot(current, incoming, new Map([['reply', 11]]), 12);
  assert.equal(result.messages[0].status, 'complete');
  assert.equal(result.components[0].version, 2);
  assert.equal(mergeSnapshot(current, incoming, new Map(), 13), null);
});
