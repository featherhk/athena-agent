import test from 'node:test';
import assert from 'node:assert/strict';
import { DraftStore } from '../src/draft-store.ts';

const file = id => ({ id, session_id: 'one', name: `${id}.md`, size: 10, content_url: `/api/files/${id}/content` });
function storage() {
  const items = new Map();
  return { getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, value), removeItem: key => items.delete(key) };
}

test('conversation and account switches restore only their own text and attachments', () => {
  const store = new DraftStore(storage());
  store.write('alice', 'one', { text: 'First unsent message', attached: [file('a')] });
  store.write('alice', 'two', { text: 'Second conversation', attached: [file('b')] });
  assert.deepEqual(store.read('bob', 'one').attached, []);
  assert.equal(store.read('bob', 'one').text, '');
  assert.equal(store.read(null, null).text, '');
  assert.equal(store.read('alice', 'one').text, 'First unsent message');
  assert.deepEqual(store.read('alice', 'one').attached.map(item => item.id), ['a']);
  assert.deepEqual(store.read('alice', 'two').attached.map(item => item.id), ['b']);
});

test('assigning a real session preserves a new draft and does not copy it to another new conversation', () => {
  const store = new DraftStore(storage());
  store.write('alice', null, { text: 'Please examine this', attached: [file('a')] });
  const sent = store.move('alice', null, 'created');
  assert.equal(store.read('alice', null).text, '');
  assert.equal(sent.text, 'Please examine this');
  assert.deepEqual(sent.attached.map(item => item.id), ['a']);
  store.submitted('alice', 'created', sent);
  assert.equal(store.read('alice', 'created').text, '');
  assert.deepEqual(store.read('alice', 'created').attached, []);
});

test('a delayed send clears its original draft while retaining newly typed text and attachments', () => {
  const store = new DraftStore(storage());
  const sent = store.write('alice', 'one', { text: 'Send this', attached: [file('sent')] });
  store.write('alice', 'two', { text: 'Untouched other conversation', attached: [] });
  store.write('alice', 'one', { text: 'My next question', attached: [file('sent'), file('new')] });
  store.submitted('alice', 'one', sent);
  assert.equal(store.read('alice', 'one').text, 'My next question');
  assert.deepEqual(store.read('alice', 'one').attached.map(item => item.id), ['new']);
  assert.equal(store.read('alice', 'two').text, 'Untouched other conversation');
});

test('a reload restores account-scoped draft including attachment manifests', () => {
  const disk = storage(), first = new DraftStore(disk);
  first.write('alice', 'one', { text: 'Not lost during a client upgrade', attached: [file('a')] });
  assert.equal(first.persist('alice', 'one'), true);
  const reloaded = new DraftStore(disk);
  assert.equal(reloaded.read('alice', 'one').text, 'Not lost during a client upgrade');
  assert.deepEqual(reloaded.read('alice', 'one').attached.map(item => item.id), ['a']);
  assert.equal(reloaded.read('bob', 'one').text, '');
});

test('private/quota-limited storage retains navigation drafts but refuses unsafe upgrade', () => {
  const store = new DraftStore({ getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); }, removeItem() { throw Error('blocked'); } });
  store.write('alice', null, { text: 'Keep locally', attached: [file('a')] });
  assert.equal(store.persist('alice', null), false);
  assert.equal(store.move('alice', null, 'created').text, 'Keep locally');
  assert.equal(store.read('alice', null).text, '');
  assert.equal(new DraftStore().persist('alice', null), false);
});
