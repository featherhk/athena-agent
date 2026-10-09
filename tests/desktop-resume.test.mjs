import assert from 'node:assert/strict';
import test from 'node:test';
import { DesktopResumeStore, readResumeClaim } from '../src/desktop-resume.ts';
import { DraftStore } from '../src/draft-store.ts';
import { SessionSelection } from '../src/session-selection.ts';
import { SubmissionIntents } from '../src/submission-intents.ts';

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const empty = writer => ({ writer, sequence: 0, entries: {} });
function server() {
  const state = { entries: {}, sequence: 0, writer: 'first' }, requests = [];
  const save = async (path, body) => {
    assert.equal(path, '/desktop/resume/save'); requests.push(structuredClone(body));
    if (body.writer !== state.writer) throw Error('desktop_resume_conflict');
    if (body.sequence !== state.sequence + 1) throw Error('sequence_conflict');
    Object.entries(body.changes).forEach(([key, value]) => { if (value === null) delete state.entries[key]; else state.entries[key] = value; });
    state.sequence = body.sequence; return { writer: body.writer, sequence: body.sequence };
  };
  return { state, requests, save };
}

test('new origin restores account conversation, draft and pending operation IDs without any dispatch', async () => {
  const backend = server(), original = new DesktopResumeStore(backend.save);
  original.activate('alice', empty('first'));
  const selection = new SessionSelection(original), drafts = new DraftStore(original), intents = new SubmissionIntents(original);
  selection.write('alice', 'trip');
  const file = { id: 'file1', name: 'route.xlsx', content_url: '/api/files/file1/content' };
  const draft = drafts.write('alice', 'trip', { text: 'Continue planning', attached: [file] });
  intents.add({ id: 'send-once', owner: 'alice', session: 'trip', text: draft.text, mode: 'queue', revision: 4, file_ids: ['file1'], draft });
  intents.addStop({ id: 'stop-once', owner: 'alice', session: 'trip' });
  await original.flush('alice');
  const restarted = new DesktopResumeStore(backend.save);
  backend.state.writer = 'new-origin'; backend.state.sequence = 0;
  restarted.activate('alice', { writer: 'new-origin', sequence: 0, entries: structuredClone(backend.state.entries) });
  assert.equal(new SessionSelection(restarted).read('alice'), 'trip');
  assert.deepEqual(new DraftStore(restarted).read('alice', 'trip'), draft);
  assert.equal(new SubmissionIntents(restarted).list('alice', 'trip')[0].id, 'send-once');
  assert.equal(new SubmissionIntents(restarted).pendingStops('alice', 'trip')[0].id, 'stop-once');
  assert.equal(backend.requests.length, 1);
});

test('explicit new conversation and its draft survive restart without reviving previous task', async () => {
  const backend = server(), storage = new DesktopResumeStore(backend.save); storage.activate('alice', empty('first'));
  const selection = new SessionSelection(storage), drafts = new DraftStore(storage);
  selection.write('alice', 'old'); drafts.write('alice', 'old', { text: 'Old unfinished task', attached: [] });
  selection.write('alice', null); drafts.write('alice', null, { text: 'A new idea', attached: [] });
  await storage.flush();
  const next = new DesktopResumeStore(backend.save); next.activate('alice', { ...empty('restart'), entries: backend.state.entries });
  assert.equal(new SessionSelection(next).read('alice'), null);
  assert.equal(new DraftStore(next).read('alice', null).text, 'A new idea');
  assert.equal(new DraftStore(next).read('alice', 'old').text, 'Old unfinished task');
});

test('inflight older draft write cannot overwrite later edits and navigation', async () => {
  const backend = server(), barrier = deferred(); let first = true;
  const storage = new DesktopResumeStore(async (path, body) => { if (first) { first = false; await barrier.promise; } return backend.save(path, body); });
  storage.activate('alice', empty('first'));
  const selection = new SessionSelection(storage), drafts = new DraftStore(storage);
  selection.write('alice', 'first-task'); drafts.write('alice', 'first-task', { text: 'a', attached: [] });
  const pending = storage.flush('alice');
  drafts.write('alice', 'first-task', { text: 'abc', attached: [] });
  selection.write('alice', 'second-task'); drafts.write('alice', 'second-task', { text: 'New context', attached: [] });
  barrier.resolve(); await pending;
  assert.equal(backend.state.sequence, 2);
  assert.equal(backend.state.entries['athena-session:alice'], 'second-task');
  assert.equal(JSON.parse(backend.state.entries['athena-draft:["alice","first-task"]']).text, 'abc');
});

test('lost save receipt retries frozen sequence and payload before saving subsequent edits', async () => {
  const seen = []; let fail = true;
  const storage = new DesktopResumeStore(async (path, body) => {
    seen.push(structuredClone(body));
    if (fail) throw Error('lost-response');
    return { writer: body.writer, sequence: body.sequence };
  });
  storage.activate('alice', empty('first'));
  storage.setItem('athena-session:alice', 'old');
  await assert.rejects(storage.flush('alice'), /lost-response/);
  await new Promise(resolve => setTimeout(resolve, 0));
  storage.setItem('athena-session:alice', 'new'); fail = false;
  await storage.flush('alice');
  assert.deepEqual(seen[0], seen.at(-2));
  assert.equal(seen.at(-1).sequence, 2);
  assert.deepEqual(seen.at(-1).changes, { 'athena-session:alice': 'new' });
});

test('switching accounts fences a late previous flush and does not copy its selection', async () => {
  const barrier = deferred(); const writes = [];
  const storage = new DesktopResumeStore(async (path, body) => { writes.push(body); await barrier.promise; return { writer: body.writer, sequence: body.sequence }; });
  storage.activate('alice', empty('alice-client'));
  storage.setItem('athena-session:alice', 'private'); const saving = storage.flush('alice');
  storage.activate('bob', empty('bob-client'));
  assert.equal(new SessionSelection(storage).read('bob'), null);
  barrier.resolve(); await saving;
  await assert.rejects(storage.flush('alice'), /desktop_resume_conflict/);
  await storage.flush('bob');
  assert.equal(writes.filter(write => write.writer === 'bob-client').flatMap(write => Object.keys(write.changes)).includes('athena-session:alice'), false);
  assert.throws(() => storage.activate('bob', { ...empty('bad'), entries: { 'athena-session:alice': 'private' } }), /invalid_response/);
});

test('hydration receipt cannot silently accept malformed or unrelated state', async () => {
  for (const raw of [null, {}, { writer: 'other', sequence: 0, entries: {} }, { writer: 'first', sequence: -1, entries: {} }, { writer: 'first', sequence: 0, entries: { key: {} } }]) {
    assert.throws(() => readResumeClaim(raw, 'first'), /invalid_response/);
  }
  assert.deepEqual(readResumeClaim(empty('first'), 'first'), empty('first'));
  const storage = new DesktopResumeStore(async () => { throw Error('should-not-send'); });
  await assert.rejects(storage.flush('alice'), /desktop_resume_unavailable/);
});

test('typing is coalesced while explicit quit flush bypasses the timer', async () => {
  const backend = server(), storage = new DesktopResumeStore(backend.save); storage.activate('alice', empty('first'));
  for (let index = 0; index < 20; index++) storage.setItem('athena-draft:["alice","trip"]', JSON.stringify({ text: 'x'.repeat(index + 1), attached: [], revision: index + 1 }));
  assert.equal(backend.requests.length, 0);
  await storage.flush('alice');
  assert.equal(backend.requests.length, 1);
  assert.equal(JSON.parse(backend.state.entries['athena-draft:["alice","trip"]']).text.length, 20);
  await new Promise(resolve => setTimeout(resolve, 175));
  assert.equal(backend.requests.length, 1, 'A flushed timer must not make redundant writes later');
});
