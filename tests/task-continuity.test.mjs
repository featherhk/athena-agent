import assert from 'node:assert/strict';
import test from 'node:test';
import { ActivityFeed, activeActivity, activityGroups, readableActivity } from '../src/task-continuity.ts';

const flush = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };
const item = (change = {}) => ({ run_id: 'r1', session_id: 's1', title: 'Task one', status: 'complete', phase: 'idle', attention_key: 'key1', created_at: '2026-10-04', updated_at: '2026-10-04', needs_user: false, ...change });
function fixture(owner = 'david') {
  const requests = [], changes = [], timers = new Map(); let timer = 0, lost = 0;
  const clock = { later: (callback, delay) => { timers.set(++timer, { callback, delay }); return timer; }, cancel: id => timers.delete(id) };
  const feed = new ActivityFeed(owner, (path, signal, body) => new Promise((resolve, reject) => requests.push({ path, signal, body, resolve, reject })), state => changes.push(state), () => lost++, clock);
  const answer = async (index, items = [item()], extras = {}) => { requests[index].resolve({ owner_id: owner, items, has_more: false, ...extras }); await flush(); };
  return { feed, requests, changes, timers, answer, lost: () => lost,
    tick: async delay => { for (const [id, task] of [...timers]) if (task.delay === delay) { timers.delete(id); task.callback(); } await flush(); } };
}

test('polls only while visible and coalesces overlapping refresh requests', async () => {
  const f = fixture(); f.feed.refresh(); assert.equal(f.requests.length, 0);
  f.feed.resume(); f.feed.refresh(); f.feed.refresh(); assert.equal(f.requests.length, 1);
  await f.answer(0); assert.equal(f.requests.length, 2);
  await f.answer(1); assert.deepEqual([...f.timers.values()].map(timer => timer.delay), [5000]);
  await f.tick(5000); assert.equal(f.requests.length, 3);
  f.feed.pause(); assert.equal(f.requests[2].signal.aborted, true);
  await f.answer(2, [item({ title: 'late hidden answer' })]);
  assert.equal(f.feed.state.items[0].title, 'Task one');
  await f.tick(5000); assert.equal(f.requests.length, 3);
  f.feed.resume(); assert.equal(f.requests.length, 4); f.feed.stop();
});

test('owner changes abort old requests and cannot reveal another account or log it out late', async () => {
  const f = fixture(); f.feed.resume(); f.feed.stop();
  await f.answer(0, [item({ title: 'private' })], { owner_id: 'wendy' });
  assert.equal(f.feed.state.items.length, 0); assert.equal(f.lost(), 0); assert.equal(f.requests[0].signal.aborted, true);
  const other = fixture(); other.feed.resume(); await other.answer(0); other.feed.refresh();
  await other.answer(1, [item({ title: 'foreign' })], { owner_id: 'wendy' });
  assert.equal(other.feed.state.items.length, 0); assert.equal(other.lost(), 1);
  other.feed.resume(); assert.equal(other.requests.length, 2);
});

test('outages and malformed responses preserve the last feed and remain retryable', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0);
  f.feed.refresh(); f.requests[1].reject(new Error('offline')); await flush();
  assert.equal(f.feed.state.error, true); assert.equal(f.feed.state.items[0].run_id, 'r1');
  await f.tick(5000); f.requests[2].resolve({ items: [], has_more: false }); await flush();
  assert.equal(f.feed.state.error, true); assert.equal(f.feed.state.items.length, 1); assert.equal(f.lost(), 0);
  f.feed.refresh(); await f.answer(3, []); assert.equal(f.feed.state.error, false); assert.equal(f.feed.state.items.length, 0); f.feed.stop();
});

test('a late acknowledgement cannot remove a newer request from the same run', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0);
  const ack = f.feed.acknowledge(item()); f.feed.refresh();
  const newer = item({ status: 'waiting_user', needs_user: true, attention_key: 'key2' });
  await f.answer(2, [newer]);
  f.requests[1].resolve({ owner_id: 'david', acknowledged: true, item: null }); await ack;
  assert.equal(f.feed.state.items[0].attention_key, 'key2'); assert.equal(f.feed.state.items[0].needs_user, true); f.feed.stop();
});

test('a GET started before acknowledgement cannot resurrect a dismissed completion', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0); f.feed.refresh();
  const ack = f.feed.acknowledge(item());
  f.requests[2].resolve({ owner_id: 'david', acknowledged: true, item: null }); await ack;
  assert.equal(f.feed.state.items.length, 0);
  await f.answer(1); assert.equal(f.feed.state.items.length, 0); assert.equal(f.requests.length, 4);
  await f.answer(3, []); assert.equal(f.feed.state.error, false); f.feed.stop();
});

test('stale acknowledgement retains current state, duplicate read receipts coalesce, logout aborts', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0);
  const first = f.feed.acknowledge(item()), second = f.feed.acknowledge(item());
  assert.equal(f.requests.length, 2); assert.equal(await second, false);
  const current = item({ attention_key: 'key2', status: 'interrupted' });
  f.requests[1].resolve({ owner_id: 'david', acknowledged: false, item: current });
  assert.equal(await first, false); assert.equal(f.feed.state.items[0].attention_key, 'key2');
  const third = f.feed.acknowledge(current); f.feed.stop();
  f.requests[3].resolve({ owner_id: 'david', acknowledged: true, item: null });
  assert.equal(await third, false); assert.equal(f.feed.state.items[0].attention_key, 'key2');
  assert.ok(f.requests.slice(2).every(request => request.signal.aborted));
});

test('stop is scoped to the chosen conversation and success requires an actual receipt', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0);
  const first = f.feed.stopSession('s2'); assert.equal(f.requests[1].path, '/sessions/s2/stop');
  f.requests[1].resolve({}); assert.equal(await first, false); assert.equal(f.feed.state.error, true);
  await f.answer(2); const next = f.feed.stopSession('s2'); f.requests[3].resolve({ ok: true });
  assert.equal(await next, true); f.feed.stop();
});

test('load more grows a bounded feed and does not overlap an outstanding refresh', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0, [item()], { has_more: true });
  f.feed.loadMore(); assert.equal(f.requests[1].path, '/activity?limit=60');
  f.feed.loadMore(); f.feed.loadMore(); f.feed.loadMore(); assert.equal(f.requests.length, 2);
  await f.answer(1); assert.equal(f.requests[2].path, '/activity?limit=100'); assert.equal(f.feed.state.limit, 100); f.feed.stop();
});

test('one conversation groups queue entries and delegated help outranks ready or working', () => {
  const rows = [item(), item({ run_id: 'r2', status: 'running' }), item({ run_id: 'r3', session_id: 's3', status: 'waiting_agents', needs_user: true }), item({ run_id: 'r4', session_id: 's2', status: 'failed' })];
  const grouped = activityGroups(rows, 's2'); assert.equal(grouped.length, 2); assert.equal(grouped[0][0].run_id, 'r3'); assert.equal(grouped[1].length, 2);
  assert.equal(activeActivity(grouped[0][0]), true); assert.equal(activityGroups(rows, 's1').length, 2);
});

test('only a visible matching terminal result is read, not polling or an obscured/current active chat', () => {
  const result = item(); const runs = [{ id: 'r1', status: 'complete' }];
  assert.equal(readableActivity(result, 's1', runs, true), true);
  assert.equal(readableActivity(result, 's1', runs, false), false);
  assert.equal(readableActivity(result, 's2', runs, true), false);
  assert.equal(readableActivity(result, 's1', [], true), false);
  assert.equal(readableActivity(result, 's1', [{ id: 'r1', status: 'running' }], true), false);
  assert.equal(readableActivity(item({ status: 'waiting_user', needs_user: true }), 's1', [{ id: 'r1', status: 'waiting_user' }], true), false);
});

test('a current conversation may expose unread results while scrolled away without duplicating its active status', () => {
  const rows = [item(), item({ run_id: 'r2', status: 'running' }), item({ run_id: 'r3', status: 'waiting_user', needs_user: true })];
  assert.deepEqual(activityGroups(rows, 's1'), []);
  const groups = activityGroups(rows, 's1', true);
  assert.equal(groups.length, 1); assert.deepEqual(groups[0].map(row => row.run_id), ['r1']);
});

test('a GET begun during ACK cannot resurrect a result when its stale DB read arrives after the ACK', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0);
  const ack = f.feed.acknowledge(item()); f.feed.refresh();
  assert.equal(f.requests[2].path, '/activity?limit=30');
  f.requests[1].resolve({ owner_id: 'david', acknowledged: true, item: null }); await ack;
  assert.equal(f.feed.state.items.length, 0);
  await f.answer(2); assert.equal(f.feed.state.items.length, 0); assert.equal(f.requests.length, 4);
  await f.answer(3, []); assert.equal(f.feed.state.items.length, 0); f.feed.stop();
});

test('failed ACK settlement also invalidates an overlapping GET until the outcome is reconciled', async () => {
  const f = fixture(); f.feed.resume(); await f.answer(0);
  const ack = f.feed.acknowledge(item()); f.feed.refresh();
  f.requests[1].reject(new Error('receipt lost')); assert.equal(await ack, false);
  assert.equal(f.feed.state.error, true);
  await f.answer(2); assert.equal(f.feed.state.error, true); assert.equal(f.requests.length, 4);
  await f.answer(3, []); assert.equal(f.feed.state.error, false); assert.equal(f.feed.state.items.length, 0); f.feed.stop();
});
