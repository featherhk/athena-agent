import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { DraftStore } from '../src/draft-store.ts';
import { DesktopTransitionFence } from '../src/desktop-transition.ts';
import { HelpDraftStore } from '../src/help-drafts.ts';
import { SubmissionBarrier } from '../src/submission-barrier.ts';
import { SubmissionIntents, submissionBody, readSubmissionReceipt, readSubmissionLookup, readStopReceipt, submissionRejected } from '../src/submission-intents.ts';
import { frontendRequest } from '../src/frontend-request.ts';
import { VoiceSubmissionError, VoiceSubmissionReceipt } from '../src/voice-recovery.ts';
import { SubmissionSourceError } from '../src/frontend-submission.ts';
import { scopeGuard } from '../src/app-actions.ts';
import { messagePage, SurfaceError, selectedSurface, dismissSurfaces, surfacesDismissed, surfaceFailed } from '../src/surface-runtime.ts';
import { findSurfaceFile, readSurfaceFile } from '../src/surface-files.ts';

// Exercise the actual App host closures, not a second implementation of the
// bridge. Transport fixtures have no network, browser or account access.
const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const fn = name => app.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(source);
const variable = name => app.body.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration => declaration.name.getText(source) === name)).getText(source);
const declares = (node, names) => ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration => names.includes(declaration.name.getText(source)));
// Use the actual render lifecycle statements and shared fixture refs. The
// ordering matters: a reused source must become loading before observation.
const lifecycle = app.body.statements.slice(app.body.statements.findIndex(node => declares(node, ['rendererIdentity'])),
  app.body.statements.findIndex(node => declares(node, ['actualSurface'])) + 1)
  .filter(node => !declares(node, ['rendererIdentity', 'rendererActivation', 'rendererRevision'])).map(node => node.getText(source)).join('\n');
const compiled = ts.transpileModule(`function bind({user,session,renderer,rendererKey,snapshot,orbPhase,locale,reduced,sending,uploading,resetting,text,attached,help,activeRendererActivation,ready=true,resumeReady=true,desktop=false}) {
  ${variable('helpDraftScope')}
  ${['currentGuard', 'submissionGuard', 'submissionScope', 'showDraft', 'editDraft', 'acceptSubmission', 'reconcileSubmission', 'submissionRevision', 'reconcileStop', 'stopSession', 'submit', 'invalidateActions', 'stop', 'openFiles', 'presentFile', 'returnToDefault'].map(fn).join('\n')}
  ${variable('surfaceScope')}
  ${variable('surfaceHost')}
  return surfaceHost;
}
function selection({user,session,snapshot,locale,recoveryMode,resetting,dismissedRenderer,rendererHealth}) {
  ${['selectedRenderer', 'rendererKey', 'rendererUnavailable', 'renderer'].map(variable).join('\n')}
  return renderer;
}
function observation({user,session,snapshot,locale,recoveryMode,resetting,dismissedRenderer,rendererHealth}) {
  ${['selectedRenderer', 'rendererKey', 'rendererUnavailable', 'renderer'].map(variable).join('\n')}
  ${lifecycle}
  return actualSurface;
}
function healthHandler() {
  ${fn('observeRendererHealth')}
  return observeRendererHealth;
}`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const copy = value => structuredClone(value);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const file = id => ({ id, name: `${id}.md`, content_url: `/api/files/${id}/content` });
const rejects = (promise, code) => assert.rejects(promise, error => error instanceof SurfaceError && error.message === code);
const ticks = async () => { for (let tick = 0; tick < 15; tick++) await Promise.resolve(); };
const receipt = (body, run_id = 'accepted-run', revision = body.expected_submission_revision) => ({
  accepted: true, run_id, status: 'queued', client_submission_id: body.client_submission_id,
  submission_revision: revision, replayed: false,
});

function fixture() {
  const state = { user: { id: 'alice' }, session: 'travel', renderer: { id: 'canvas' }, rendererKey: 'alice:travel:canvas:1:en',
    snapshot: { cursor: 5, submission_revision: 0, messages: [{ id: 'message', role: 'assistant', content: 'Real answer', reasoning: 'Visible reasoning', status: 'complete' }],
      runs: [{ id: 'run', status: 'complete', phase: 'responding', error_code: null, provider_request: 'must stay private' }],
      help_requests: [], files: [file('report')], components: [{ id: 'canvas', placement: 'surface', version: 1, view_revision: 0 }] },
    orbPhase: 'idle', locale: 'en', reduced: true, sending: false, uploading: false, resetting: false, text: '', attached: [], help: null,
    recoveryMode: false, rendererHealth: {}, dismissedRenderer: null };
  const uiScope = { current: { owner: 'alice', session: 'travel', generation: 1, control: 0 } };
  const rendererIdentity = { current: state.rendererKey }, rendererRevision = { current: 0 }, rendererActivation = { current: 1 }, drafts = { current: new DraftStore() }, helpDrafts = { current: new HelpDraftStore() };
  const submissions = { current: new SubmissionBarrier() }, calls = [], views = [], errors = [];
  const intents = { current: new SubmissionIntents() }, reconcilingIntents = { current: new Set() };
  intents.current.observe('alice', 'travel', state.snapshot.submission_revision);
  let transport = async (path, body) => path.endsWith('/messages') ? receipt(body)
    : path.endsWith('/stop') ? { ok: true, client_stop_id: body.client_stop_id, submission_revision: 1 } : { files: [], submission_revision: 0 };
  let prepare = async (_, client) => client;
  let layoutTransport = async () => {};
  let fileTransport = async () => new Response(JSON.stringify({ kind: 'text', content: 'File content', truncated: false }));
  const surfaceFileSnapshot = { current: state.snapshot };
  const desktopTransition = { current: new DesktopTransitionFence() };
  const env = { desktopTransition, desktopResume: { current: null }, drafts, helpDrafts, uiScope, rendererIdentity, rendererRevision, rendererActivation, submissions, VoiceSubmissionError, VoiceSubmissionReceipt, SubmissionSourceError,
    intents, reconcilingIntents, submissionBody, readSubmissionReceipt, readSubmissionLookup, readStopReceipt, submissionRejected, frontendRequest,
    AbortController, crypto: globalThis.crypto, refreshIntents() {},
    SurfaceError, messagePage, scopeGuard, selectedSurface, dismissSurfaces, surfacesDismissed, surfaceFailed, findSurfaceFile, surfaceFileSnapshot,
    readSurfaceFile: (...args) => readSurfaceFile(...args, (...fetchArgs) => fileTransport(...fetchArgs)), appClient: { current: 'client-one' },
    conversationScroll: { policy: { resume() {} } }, input: { current: null },
    frontendObservation: { prepareSubmission: (...args) => prepare(...args) },
    api: async (path, body) => { calls.push({ path, body: copy(body) }); return transport(path, body); },
    setText: value => { state.text = value; }, setAttached: value => { state.attached = value; },
    setSending: value => { state.sending = value; }, setUploading: value => { state.uploading = value; }, setPhase: value => { state.orbPhase = value; },
    setChooseFiles() {}, setVoiceRecovery() {}, setHelpDraftRevision() {}, setError: value => { errors.push(value); },
    finishSubmission(ticket, current) { const revision = submissions.current.finish(ticket); if (revision !== null) { submissions.current.commit(revision); if (current?.()) state.sending = false; } },
    setHistory: value => { views.push({ target: 'history', value }); }, setFiles: value => { views.push({ target: 'files', value }); },
    setAccount: value => { views.push({ target: 'account', value }); }, setAppearance: value => { views.push({ target: 'appearance', value }); },
    setActivity: value => { views.push({ target: 'activity', value }); }, setSurfaces: value => { views.push({ target: 'surfaces', value }); },
    setSnapshot: update => { state.snapshot = typeof update === 'function' ? update(state.snapshot) : update; },
    setDesktopReplyOpen: value => { views.push({ target: 'desktop-reply', value }); }, setDesktopLog: value => { views.push({ target: 'desktop-log', value }); },
    ensureVoiceSession: async () => { throw new Error('Unexpected session creation'); },
    setPreview: value => { views.push({ target: 'modal-preview', value }); }, setInlinePreview: value => { views.push({ target: 'inline-preview', value }); }, setDismissedRenderer: value => { state.dismissedRenderer = value; },
    setRendererHealth: update => { state.rendererHealth = update(state.rendererHealth); },
    placementOf: item => item.view?.placement || item.placement,
    changeComponentView: async (item, view) => { views.push({ target: 'layout', id: item.id, value: view }); return layoutTransport(item, view); },
    requestLogin() { errors.push('login'); }, t: value => value, errorMessage: value => value,
  };
  const context = vm.createContext(env); vm.runInContext(compiled, context);
  return { state, desktopTransition, uiScope, rendererIdentity, rendererRevision, rendererActivation, drafts, calls, views, errors,
    receipt: (runId, index = 0) => receipt(calls.filter(call => call.path.endsWith('/messages'))[index].body, runId),
    render: () => { surfaceFileSnapshot.current = state.snapshot; return context.bind({ ...state, attached: [...state.attached], activeRendererActivation: rendererActivation.current }); },
    selected: () => context.selection(state),
    observation: () => copy(context.observation(state)), health: (key, revision, value, activation = rendererActivation.current) => context.healthHandler()(key, revision, activation, value),
    draft(value) { const saved = drafts.current.write(state.user.id, state.session, value); state.text = saved.text; state.attached = saved.attached; return saved; },
    transport: value => { transport = value; }, prepare: value => { prepare = value; },
    layoutTransport: value => { layoutTransport = value; },
    fileTransport: value => { fileTransport = value; },
    switch(owner, session) { state.user = owner ? { id: owner } : null; state.session = session; uiScope.current = { ...uiScope.current, owner, session, generation: uiScope.current.generation + 1 }; },
  };
}

test('surface reads canonical messages and explicit run state without changing history', async () => {
  const f = fixture(), host = f.render(), original = copy(f.state.snapshot);
  const snapshot = copy(await host.call('snapshot', {}));
  assert.equal(snapshot.protocol, 1); assert.equal(snapshot.session_id, 'travel');
  assert.equal(snapshot.phase, 'idle'); assert.equal(snapshot.locale, 'en'); assert.equal(snapshot.reduced_motion, true);
  assert.deepEqual(snapshot.runs, [{ id: 'run', status: 'complete', phase: 'responding', error_code: null }]);
  const page = copy(await host.call('messages', { limit: 1 }));
  assert.equal(page.items[0].content, 'Real answer'); assert.equal(page.items[0].reasoning, 'Visible reasoning');
  assert.equal(page.has_more, false); assert.deepEqual(f.state.snapshot, original); assert.equal(f.calls.length, 0);
});

test('surface uses the canonical typed draft and rejects stale writes without losing attachments', async () => {
  const f = fixture(); const first = f.draft({ text: 'Already typed', attached: [file('keep')] }), host = f.render();
  assert.deepEqual(copy(await host.call('draft', {})), first);
  const updated = await host.call('writeDraft', { text: 'Edited in the custom surface', expected_revision: first.revision });
  assert.equal(updated.text, 'Edited in the custom surface'); assert.equal(updated.revision, first.revision + 1);
  assert.deepEqual(copy(updated.attached), [file('keep')]); assert.equal(f.state.text, updated.text);
  await rejects(host.call('writeDraft', { text: 'Stale replacement', expected_revision: first.revision }), 'draft_conflict');
  for (const text of [42, null, 'x'.repeat(30001)]) await rejects(host.call('writeDraft', { text, expected_revision: updated.revision }), 'invalid_request');
  assert.equal(f.drafts.current.read('alice', 'travel').text, updated.text); assert.equal(f.calls.length, 0);
});

test('surface send waits for a real receipt and clears only the submitted canonical draft', async () => {
  const f = fixture(), gate = deferred(); const saved = f.draft({ text: 'Book nothing; compare options', attached: [file('trip')] });
  f.transport(() => gate.promise);
  const host = f.render(), pending = host.call('request', { action: 'send', expected_revision: saved.revision, mode: 'queue' });
  await ticks();
  assert.equal(f.calls.length, 1);
  assert.match(f.calls[0].body.client_submission_id, /^[a-zA-Z0-9_-]+$/);
  assert.deepEqual(f.calls, [{ path: '/sessions/travel/messages', body: { text: saved.text, mode: 'queue', file_ids: ['trip'], frontend_client_id: 'client-one',
    client_submission_id: f.calls[0].body.client_submission_id, expected_submission_revision: 0 } }]);
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), saved);
  await rejects(host.call('request', { action: 'send', expected_revision: saved.revision }), 'not_sent');
  assert.equal(f.calls.length, 1, 'A second concurrent request cannot dispatch the same draft again');
  gate.resolve(f.receipt('durable-run'));
  assert.deepEqual(copy(await pending), { status: 'accepted', run_id: 'durable-run' });
  const after = f.drafts.current.read('alice', 'travel'); assert.equal(after.text, ''); assert.deepEqual(after.attached, []);
});

test('a new edit while send is pending survives its old receipt', async () => {
  const f = fixture(), gate = deferred(); const first = f.draft({ text: 'First request', attached: [file('first')] });
  f.transport(() => gate.promise);
  const host = f.render(), pending = host.call('request', { action: 'send', expected_revision: first.revision });
  await ticks();
  f.draft({ text: 'Next request still being written', attached: [file('first'), file('next')] });
  gate.resolve(f.receipt('first-run')); await pending;
  const after = f.drafts.current.read('alice', 'travel');
  assert.equal(after.text, 'Next request still being written'); assert.deepEqual(after.attached, [file('first'), file('next')]);
});

test('lost or malformed send receipts preserve the draft and never cause an automatic retry', async () => {
  for (const transport of [async () => { throw new Error('response lost'); }, async () => ({})]) {
    const f = fixture(); const original = f.draft({ text: 'One real instruction', attached: [file('keep')] });
    f.transport(transport);
    await rejects(f.render().call('request', { action: 'send', expected_revision: original.revision }), 'delivery_unknown');
    assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
    assert.equal(f.calls.filter(call => call.path.endsWith('/messages')).length, 1);
    assert.equal(f.calls.filter(call => call.path.includes('/submissions/')).length, 1, 'Only a read-only receipt lookup follows an uncertain POST');
    await ticks(); assert.equal(f.calls.length, 2); assert.equal(f.state.sending, false);
  }
});

test('stale draft revisions and invalid send modes never dispatch a message', async () => {
  const f = fixture(); const original = f.draft({ text: 'Current draft', attached: [] }), host = f.render();
  await rejects(host.call('request', { action: 'send', expected_revision: original.revision - 1 }), 'draft_conflict');
  await rejects(host.call('request', { action: 'send', expected_revision: original.revision, mode: 'repeat' }), 'invalid_request');
  assert.equal(f.calls.length, 0); assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
});

test('a retired frame or changed account/session cannot read drafts or invoke host effects', async () => {
  for (const retire of [f => { f.rendererIdentity.current = 'replacement-frame'; }, f => f.switch('bob', 'travel'), f => f.switch('alice', 'other')]) {
    const f = fixture(); const original = f.draft({ text: 'Private draft', attached: [] }), host = f.render();
    retire(f);
    for (const [method, args] of [['snapshot', {}], ['messages', {}], ['draft', {}], ['writeDraft', { text: 'Wrong session', expected_revision: original.revision }],
      ['request', { action: 'send', expected_revision: original.revision }], ['request', { action: 'open', target: 'account' }]]) {
      await rejects(host.call(method, args), 'surface_closed');
    }
    assert.equal(f.calls.length, 0); assert.equal(f.views.length, 0);
    assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
  }
});

test('Stop during frontend preparation prevents send and preserves the surface draft', async () => {
  const f = fixture(), gate = deferred(); const original = f.draft({ text: 'Do not start after stopping', attached: [file('keep')] });
  f.prepare(() => gate.promise);
  const host = f.render(), pending = host.call('request', { action: 'send', expected_revision: original.revision });
  await ticks();
  assert.deepEqual(copy(await host.call('request', { action: 'stop' })), { status: 'applied' });
  gate.resolve('client-one'); await rejects(pending, 'not_sent');
  assert.deepEqual(f.calls.map(item => item.path), ['/sessions/travel/stop']);
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
});

test('changing conversations during frontend preparation cannot send into the previous session', async () => {
  const f = fixture(), gate = deferred(); const original = f.draft({ text: 'Unsent old-session draft', attached: [] });
  f.prepare(() => gate.promise);
  const pending = f.render().call('request', { action: 'send', expected_revision: original.revision });
  f.switch('alice', 'other'); const other = f.draft({ text: 'Independent new-session draft', attached: [] });
  gate.resolve('client-one'); await rejects(pending, 'not_sent');
  assert.equal(f.calls.length, 0); assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
  assert.deepEqual(f.drafts.current.read('alice', 'other'), other);
});

test('host actions use existing views and reject unavailable files and arbitrary methods', async () => {
  const f = fixture(), host = f.render();
  for (const target of ['history', 'account', 'appearance', 'activity', 'surfaces']) {
    assert.equal((await host.call('request', { action: 'open', target })).status, 'requested');
  }
  assert.deepEqual(f.views.map(view => view.target), ['history', 'account', 'appearance', 'activity', 'surfaces']);
  await host.call('request', { action: 'preview', file_id: 'report' });
  assert.deepEqual(f.views.slice(-3), [{ target: 'inline-preview', value: file('report') }, { target: 'modal-preview', value: null }, { target: 'files', value: null }]);
  await rejects(host.call('request', { action: 'preview', file_id: 'foreign' }), 'file_not_found');
  await rejects(host.call('request', { action: 'open', target: 'eval' }), 'unsupported_target');
  await rejects(host.call('request', { action: 'erase_history' }), 'unsupported_action');
  await rejects(host.call('exec', { code: 'arbitrary code' }), 'unsupported_method');
  assert.equal(f.calls.length, 0);
});

test('Stop retires the pre-stop surface closure before the next React render', async () => {
  const f = fixture(); const draft = f.draft({ text: 'Still here', attached: [file('keep')] }), host = f.render();
  assert.equal((await host.call('request', { action: 'stop' })).status, 'applied');
  for (const [method, args] of [['snapshot', {}], ['draft', {}], ['writeDraft', { text: 'Stale frame', expected_revision: draft.revision }],
    ['request', { action: 'preview', file_id: 'report' }], ['request', { action: 'send', expected_revision: draft.revision }]]) {
    await rejects(host.call(method, args), 'surface_closed');
  }
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), draft);
  assert.deepEqual(f.calls.map(call => call.path), ['/sessions/travel/stop']); assert.equal(f.views.length, 0);
  // A new render gets the current action guard and can still use the same frame.
  assert.equal((await f.render().call('draft', {})).text, draft.text);
});

test('surface file reads include public message attachments and stay inside the current conversation', async () => {
  const f = fixture(), requests = [];
  f.state.snapshot.messages.push({ id: 'upload', role: 'user', content: '', status: 'complete', attachments: [file('attached')] });
  f.state.snapshot.messages.push({ id: 'private', role: 'tool', content: '', status: 'complete', attachments: [file('tool-only')] });
  f.fileTransport(async url => { requests.push(url); return new Response(JSON.stringify({ kind: 'document', blocks: [{ kind: 'paragraph', text: 'Owned attachment', heading: 0 }], truncated: false })); });
  const host = f.render(), result = await host.call('file', { file_id: 'attached' });
  assert.equal(result.kind, 'document'); assert.equal(result.blocks[0].text, 'Owned attachment');
  assert.deepEqual(requests, ['/api/files/attached/preview']);
  await host.call('request', { action: 'preview', file_id: 'attached' });
  assert.deepEqual(f.views.slice(-3), [{ target: 'inline-preview', value: file('attached') }, { target: 'modal-preview', value: null }, { target: 'files', value: null }]);
  for (const file_id of ['unknown', 'tool-only', 'https://unrelated.invalid/file']) await rejects(host.call('file', { file_id }), 'file_not_found');
  assert.equal(requests.length, 1);
});

test('a file request finishing after a session, account, stop or renderer activation change releases no data', async () => {
  for (const retire of [f => f.switch('alice', 'other'), f => f.switch('bob', 'travel'),
    f => { f.uiScope.current.control++; }, f => { f.rendererActivation.current++; }]) {
    const f = fixture(), gate = deferred(); let reads = 0;
    f.fileTransport(async () => { reads++; return gate.promise; });
    const host = f.render(), result = host.call('file', { file_id: 'report' });
    retire(f); gate.resolve(new Response(JSON.stringify({ kind: 'text', content: 'Old-session content', truncated: false })));
    await rejects(result, 'surface_closed'); assert.equal(reads, 1);
    await rejects(host.call('file', { file_id: 'report' }), 'surface_closed'); assert.equal(reads, 1);
  }
});

test('returning to default suppresses every standby while hide receipts arrive, then a layout activation reopens', async () => {
  const f = fixture();
  f.state.snapshot.components.unshift({ id: 'alternate', placement: 'surface', version: 1, view_revision: 0 });
  const gates = new Map(f.state.snapshot.components.map(item => [item.id, deferred()]));
  f.layoutTransport(item => gates.get(item.id).promise);
  assert.equal(f.selected().id, 'canvas');
  const pending = f.render().call('request', { action: 'default' });
  assert.equal(f.selected(), undefined, 'The fixed shell returns before either network request finishes');
  assert.equal((await pending).status, 'requested');
  for (const id of ['canvas', 'alternate']) {
    f.state.snapshot.components = f.state.snapshot.components.map(item => item.id === id ? { ...item, view: { hidden: true }, view_revision: 1 } : item);
    gates.get(id).resolve(); await ticks();
    assert.equal(f.selected(), undefined, 'Hiding one renderer must never briefly mount another');
  }
  // Same source version, only a later layout revision. This is shared by a
  // user's Open control and the agent's registry layout operation.
  f.state.snapshot.components = f.state.snapshot.components.map(item => item.id === 'alternate' ? { ...item, view: { hidden: false }, view_revision: 2 } : item);
  assert.equal(f.selected().id, 'alternate');
});

test('failed hide keeps the default visible but cannot permanently block a later explicit reopen', async () => {
  const f = fixture();
  f.state.snapshot.components.unshift({ id: 'alternate', placement: 'surface', version: 1, view_revision: 0 });
  f.layoutTransport(async () => { throw new Error('offline'); });
  await f.render().call('request', { action: 'default' }); await ticks();
  assert.equal(f.selected(), undefined);
  f.state.snapshot.components[1] = { ...f.state.snapshot.components[1], view_revision: 1, view: { hidden: false } };
  assert.equal(f.selected().id, 'canvas');
});

test('surface dismissal is scoped and a new visible source version can deliberately reactivate', async () => {
  const f = fixture();
  await f.render().call('request', { action: 'default' });
  assert.equal(f.selected(), undefined);
  f.state.snapshot.components[0] = { ...f.state.snapshot.components[0], version: 2, view: { hidden: true } };
  assert.equal(f.selected(), undefined, 'Updating a hidden source is not an activation');
  f.state.snapshot.components[0] = { ...f.state.snapshot.components[0], view: { hidden: false } };
  assert.equal(f.selected().version, 2);
  f.state.snapshot.components[0] = { ...f.state.snapshot.components[0], version: 1 };
  f.switch('alice', 'another'); assert.equal(f.selected().id, 'canvas');
  f.switch('bob', 'travel'); assert.equal(f.selected().id, 'canvas');
});

test('failed renderer stays default until a new layout activation retries the same source', () => {
  const f = fixture(), key = f.state.rendererKey;
  f.health(key, 0, 'failed');
  assert.equal(f.selected(), undefined);
  assert.deepEqual(f.observation(), { provider: 'default', state: 'mounted' });
  // A same-activation late mounted report must not undo a failure.
  f.health(key, 0, 'mounted'); assert.equal(f.selected(), undefined);
  f.state.snapshot.components[0] = { ...f.state.snapshot.components[0], view_revision: 1, view: { hidden: false } };
  f.rendererRevision.current = 1;
  assert.equal(f.selected().id, 'canvas');
  assert.equal(f.observation().state, 'loading', 'The failed frame is not proof that its replacement mounted');
  const before = f.state.rendererHealth;
  f.health(key, 0, 'mounted'); f.health(key, 0, 'failed');
  assert.equal(f.state.rendererHealth, before, 'Old activation callbacks are ignored');
  assert.equal(f.observation().state, 'loading');
  f.health(key, 1, 'mounted');
  assert.equal(f.observation().state, 'mounted'); assert.equal(f.observation().view_revision, 1);
});

test('layout changes keep a mounted renderer healthy, but a new failure belongs to its current activation', () => {
  const f = fixture(), key = f.state.rendererKey;
  f.health(key, 0, 'mounted');
  f.state.snapshot.components[0].view_revision = 2; f.rendererRevision.current = 2;
  assert.equal(f.observation().state, 'mounted');
  f.health(key, 2, 'failed');
  assert.equal(f.selected(), undefined); assert.equal(f.state.rendererHealth[key].view_revision, 2);
  f.state.snapshot.components[0].view_revision = 1;
  assert.equal(f.selected(), undefined, 'An older snapshot is not a new activation');
});

test('late previous-version or retired-frame health cannot mark a new renderer mounted', () => {
  const f = fixture(), oldKey = f.state.rendererKey, newKey = 'alice:travel:canvas:2:en';
  f.state.snapshot.components[0].version = 2; f.rendererIdentity.current = newKey;
  f.health(oldKey, 0, 'mounted');
  assert.deepEqual(copy(f.state.rendererHealth), {}); assert.equal(f.observation().state, 'loading');
  f.health(newKey, 0, 'mounted'); assert.equal(f.observation().state, 'mounted');
  f.rendererIdentity.current = '';
  const previous = f.state.rendererHealth; f.health(newKey, 0, 'failed');
  assert.equal(f.state.rendererHealth, previous, 'Unmounted frame callbacks cannot rewrite health');
});

test('A to B to A starts loading and rejects receipts from the previous A mount', () => {
  const f = fixture(), first = copy(f.state.snapshot.components[0]), oldKey = f.state.rendererKey;
  f.observation(); const firstActivation = f.rendererActivation.current;
  f.health(oldKey, 0, 'mounted'); assert.equal(f.observation().state, 'mounted');
  f.state.snapshot.components = [{ ...first, id: 'other' }];
  assert.equal(f.observation().state, 'loading');
  const otherKey = f.rendererIdentity.current; f.health(otherKey, 0, 'mounted'); assert.equal(f.observation().state, 'mounted');
  f.state.snapshot.components = [first];
  assert.equal(f.observation().state, 'loading', 'Mounted evidence belongs to a continuous iframe lifetime');
  assert.ok(f.rendererActivation.current > firstActivation);
  f.health(oldKey, 0, 'mounted', firstActivation); f.health(oldKey, 0, 'failed', firstActivation);
  assert.equal(f.observation().state, 'loading'); assert.equal(f.selected().id, 'canvas');
  f.health(oldKey, 0, 'mounted'); assert.equal(f.observation().state, 'mounted');
});

test('returning from another conversation or recovery cannot reuse an earlier mounted receipt', () => {
  for (const leave of ['conversation', 'recovery', 'hidden']) {
    const f = fixture(), key = f.state.rendererKey;
    f.observation(); f.health(key, 0, 'mounted'); const previousActivation = f.rendererActivation.current;
    assert.equal(f.observation().state, 'mounted');
    if (leave === 'conversation') f.switch('alice', 'other');
    else if (leave === 'recovery') f.state.recoveryMode = true;
    else f.state.snapshot.components[0].view = { hidden: true };
    f.observation();
    if (leave === 'conversation') f.switch('alice', 'travel');
    else if (leave === 'recovery') f.state.recoveryMode = false;
    else f.state.snapshot.components[0].view = { hidden: false };
    assert.equal(f.observation().state, 'loading', leave);
    f.health(key, 0, 'mounted', previousActivation); assert.equal(f.observation().state, 'loading', leave);
    f.health(key, 0, 'mounted'); assert.equal(f.observation().state, 'mounted', leave);
  }
});

test('an ordinary layout update does not create a new iframe activation or discard mounted evidence', () => {
  const f = fixture(), key = f.state.rendererKey;
  f.observation(); f.health(key, 0, 'mounted'); const activation = f.rendererActivation.current;
  f.state.snapshot.components[0] = { ...f.state.snapshot.components[0], view_revision: 1, view: { width: 700 } };
  assert.equal(f.observation().state, 'mounted'); assert.equal(f.rendererActivation.current, activation);
  f.health(key, 1, 'failed'); assert.equal(f.selected(), undefined);
  assert.equal(f.state.rendererHealth[key].view_revision, 1);
});

test('generated surface cannot replace a draft or send new work during final desktop persistence', async () => {
  const f = fixture(), draft = f.draft({ text: 'Final saved words', attached: [file('kept')] }), host = f.render();
  f.desktopTransition.current.begin('logout');
  await rejects(host.call('writeDraft', { text: 'Too late', expected_revision: draft.revision }), 'surface_closed');
  await rejects(host.call('request', { action: 'send', expected_revision: draft.revision }), 'not_sent');
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), draft);
  assert.equal(f.calls.length, 0);
});


test('desktop surface preview delegates to durable presentation and rereads canonical state before showing it', async () => {
  const f = fixture(); f.state.desktop = true;
  const canonical = { ...file('report'), content_revision: 's1-current' };
  const component = { id: 'athena_file_report', version: 1, file_id: 'report', file: canonical, placement: 'desktop', view: { hidden: false } };
  const committed = { ...copy(f.state.snapshot), cursor: 6, components: [component] };
  f.transport(async path => path.endsWith('/presentation') ? { component, created: true } : committed);
  assert.equal((await f.render().call('request', { action: 'preview', file_id: 'report' })).status, 'requested');
  assert.deepEqual(f.calls, [
    { path: '/sessions/travel/files/report/presentation', body: { reveal: true } },
    { path: '/sessions/travel', body: undefined },
  ]);
  assert.deepEqual(copy(f.state.snapshot), committed);
  assert.deepEqual(f.views, [
    { target: 'inline-preview', value: canonical }, { target: 'modal-preview', value: null }, { target: 'files', value: null },
    { target: 'desktop-reply', value: false }, { target: 'desktop-log', value: false },
  ]);
  assert.equal(f.errors.length, 0);
});

test('a desktop preview request cannot reopen a concurrently hidden or removed file component', async () => {
  for (const unavailable of ['hidden', 'removed']) {
    const f = fixture(); f.state.desktop = true;
    const component = { id: 'athena_file_report', version: 1, file_id: 'report', file: file('report'), placement: 'desktop', view: { hidden: true } };
    const committed = { ...copy(f.state.snapshot), cursor: 6, components: unavailable === 'removed' ? [] : [component] };
    f.transport(async path => path.endsWith('/presentation') ? { component, created: false } : committed);
    assert.equal((await f.render().call('request', { action: 'preview', file_id: 'report' })).status, 'requested');
    assert.equal(f.calls.length, 2);
    assert.deepEqual(f.views, [], unavailable);
    assert.deepEqual(copy(f.state.snapshot), committed);
  }
});

test('a desktop presentation reply after an account or conversation switch never opens old work', async () => {
  for (const next of [['alice', 'other'], ['bob', 'travel']]) {
    const f = fixture(), gate = deferred(); f.state.desktop = true;
    f.transport(() => gate.promise);
    const pending = f.render().call('request', { action: 'preview', file_id: 'report' });
    await ticks(); assert.equal(f.calls.length, 1);
    f.switch(...next);
    gate.resolve({ component: { id: 'athena_file_report', file_id: 'report' }, created: true });
    assert.equal((await pending).status, 'requested');
    assert.equal(f.calls.length, 1, 'Retired UI must not request another snapshot or retry the side effect');
    assert.deepEqual(f.views, []);
  }
});
