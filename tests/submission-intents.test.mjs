import test from 'node:test';
import assert from 'node:assert/strict';
import { SubmissionIntents, submissionBody, readSubmissionReceipt, readSubmissionLookup, readStopReceipt, submissionRejected } from '../src/submission-intents.ts';
import { DraftStore } from '../src/draft-store.ts';
const disk = () => {
  const map = new Map();
  return { getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) };
};
const file = id => ({ id, name: id, content_url: `/api/files/${id}/content` });
const make = overrides => ({ id: 'one', owner: 'alice', session: 'travel', text: 'Compare flights', mode: 'queue', file_ids: ['a'], revision: 3,
  draft: { text: 'Compare flights', attached: [file('a')], revision: 7 }, ...overrides });

test('reload retains a frozen intent separately from the editable draft and isolates accounts/sessions', () => {
  const storage = disk(), first = new SubmissionIntents(storage), intent = make(); first.add(intent);
  intent.text = 'Mutated caller'; intent.file_ids.push('b');
  const reloaded = new SubmissionIntents(storage), pending = reloaded.list('alice', 'travel')[0];
  assert.equal(pending.text, 'Compare flights'); assert.deepEqual(pending.file_ids, ['a']);
  assert.deepEqual(reloaded.list('bob', 'travel'), []); assert.deepEqual(reloaded.list('alice', 'other'), []);
  pending.text = 'Changed returned copy'; assert.equal(reloaded.list('alice', 'travel')[0].text, 'Compare flights');
  assert.deepEqual(submissionBody(pending, 'window'), { text: 'Changed returned copy', mode: 'queue', file_ids: ['a'], frontend_client_id: 'window', client_submission_id: 'one', expected_submission_revision: 3 });
  reloaded.remove(pending); assert.deepEqual(new SubmissionIntents(storage).list('alice', 'travel'), []);
});

test('identical text in a new deliberate draft is a new intent, while an untouched retry uses the original ID', () => {
  const store = new SubmissionIntents(), intent = make(); store.add(intent);
  assert.equal(store.forDraft('alice', 'travel', intent.draft).id, 'one');
  assert.equal(store.forDraft('alice', 'travel', { ...intent.draft, revision: 8 }), undefined);
  assert.equal(store.forDraft('alice', 'travel', { ...intent.draft, attached: [file('b')] }), undefined);
  assert.equal(store.forDraft('bob', 'travel', intent.draft), undefined);
});

test('late acceptance never removes an attachment readded while delivery was uncertain', () => {
  const storage = disk(), drafts = new DraftStore(storage), sent = drafts.write('alice', 'travel', { text: 'Original', attached: [file('a')] });
  drafts.write('alice', 'travel', { text: 'Original', attached: [] });
  const edited = drafts.write('alice', 'travel', { text: 'Original', attached: [file('a')] });
  const reloaded = new DraftStore(storage);
  assert.deepEqual(reloaded.acknowledged('alice', 'travel', sent), edited);
  assert.deepEqual(reloaded.acknowledged('alice', 'travel', edited).attached, []);
  assert.equal(reloaded.read('alice', 'travel').text, '');
});

test('monotonic revision does not regress to old snapshots, and a lost Stop requires a fresh read', () => {
  const store = new SubmissionIntents(); store.observe('alice', 'travel', 2);
  store.stopStarted('alice', 'travel'); assert.equal(store.revision('alice', 'travel'), undefined);
  assert.throws(() => store.refreshed('alice', 'travel', 2, store.epoch('alice', 'travel')));
  store.stopFinished('alice', 'travel', 4); store.observe('alice', 'travel', 1);
  assert.equal(store.revision('alice', 'travel'), 4);
  store.stopStarted('alice', 'travel'); store.stopFinished('alice', 'travel');
  store.observe('alice', 'travel', 4); assert.equal(store.revision('alice', 'travel'), undefined);
  assert.equal(store.refreshed('alice', 'travel', 5, store.epoch('alice', 'travel')), 5);
  assert.equal(store.revision('bob', 'travel'), undefined);
  assert.equal(new SubmissionIntents().revision('alice', 'travel'), undefined);
});

test('one lost overlapping Stop cannot be masked by another successful Stop receipt', () => {
  for (const order of [[undefined, 8], [8, undefined]]) {
    const store = new SubmissionIntents(); store.observe('alice', 'travel', 6);
    store.stopStarted('alice', 'travel'); store.stopStarted('alice', 'travel');
    store.stopFinished('alice', 'travel', order[0]); store.stopFinished('alice', 'travel', order[1]);
    assert.equal(store.revision('alice', 'travel'), undefined);
    assert.equal(store.refreshed('alice', 'travel', 9, store.epoch('alice', 'travel')), 9);
  }
});

test('storage denied still retains page-local receipts; scope-corrupted storage is ignored', () => {
  const store = new SubmissionIntents({ getItem() { throw Error(); }, setItem() { throw Error(); }, removeItem() { throw Error(); } });
  store.add(make()); assert.equal(store.list('alice', 'travel')[0].id, 'one');
  assert.deepEqual(new SubmissionIntents().list('alice', 'travel'), []);
  const malicious = new SubmissionIntents({ getItem() { return JSON.stringify([make({ owner: 'other' })]); }, setItem() {}, removeItem() {} });
  assert.deepEqual(malicious.list('alice', 'travel'), []);
});

test('missing receipts remain a valid uncertain result and malformed success is never accepted', () => {
  const good = { accepted: true, run_id: 'run', status: 'interrupted', client_submission_id: 'one', submission_revision: 7, replayed: true };
  assert.deepEqual(readSubmissionReceipt(good, 'one'), good);
  assert.equal(readSubmissionLookup({ ...good, found: true }, 'one').found, true);
  assert.deepEqual(readSubmissionLookup({ found: false, client_submission_id: 'one', submission_revision: 7 }, 'one'), { found: false, client_submission_id: 'one', submission_revision: 7 });
  for (const invalid of [{}, { ...good, client_submission_id: 'other' }, { ...good, accepted: false }, { ...good, submission_revision: -1 }, { ...good, run_id: '' }]) assert.throws(() => readSubmissionReceipt(invalid, 'one'));
  assert.equal(submissionRejected(Error('network')), false);
  assert.equal(submissionRejected({ status: 500, code: 'internal' }), false);
  assert.equal(submissionRejected({ status: 409, code: 'submission_revision_conflict' }), true);
});

test('a snapshot started before a lost Stop cannot restore confidence after that Stop finishes', () => {
  const store = new SubmissionIntents(), before = store.epoch('alice', 'travel');
  store.stopStarted('alice', 'travel'); store.stopFinished('alice', 'travel');
  assert.throws(() => store.refreshed('alice', 'travel', 3, before));
  assert.equal(store.revision('alice', 'travel'), undefined);
  assert.equal(store.refreshed('alice', 'travel', 4, store.epoch('alice', 'travel')), 4);
});

test('unconfirmed Stop survives reload and only its exact receipt permits a new intent', () => {
  const storage = disk(), store = new SubmissionIntents(storage), stop = { id: 'stop-one', owner: 'alice', session: 'travel' };
  store.observe('alice', 'travel', 4); store.addStop(stop); store.stopStarted('alice', 'travel'); store.stopFinished('alice', 'travel');
  const reloaded = new SubmissionIntents(storage);
  assert.deepEqual(reloaded.pendingStops('alice', 'travel'), [stop]);
  assert.deepEqual(reloaded.pendingStops('bob', 'travel'), []);
  assert.throws(() => reloaded.refreshed('alice', 'travel', 4, reloaded.epoch('alice', 'travel')), 'Even a fresh snapshot is not proof the delayed Stop finished');
  assert.equal(readStopReceipt({ found: false, client_stop_id: 'stop-one' }, 'stop-one', true), null);
  assert.throws(() => readStopReceipt({ found: true, client_stop_id: 'different', submission_revision: 5 }, 'stop-one', true));
  reloaded.confirmStop(stop, readStopReceipt({ found: true, client_stop_id: 'stop-one', submission_revision: 5 }, 'stop-one', true));
  assert.equal(reloaded.refreshed('alice', 'travel', 5, reloaded.epoch('alice', 'travel')), 5);
  assert.deepEqual(new SubmissionIntents(storage).pendingStops('alice', 'travel'), []);
});

test('two uncertain Stops each need their own receipt and draft preservation survives reload', () => {
  const storage = disk(), store = new SubmissionIntents(storage), a = { id: 'a', owner: 'alice', session: 'travel' }, b = { ...a, id: 'b' };
  store.add(make()); store.preserveDrafts('alice', 'travel'); store.addStop(a); store.addStop(b); store.confirmStop(a, 6);
  assert.equal(store.revision('alice', 'travel'), undefined);
  const reloaded = new SubmissionIntents(storage);
  assert.equal(reloaded.list('alice', 'travel')[0].preserveDraft, true);
  assert.deepEqual(reloaded.pendingStops('alice', 'travel'), [b]);
  reloaded.confirmStop(b, 7); assert.equal(reloaded.refreshed('alice', 'travel', 7, reloaded.epoch('alice', 'travel')), 7);
});
