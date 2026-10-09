import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { DesktopResumeStore, readResumeClaim } from '../src/desktop-resume.ts';
import { DraftStore } from '../src/draft-store.ts';
import { SessionSelection } from '../src/session-selection.ts';
import { SubmissionIntents } from '../src/submission-intents.ts';
import { HelpDraftStore } from '../src/help-drafts.ts';
import { DesktopTransitionFence } from '../src/desktop-transition.ts';

const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const functions = ['restoreUser', 'submit', 'navigate', 'freezeDesktop', 'thawDesktop', 'editDraft', 'upload', 'signOut', 'submissionGuard'].map(name => app.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(source));
const code = ts.transpileModule(functions.join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function fixture() {
  const delayed = deferred(), steps = [], storage = new DesktopResumeStore(async () => { throw Error('unexpected save'); });
  const state = { desktop: true, desktopTransition: { current: new DesktopTransitionFence() }, uploadPending: { current: 0 }, fileInput: { current: null },
    setDesktopFrozen: value => steps.push(['frozen', value]), window: { dispatchEvent() {} }, Event: class {}, t: value => value, desktopResume: { current: storage }, authEpoch: { current: 4 }, api: () => delayed.promise,
    crypto: { randomUUID: () => 'new-window' }, readResumeClaim, SessionSelection, DraftStore, SubmissionIntents, HelpDraftStore,
    selection: { current: null }, drafts: { current: null }, intents: { current: null }, helpDrafts: { current: null },
    setResumeReady: value => { steps.push(['ready', value]); }, acceptUser: user => { steps.push(['account', user.id, state.selection.current.read(user.id), state.drafts.current.read(user.id, 'trip').text]); },
    ready: true, resumeReady: false, text: 'Do something', attached: [], sending: false, resetting: false, uploading: false,
    uiScope: { current: { owner: 'alice', session: 'trip', generation: 7 } },
    setDesktopLog: value => steps.push(['log', value]), setDesktopReplyOpen: value => steps.push(['open', value]),
    setHistory: value => steps.push(['history', value]), setMenu: value => steps.push(['menu', value]),
  };
  const context = vm.createContext(state); vm.runInContext(code, context);
  return { delayed, steps, state, context };
}

test('actual App does not accept an account or enable send until durable restore completes', async () => {
  const f = fixture();
  const pending = f.context.restoreUser({ id: 'alice' }, undefined, 4);
  assert.deepEqual(f.steps, [['ready', false]]);
  assert.equal(await f.context.submit(), undefined, 'Send must return before creating a session or requesting a model');
  f.delayed.resolve({ writer: 'new-window', sequence: 0, entries: { 'athena-session:alice': 'trip', 'athena-draft:["alice","trip"]': JSON.stringify({ text: 'Unsent on previous port', revision: 8, attached: [] }) } });
  assert.equal(await pending, true);
  assert.deepEqual(f.steps, [['ready', false], ['account', 'alice', 'trip', 'Unsent on previous port'], ['ready', true]]);
});

test('actual App ignores a restore that arrives after account generation changed', async () => {
  const f = fixture(), pending = f.context.restoreUser({ id: 'alice' }, undefined, 4);
  f.state.authEpoch.current++;
  f.delayed.resolve({ writer: 'new-window', sequence: 0, entries: { 'athena-session:alice': 'private' } });
  assert.equal(await pending, false);
  assert.deepEqual(f.steps, [['ready', false]]);
  assert.equal(f.state.selection.current, null);
  assert.equal(f.state.desktopResume.current.getItem('athena-session:alice'), null);
});

test('actual App reselecting same history row changes presentation without clearing conversation, draft or operation state', () => {
  const f = fixture();
  // Any reset, snapshot, navigation epoch or submission change is undefined in
  // this fixture and would throw, catching a regression to destructive reload.
  f.context.navigate('trip');
  assert.deepEqual(f.steps, [['log', false], ['open', true], ['history', false], ['menu', false]]);
  assert.equal(f.state.uiScope.current.generation, 7);
  assert.equal(f.state.text, 'Do something');
});

test('actual App freezes edits navigation uploads and sends across final persistence boundary', async () => {
  const f = fixture(), token = f.context.freezeDesktop('quit');
  assert.deepEqual(f.steps, [['frozen', true]]);
  f.context.editDraft({ text: 'Must not appear after final save' });
  f.context.navigate('different-task');
  await f.context.submit('New action');
  await f.context.upload([{ name: 'late.txt', size: 1 }]);
  assert.deepEqual(f.steps, [['frozen', true]]);
  assert.equal(f.state.text, 'Do something');
  assert.equal(f.state.uiScope.current.session, 'trip');
  assert.equal(f.state.uploadPending.current, 0);
  f.context.thawDesktop(token);
  assert.deepEqual(f.steps.at(-1), ['frozen', false]);
});

test('actual App refuses final persistence boundary while an attachment upload is unfinished', () => {
  const f = fixture(); f.state.uploadPending.current = 1;
  assert.throws(() => f.context.freezeDesktop('quit'), /desktop.saveFailed/);
  assert.equal(f.state.desktopTransition.current.blocked, false);
  assert.deepEqual(f.steps, []);
});

test('actual App failed logout thaws only after final flush and logout attempt', async () => {
  const f = fixture(), saved = deferred(), logout = deferred(); const paths = [];
  f.state.desktopResume.current = { flush: () => { paths.push('flush'); return saved.promise; } };
  f.state.invalidateActions = () => {};
  f.state.setError = value => f.steps.push(['error', value]);
  f.state.api = async path => { paths.push(path); if (path === '/auth/logout') { await logout.promise; throw Error('offline'); } return { user: { id: 'alice' } }; };
  const pending = f.context.signOut();
  assert.deepEqual(paths, ['flush']);
  assert.equal(f.state.desktopTransition.current.blocked, true);
  saved.resolve(); await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(paths, ['flush', '/auth/logout']);
  f.context.editDraft({ text: 'Too late' }); f.context.navigate('late-session');
  assert.equal(f.state.text, 'Do something'); assert.equal(f.state.uiScope.current.session, 'trip');
  logout.resolve(); await pending;
  assert.deepEqual(paths, ['flush', '/auth/logout', '/auth/me']);
  assert.equal(f.state.desktopTransition.current.blocked, false);
  assert.deepEqual(f.steps.at(-1), ['frozen', false]);
});

test('a canceled old quit cannot thaw a newer logout boundary', () => {
  const fence = new DesktopTransitionFence(), old = fence.begin('quit');
  assert.equal(fence.cancelQuit(), true);
  const current = fence.begin('logout');
  assert.equal(fence.release(old), false);
  assert.equal(fence.cancelQuit(), false);
  assert.equal(fence.blocked, true);
  assert.equal(fence.release(current), true);
  assert.equal(fence.blocked, false);
});

test('already-started session creation cannot dispatch or navigate after Quit finalization starts', async () => {
  const f = fixture(), created = deferred(), paths = [];
  Object.assign(f.state, {
    ready: true, resumeReady: true, user: { id: 'alice' }, session: null,
    submissions: { current: { begin: () => ({ id: 1 }) } }, submissionScope: () => 'alice:blank',
    currentGuard: () => () => true,
    drafts: { current: { read: () => ({ text: 'Keep this unsent request', attached: [], revision: 1 }) } },
    setSending: () => {}, setError: () => {}, conversationScroll: { policy: { resume() {} } }, finishSubmission: () => {},
    api: async path => { paths.push(path); return created.promise; },
  });
  const sending = f.context.submit('Keep this unsent request');
  assert.deepEqual(paths, ['/sessions']);
  f.context.freezeDesktop('quit');
  created.resolve({ id: 'allocated-but-unsent' }); await sending;
  assert.deepEqual(paths, ['/sessions'], 'No message or new intent may follow the shutdown persistence boundary');
  assert.equal(f.state.uiScope.current.session, 'trip', 'Navigation remains unchanged during shutdown');
});
