import assert from 'node:assert/strict';
import test from 'node:test';
import { SessionSelection } from '../src/session-selection.ts';

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { values, getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
const key = owner => `athena-session:${owner}`;

test('a legacy shared conversation is migrated once and survives reload independently', () => {
  const tab = storage(), shared = storage({ [key('alice')]: 'legacy' });
  const first = new SessionSelection(tab, shared);
  assert.equal(first.read('alice'), 'legacy');
  assert.equal(tab.getItem(key('alice')), 'legacy');
  shared.setItem(key('alice'), 'other-window');
  assert.equal(first.read('alice'), 'legacy');
  assert.equal(new SessionSelection(tab, shared).read('alice'), 'legacy');
});

test('two windows keep their own conversations while a new tab inherits the latest shared starting point', () => {
  const shared = storage(), tabA = storage(), tabB = storage();
  const windowA = new SessionSelection(tabA, shared), windowB = new SessionSelection(tabB, shared);
  windowA.write('alice', 'conversation-a');
  assert.equal(windowB.read('alice'), 'conversation-a');
  windowB.write('alice', 'conversation-b');
  assert.equal(new SessionSelection(tabA, shared).read('alice'), 'conversation-a');
  assert.equal(new SessionSelection(tabB, shared).read('alice'), 'conversation-b');
  assert.equal(new SessionSelection(storage(), shared).read('alice'), 'conversation-b');
  windowA.write('alice', 'conversation-c');
  assert.equal(windowB.read('alice'), 'conversation-b');
});

test('a deliberately blank conversation remains blank after another window moves and this tab reloads', () => {
  const tab = storage(), shared = storage();
  const first = new SessionSelection(tab, shared);
  first.write('alice', 'old-conversation');
  first.write('alice', null);
  assert.equal(tab.getItem(key('alice')), '');
  assert.equal(shared.getItem(key('alice')), null);
  new SessionSelection(storage(), shared).write('alice', 'other-window');
  assert.equal(new SessionSelection(tab, shared).read('alice'), null);
  assert.equal(new SessionSelection(storage(), shared).read('alice'), 'other-window');
});

test('accounts are isolated and a signed-out read or write never changes a saved account selection', () => {
  const tab = storage(), shared = storage(), selection = new SessionSelection(tab, shared);
  selection.write('alice', 'alice-session');
  selection.write('bob', 'bob-session');
  assert.equal(selection.read(null), null);
  assert.equal(selection.write(null, 'guest-session'), null);
  assert.equal(selection.read('alice'), 'alice-session');
  assert.equal(selection.read('bob'), 'bob-session');
  assert.equal(selection.read('new-owner'), null);
  assert.deepEqual([...tab.values.keys()].sort(), [key('alice'), key('bob'), key('new-owner')].sort());
  const reload = new SessionSelection(tab, shared);
  assert.equal(reload.read('bob'), 'bob-session');
  assert.equal(reload.read('alice'), 'alice-session');
});

test('an invalid tab selection resets locally and never falls through to another window', () => {
  for (const invalid of ['../other', 'https://example.com', 'with spaces', 'x'.repeat(65)]) {
    const tab = storage({ [key('alice')]: invalid }), shared = storage({ [key('alice')]: 'other-window' });
    assert.equal(new SessionSelection(tab, shared).read('alice'), null);
    assert.equal(tab.getItem(key('alice')), '');
    assert.equal(shared.getItem(key('alice')), 'other-window');
  }
});

test('invalid shared values and invalid writes do not become navigable session identifiers', () => {
  const tab = storage(), shared = storage({ [key('alice')]: '{"id":"unsafe"}' });
  const selection = new SessionSelection(tab, shared);
  assert.equal(selection.read('alice'), null);
  assert.equal(tab.getItem(key('alice')), '');
  selection.write('alice', 'valid-session');
  assert.equal(selection.write('alice', '/api/auth/logout'), null);
  assert.equal(selection.read('alice'), null);
  assert.equal(new SessionSelection(tab, shared).read('alice'), null);
  const before = [...tab.values];
  for (const owner of ['', '../owner', 'x'.repeat(65)]) {
    assert.equal(selection.read(owner), null);
    assert.equal(selection.write(owner, 'session'), null);
  }
  assert.deepEqual([...tab.values], before);
});

test('blocked tab storage falls back only once per owner and keeps subsequent selection in memory', () => {
  const blocked = { getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); }, removeItem() { throw Error('blocked'); } };
  const shared = storage({ [key('alice')]: 'initial' }), selection = new SessionSelection(blocked, shared);
  assert.equal(selection.read('alice'), 'initial');
  shared.setItem(key('alice'), 'other-window');
  assert.equal(selection.read('alice'), 'initial');
  selection.write('alice', 'current');
  shared.setItem(key('alice'), 'other-window-again');
  assert.equal(selection.read('alice'), 'current');
  selection.write('alice', null);
  assert.equal(selection.read('alice'), null);
  const memory = new SessionSelection(blocked, blocked);
  assert.equal(memory.read('alice'), null);
  memory.write('alice', 'memory-only');
  assert.equal(memory.read('alice'), 'memory-only');
  assert.equal(memory.read('bob'), null);
});

test('a failed shared storage operation does not prevent the tab from restoring across reload', () => {
  const shared = { getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }, removeItem() { throw Error('blocked'); } };
  const tab = storage(), selection = new SessionSelection(tab, shared);
  selection.write('alice', 'retained');
  assert.equal(new SessionSelection(tab, shared).read('alice'), 'retained');
  selection.write('alice', null);
  assert.equal(new SessionSelection(tab, shared).read('alice'), null);
  const memory = new SessionSelection();
  memory.write('alice', 'no-storage');
  assert.equal(memory.read('alice'), 'no-storage');
});
