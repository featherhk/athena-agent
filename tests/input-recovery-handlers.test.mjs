import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { HelpDraftStore } from '../src/help-drafts.ts';
import { DraftStore } from '../src/draft-store.ts';
import { SubmissionBarrier } from '../src/submission-barrier.ts';
import { SubmissionIntents, submissionBody, readSubmissionReceipt, readSubmissionLookup, readStopReceipt, submissionRejected } from '../src/submission-intents.ts';
import { frontendRequest } from '../src/frontend-request.ts';
import { VoiceSubmissionError, VoiceSubmissionReceipt } from '../src/voice-recovery.ts';
import { scopeGuard } from '../src/app-actions.ts';
import { SubmissionSourceError } from '../src/frontend-submission.ts';
import { ConversationScroll } from '../src/conversation-scroll.ts';

// Compile the actual App handler bodies and derived help state with their lexical
// render scope. This tests effects/receipts, not a reimplementation of submission.
const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const functionSource = name => app.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(source);
const variableSource = name => app.body.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration => declaration.name.getText(source) === name)).getText(source);
const compiled = ts.transpileModule(`function bind({ user, session, help, sending, uploading, attached, text, resetting }) {
  ${['helpDraftScope', 'helpAnswers', 'voiceQuestion'].map(variableSource).join('\n')}
  ${['acceptSubmission', 'reconcileSubmission', 'submissionRevision', 'reconcileStop', 'stopSession', 'submit', 'submitVoice', 'answerHelp', 'upload', 'invalidateActions', 'stop'].map(functionSource).join('\n')}
  return { submit, submitVoice, answerHelp, upload, stop, voiceQuestion, helpAnswers };
}`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;

const scope = { owner: 'alice', session: 'travel', request: 'trip' };
const help = { id: scope.request, questions: [{ id: 'date', question: 'When?' }, { id: 'city', question: 'Where?' }] };
const file = id => ({ id, name: `${id}.md`, content_url: `/api/files/${id}/content` });
const ticks = async () => { for (let tick = 0; tick < 15; tick++) await Promise.resolve(); };
const receipt = (body, run_id = 'run', revision = body.expected_submission_revision) => ({
  accepted: true, run_id, status: 'queued', client_submission_id: body.client_submission_id,
  submission_revision: revision, replayed: false,
});
function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
function fixture() {
  const disk = storage(), helpDrafts = { current: new HelpDraftStore(disk) }, drafts = { current: new DraftStore() };
  const state = { user: { id: scope.owner }, session: scope.session, help, sending: false, uploading: false, attached: [], text: '', resetting: false };
  const uiScope = { current: { owner: scope.owner, session: scope.session, generation: 1, control: 0 } };
  const submissions = { current: new SubmissionBarrier() }, calls = [], ordinary = [], errors = [], uploads = [], navigations = [];
  const intents = { current: new SubmissionIntents(disk) }, reconcilingIntents = { current: new Set() };
  intents.current.observe(scope.owner, scope.session, 0);
  let transport = async (path, body) => body === undefined ? { cursor: 1, submission_revision: 0 } : path.endsWith('/stop') ? { ok: true, client_stop_id: body.client_stop_id, submission_revision: 1 }
    : path.endsWith('/messages') ? receipt(body) : { id: state.help.id, status: 'answered' };
  let prepare = async (_, client) => client;
  let uploadTransport = async (_, init) => ({ ok: true, json: async () => file(init.body.name) });
  const fileInput = { current: { value: '' } }, authEpoch = { current: 1 };
  const environment = {
    helpDrafts, drafts, submissions, uiScope, fileInput, authEpoch, VoiceSubmissionError, VoiceSubmissionReceipt, SubmissionSourceError,
    intents, reconcilingIntents, submissionBody, readSubmissionReceipt, readSubmissionLookup, readStopReceipt, submissionRejected, frontendRequest,
    AbortController, crypto: globalThis.crypto, refreshIntents() {},
    conversationScroll: { policy: new ConversationScroll() }, input: { current: { focus() {} } }, appClient: { current: 'client' },
    frontendObservation: { prepareSubmission: (...args) => prepare(...args) },
    api: async (path, body) => { calls.push({ path, body: body === undefined ? undefined : structuredClone(body) }); if (path.endsWith('/messages')) ordinary.push(body); return transport(path, body); },
    fetch: async (path, init) => { uploads.push({ path, init }); return uploadTransport(path, init); },
    setVoiceRecovery() {}, setChooseFiles() {}, setPhase(value) { state.phase = value; },
    setHelpDraftRevision() {}, setSending(value) { state.sending = value; }, setUploading(value) { state.uploading = value; },
    setError(value) { errors.push(value); }, setSnapshot() {},
    currentGuard: () => scopeGuard(() => uiScope.current), submissionScope: () => JSON.stringify(uiScope.current),
    finishSubmission(ticket, isCurrent) { const revision = submissions.current.finish(ticket); if (revision !== null) submissions.current.commit(revision); if (isCurrent?.()) state.sending = false; },
    showDraft(draft) { state.attached = draft.attached; state.text = draft.text; },
    navigate(sid, migrate = false) { navigations.push(sid); if (migrate) environment.showDraft(drafts.current.move(state.user.id, state.session, sid)); state.session = sid; uiScope.current = { ...uiScope.current, session: sid, generation: uiScope.current.generation + 1 }; },
    requestLogin() { errors.push('login'); }, t: key => key, errorMessage: code => code,
  };
  const context = vm.createContext(environment); vm.runInContext(compiled, context);
  return { state, calls, ordinary, errors, uploads, fileInput, uiScope, drafts, navigations,
    receipt: (runId, index = 0) => receipt(ordinary[index], runId),
    render: () => context.bind({ ...state, attached: [...state.attached] }),
    draft: value => { const saved = drafts.current.write(state.user.id, state.session, value); environment.showDraft(saved); return saved; },
    read: (target = scope) => helpDrafts.current.read(target),
    write: (answers, target = scope) => helpDrafts.current.write(target, answers),
    reload: () => { helpDrafts.current = new HelpDraftStore(disk); },
    reloadIntents: () => { intents.current = new SubmissionIntents(disk); },
    transport: value => { transport = value; }, uploadTransport: value => { uploadTransport = value; },
    prepare: value => { prepare = value; },
    switch: (owner, session, request = help) => {
      state.user = owner ? { id: owner } : null; state.session = session; state.help = request;
      uiScope.current = { ...uiScope.current, owner, session, generation: uiScope.current.generation + 1 };
    },
  };
}

test('spoken partial answers survive hangup/reload and the real text handler completes the same request', async () => {
  const f = fixture();
  await f.render().submitVoice('Next Friday');
  assert.equal(f.calls.length, 0, 'A partial reply is retained locally, not submitted prematurely');
  f.reload();
  assert.deepEqual(f.render().helpAnswers, { date: 'Next Friday' });
  assert.equal(f.render().voiceQuestion.id, 'city');
  f.write({ ...f.read().answers, city: 'Kyoto' });
  await f.render().answerHelp(f.read().answers);
  assert.deepEqual(f.calls[0].body.answers, { date: 'Next Friday', city: 'Kyoto' });
  assert.deepEqual(f.read().answers, {});
});

test('typed answers guide speech to the remaining question, including whitespace-only fields', async () => {
  const f = fixture(); f.write({ date: 'Friday', city: '  ' });
  const current = f.render();
  assert.equal(current.voiceQuestion.id, 'city');
  await current.submitVoice('Kyoto');
  assert.deepEqual(f.calls[0].body.answers, { date: 'Friday', city: 'Kyoto' });
  assert.deepEqual(f.read().answers, {});
  assert.equal(f.ordinary.length, 0);
});

test('submission rereads the shared form so a newer typed edit is not overwritten by an old render', async () => {
  const f = fixture(), oldRender = f.render();
  f.write({ date: 'Typed while the microphone was listening' });
  await oldRender.submitVoice('Kyoto');
  assert.deepEqual(f.calls[0].body.answers, { date: 'Typed while the microphone was listening', city: 'Kyoto' });
});

test('a lost final voice receipt preserves all answers and never retries or interrupts on the next utterance', async () => {
  const f = fixture(); f.write({ date: 'Friday' });
  f.transport(async () => { throw new Error('response lost'); });
  await assert.rejects(f.render().submitVoice('Kyoto'), error => error.delivery === 'unknown');
  f.reload();
  assert.deepEqual(f.read().answers, { date: 'Friday', city: 'Kyoto' });
  assert.equal(f.render().voiceQuestion, undefined);
  await assert.rejects(f.render().submitVoice('Try again'), error => error.delivery === 'not_sent');
  assert.equal(f.calls.length, 1); assert.equal(f.ordinary.length, 0);
  f.transport(async (path, body) => body ? { id: help.id, status: 'answered' } : { cursor: 1 });
  await f.render().answerHelp(f.read().answers);
  assert.equal(f.calls.filter(call => call.body).length, 2, 'Only explicit Continue retries the idempotent answer endpoint');
  assert.deepEqual(f.read().answers, {});
});

test('a malformed voice receipt stays unknown, and an old receipt cannot clear later text or another scope', async () => {
  const f = fixture(); f.write({ date: 'Friday' });
  f.transport(async () => ({ id: help.id, status: 'pending' }));
  await assert.rejects(f.render().submitVoice('Kyoto'), error => error.delivery === 'unknown');
  assert.deepEqual(f.read().answers, { date: 'Friday', city: 'Kyoto' });
  f.write({ date: 'Saturday' });
  let resolve;
  f.transport(() => new Promise(done => { resolve = done; }));
  const pending = f.render().submitVoice('Osaka');
  f.write({ date: 'Sunday', city: 'Tokyo' });
  f.switch('bob', 'work'); f.write({ date: 'Monday' }, { owner: 'bob', session: 'work', request: 'trip' });
  resolve({ id: help.id, status: 'answered' }); await pending;
  assert.deepEqual(f.read().answers, { date: 'Sunday', city: 'Tokyo' });
  assert.deepEqual(f.render().helpAnswers, { date: 'Monday' });
  f.switch('alice', 'other'); assert.deepEqual(f.render().helpAnswers, {});
  f.switch('alice', 'travel', { ...help, id: 'new-request' }); assert.deepEqual(f.render().helpAnswers, {});
});

test('a single spoken answer with a lost receipt is retained for review after reload without touching typed input', async () => {
  const f = fixture(); f.state.help = { ...help, questions: [help.questions[1]] };
  const original = f.drafts.current.write('alice', 'travel', { text: 'A separate unfinished message', attached: [file('keep')] });
  f.transport(async () => { throw Error('no receipt'); });
  await assert.rejects(f.render().submitVoice('Kyoto'), error => error.delivery === 'unknown');
  f.reload();
  assert.deepEqual(f.render().helpAnswers, { city: 'Kyoto' });
  assert.equal(f.render().voiceQuestion, undefined);
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
  assert.equal(f.calls.length, 1); assert.equal(f.ordinary.length, 0);
});

test('upload limit rejection clears the native selection so the same file can be chosen after removing an attachment', async () => {
  const f = fixture();
  f.state.attached = Array.from({ length: 10 }, (_, index) => file(String(index)));
  f.drafts.current.write('alice', 'travel', { text: 'Keep this draft', attached: f.state.attached });
  const picked = { name: 'chosen.md', size: 100 };
  f.fileInput.current.value = picked.name;
  await f.render().upload([picked]);
  assert.equal(f.fileInput.current.value, ''); assert.equal(f.errors.at(-1), 'files.limit');
  assert.equal(f.uploads.length, 0); assert.equal(f.state.attached.length, 10);
  f.state.attached.pop();
  f.drafts.current.write('alice', 'travel', { text: 'Keep this draft', attached: f.state.attached });
  f.fileInput.current.value = picked.name;
  await f.render().upload([picked]);
  assert.equal(f.uploads.length, 1); assert.equal(f.state.attached.length, 10);
  assert.equal(f.drafts.current.read('alice', 'travel').text, 'Keep this draft');
});

test('oversized file rejection is repeatable and never clears already attached files', async () => {
  const f = fixture(), original = [file('valid')]; f.state.attached = original;
  const picked = { name: 'too-large.bin', size: 20 * 1024 * 1024 + 1 };
  for (let attempt = 0; attempt < 2; attempt++) {
    f.fileInput.current.value = picked.name; await f.render().upload([picked]);
    assert.equal(f.fileInput.current.value, ''); assert.equal(f.errors.at(-1), 'error.file_too_large');
    assert.deepEqual(f.state.attached, original);
  }
  assert.equal(f.errors.length, 2); assert.equal(f.uploads.length, 0);
});

test('upload copies the live FileList before clearing it and does not clear a later selection on completion', async () => {
  const f = fixture(), selection = [{ name: 'first.md', size: 12 }]; let inputValue = 'first.md', resolve;
  f.fileInput.current = { get value() { return inputValue; }, set value(value) { inputValue = value; if (!value) selection.splice(0); } };
  f.uploadTransport(() => new Promise(done => { resolve = done; }));
  const pending = f.render().upload(selection);
  assert.equal(selection.length, 0); assert.equal(f.uploads[0].init.body.name, 'first.md');
  f.fileInput.current.value = 'later.md';
  resolve({ ok: true, json: async () => file('first') }); await pending;
  assert.equal(f.fileInput.current.value, 'later.md'); assert.equal(f.state.attached[0].id, 'first');
});

test('Stop during first-session creation retains the new-chat draft and a late session response cannot migrate or send it', async () => {
  const f = fixture(); f.switch('alice', null, null);
  const original = f.draft({ text: 'Keep my first message', attached: [file('original')] });
  let resolve;
  f.transport((path) => { assert.equal(path, '/sessions'); return new Promise(done => { resolve = done; }); });
  const pending = f.render().submit();
  assert.equal(f.state.sending, true);
  await f.render().stop();
  assert.equal(f.state.sending, false);
  resolve({ id: 'created-late' }); await pending;
  assert.equal(f.state.session, null); assert.deepEqual(f.navigations, []);
  assert.deepEqual(f.drafts.current.read('alice', null), original);
  assert.deepEqual(f.drafts.current.read('alice', 'created-late').attached, []);
  assert.equal(f.calls.length, 1); assert.equal(f.ordinary.length, 0);
});

test('Stop while frontend preparation is pending prevents a messages POST and preserves the current draft', async () => {
  const f = fixture(); f.switch('alice', 'travel', null);
  const original = f.draft({ text: 'Wait for me', attached: [file('original')] });
  let resolve;
  f.prepare(() => new Promise(done => { resolve = done; }));
  const pending = f.render().submit();
  await ticks();
  await f.render().stop(); resolve('client'); await pending;
  assert.deepEqual(f.calls.map(call => call.path), ['/sessions/travel/stop']);
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
  assert.equal(f.state.phase, 'stopped'); assert.equal(f.state.sending, false);
});

test('a message receipt arriving after Stop cannot clear the preserved draft or files, including later edits', async () => {
  for (const editAfterStop of [false, true]) {
    const f = fixture(); f.switch('alice', 'travel', null);
    let expected = f.draft({ text: 'Original words', attached: [file('original')] }), resolve;
    f.transport((path, body) => path.endsWith('/messages') ? new Promise(done => { resolve = done; }) : Promise.resolve({ ok: true, client_stop_id: body.client_stop_id, submission_revision: 1 }));
    const pending = f.render().submit();
    await ticks();
    assert.equal(f.ordinary.length, 1);
    await f.render().stop();
    if (editAfterStop) expected = f.draft({ text: 'New words after stopping', attached: [file('original'), file('new')] });
    resolve(f.receipt('stopped-run')); await pending;
    assert.deepEqual(f.drafts.current.read('alice', 'travel'), expected);
    assert.deepEqual(f.state.attached, expected.attached); assert.equal(f.state.text, expected.text);
    assert.equal(f.state.sending, false); assert.equal(f.ordinary.length, 1);
  }
});

test('after Stop a new send owns its draft and lock even when the previous response arrives first', async () => {
  const f = fixture(); f.switch('alice', 'travel', null);
  f.draft({ text: 'Original', attached: [file('original')] });
  const responses = [];
  f.transport((path, body) => path.endsWith('/messages') ? new Promise(done => { responses.push(done); }) : Promise.resolve({ ok: true, client_stop_id: body.client_stop_id, submission_revision: 1 }));
  const first = f.render().submit();
  await ticks();
  await f.render().stop();
  const nextDraft = f.draft({ text: 'Explicit new message', attached: [file('original'), file('new')] });
  const second = f.render().submit();
  await ticks();
  responses[0](f.receipt('old')); await first;
  assert.equal(f.state.sending, true);
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), nextDraft);
  responses[1](f.receipt('new', 1)); await second;
  assert.equal(f.state.sending, false);
  assert.equal(f.drafts.current.read('alice', 'travel').text, '');
  assert.deepEqual(f.drafts.current.read('alice', 'travel').attached, []);
});

test('a lost Stop survives reload and blocks new sends until that exact Stop has a receipt', async () => {
  const f = fixture(); f.switch('alice', 'travel', null);
  const original = f.draft({ text: 'Only after the old work has stopped', attached: [file('keep')] });
  let confirmed = false;
  f.transport(async (path, body) => {
    if (path.endsWith('/stop')) throw new Error('Stop response lost');
    if (path.includes('/stops/')) return { found: confirmed, client_stop_id: path.split('/').at(-1), ...(confirmed ? { submission_revision: 1 } : {}) };
    if (path.endsWith('/messages')) return receipt(body);
    return { cursor: 100, submission_revision: 100 };
  });
  await f.render().stop();
  const stopped = f.calls.find(call => call.path.endsWith('/stop')).body.client_stop_id;
  assert.match(stopped, /^[A-Za-z0-9_-]+$/);
  f.reloadIntents();
  await f.render().submit();
  assert.equal(f.ordinary.length, 0);
  assert.equal(f.calls.filter(call => call.path === '/sessions/travel').length, 0, 'An unrelated snapshot revision cannot prove a delayed Stop finished');
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
  assert.equal(f.errors.at(-1), 'send.stopUnknown');
  confirmed = true;
  await f.render().submit();
  assert.equal(f.ordinary.length, 1);
  assert.equal(f.ordinary[0].expected_submission_revision, 1);
  assert.equal(f.calls.filter(call => call.path.endsWith('/stop')).length, 1, 'Recovery only reads the Stop receipt; it does not repeat Stop');
  assert.ok(f.calls.filter(call => call.path.includes('/stops/')).every(call => call.path.endsWith(stopped)));
  assert.equal(f.drafts.current.read('alice', 'travel').text, '');
  assert.deepEqual(f.drafts.current.read('alice', 'travel').attached, []);
});

test('an uncertain original message keeps its identity when a single-question help request appears before retry', async () => {
  const f = fixture(); f.switch('alice', 'travel', null);
  const original = f.draft({ text: 'Compare the two options', attached: [file('comparison')] });
  let responseLost = true;
  f.transport(async (path, body) => {
    if (path.includes('/submissions/')) return { found: false, client_submission_id: path.split('/').at(-1), submission_revision: 0 };
    assert.ok(path.endsWith('/messages'), 'Retry must not convert the original task into a help answer');
    if (responseLost) throw new Error('First response lost');
    return receipt(body, 'original-run');
  });
  await f.render().submit();
  assert.deepEqual(f.drafts.current.read('alice', 'travel'), original);
  assert.equal(f.ordinary.length, 1);
  const first = structuredClone(f.ordinary[0]);
  f.state.help = { ...help, questions: [help.questions[0]] };
  f.write({ date: 'Separate answer still being drafted' });
  responseLost = false;
  await f.render().submit();
  assert.equal(f.ordinary.length, 2);
  assert.deepEqual(f.ordinary[1], first, 'Explicit retry must retain the same submission ID, payload and fence');
  assert.equal(f.calls.filter(call => call.path.includes('/help/')).length, 0);
  assert.deepEqual(f.read().answers, { date: 'Separate answer still being drafted' });
  assert.equal(f.drafts.current.read('alice', 'travel').text, '');
  assert.deepEqual(f.drafts.current.read('alice', 'travel').attached, []);
});

for (const entry of ['single-question composer', 'help form', 'completed spoken answers']) {
  test(`an unconfirmed Stop blocks ${entry} without losing answers or the typed draft`, async () => {
    const f = fixture();
    if (entry === 'single-question composer') f.state.help = { ...help, questions: [help.questions[0]] };
    const draft = f.draft({ text: entry === 'single-question composer' ? 'Next Friday' : 'Separate unfinished message', attached: [file('keep')] });
    const answers = entry === 'completed spoken answers' ? { date: 'Friday' }
      : entry === 'help form' ? { date: 'Friday', city: 'Tokyo' } : { date: 'Unsubmitted form answer' };
    f.write(answers);
    f.transport(async path => {
      if (path.endsWith('/stop')) throw new Error('Stop response lost');
      if (path.includes('/stops/')) return { found: false, client_stop_id: path.split('/').at(-1) };
      throw new Error('No task or help answer may dispatch while Stop is unconfirmed');
    });
    await f.render().stop();
    const stopId = f.calls.find(call => call.path.endsWith('/stop')).body.client_stop_id;
    const before = f.calls.length;
    if (entry === 'single-question composer') await f.render().submit();
    else if (entry === 'help form') await f.render().answerHelp(f.read().answers);
    else await assert.rejects(f.render().submitVoice('Tokyo'), error => error.delivery === 'not_sent');
    assert.deepEqual(f.calls.slice(before), [{ path: `/sessions/travel/stops/${stopId}`, body: undefined }], 'Only the exact Stop receipt may be queried');
    assert.equal(f.calls.filter(call => call.path.includes('/help/')).length, 0);
    assert.equal(f.ordinary.length, 0);
    assert.deepEqual(f.drafts.current.read('alice', 'travel'), draft);
    assert.deepEqual(f.state.attached, draft.attached); assert.equal(f.state.text, draft.text);
    f.reload();
    assert.deepEqual(f.read().answers, entry === 'completed spoken answers' ? { date: 'Friday', city: 'Tokyo' } : answers);
    assert.equal(f.state.sending, false);
  });
}
