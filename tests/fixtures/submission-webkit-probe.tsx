// Real production App and WebKit; entirely synthetic account/API/storage.
// Native reload recreates the document and module graph. Only this fixture's
// fake server and browser-storage values survive, never App/React closures.
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '../../src/App';
import { I18nProvider } from '../../src/i18n';

const state = window.__submissionFixture;
state.results ||= {};
state.requests ||= [];
state.sessionStorage ||= {};
state.localStorage ||= { 'athena-language': 'en' };
state.stage ||= 'initial';
const storage = values => ({
  getItem: key => values[key] ?? null,
  setItem: (key, value) => { if (state.storageUnavailable) throw new Error('synthetic storage unavailable'); values[key] = String(value); },
  removeItem: key => { if (state.storageUnavailable) throw new Error('synthetic storage unavailable'); delete values[key]; },
});
Object.defineProperty(window, 'sessionStorage', { value: storage(state.sessionStorage) });
Object.defineProperty(window, 'localStorage', { value: storage(state.localStorage) });
let identifier = 0;
if (!crypto.randomUUID) Object.defineProperty(crypto, 'randomUUID', { value: () => `00000000-0000-4000-8000-${String(Date.now() + ++identifier).slice(-12)}` });
const report = value => window.webkit.messageHandlers.probe.postMessage(value);
const delay = (ms = 40) => new Promise(resolve => setTimeout(resolve, ms));
const until = async (read, name) => {
  const deadline = performance.now() + 6000;
  do { if (read()) return; await delay(); } while (performance.now() < deadline);
  throw new Error(`timeout:${name}`);
};
const check = (name, pass, detail = undefined) => {
  state.results[name] = { pass: Boolean(pass), detail }; report({ progress: name });
  if (!pass) throw new Error(name);
};
const capture = name => new Promise((resolve, reject) => {
  const done = event => {
    clearTimeout(timer); window.removeEventListener('probe-captured', done);
    event.detail?.error ? reject(new Error(event.detail.error)) : resolve();
  };
  const timer = setTimeout(() => { window.removeEventListener('probe-captured', done); reject(new Error(`capture timeout:${name}`)); }, 5000);
  window.addEventListener('probe-captured', done); report({ capture: name });
});
async function resize(width, height) {
  report({ resize: { width, height } });
  await until(() => innerWidth === width && innerHeight === height, 'native viewport resize');
  await delay(180);
}
function visibleGeometry(selector) {
  const element = document.querySelector(selector), box = element?.getBoundingClientRect();
  if (!box) return { visible: false, selector };
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return { selector, left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height,
    visible: box.width > 0 && box.height > 0 && box.left >= -1 && box.right <= innerWidth + 1
      && box.top >= -1 && box.bottom <= innerHeight + 1 && element.contains(hit) };
}
const sources = [];
class Source {
  static CLOSED = 2;
  readyState = 1; onopen; onmessage; onerror; url;
  constructor(url) { this.url = url; sources.push(this); setTimeout(() => this.onopen?.(), 0); }
  addEventListener() {} removeEventListener() {} close() { this.readyState = 2; }
}
window.EventSource = Source;
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const emit = session => {
  const snapshot = state.sessions[session]; snapshot.cursor++;
  for (const source of sources.filter(item => item.readyState === 1 && item.url.startsWith(`/api/sessions/${session}/events`))) {
    source.onmessage?.({ data: JSON.stringify({ seq: snapshot.cursor, type: 'refresh', payload: {} }) });
  }
};
const edit = async text => {
  const input = document.querySelector('.composer textarea');
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, text);
  input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
  await delay();
};
const send = () => document.querySelector('.composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
const reload = stage => { state.stage = stage; report({ reload: state }); };

state.owner ||= 'probe';
state.sessions ||= Object.fromEntries(['s1', 's2'].map(id => [id, { cursor: 1, submission_revision: 0,
  messages: [], runs: [], components: [], files: [], tasks: [], help_requests: [] }]));
state.receipts ||= {};
state.stopReceipts ||= {};
state.delayedStops ||= [];
state.acceptances ||= 0;
state.postOutcome ||= 'normal';
if (state.stage === 'initial') state.sessionStorage['athena-session:probe'] = 's1';
const fingerprint = payload => JSON.stringify([payload.text, payload.mode, payload.file_ids, payload.expected_submission_revision]);
let releasePost, releaseLookup;
function applyStop(owner, session, identifier) {
  const key = `${owner}:${session}:${identifier}`, existing = state.stopReceipts[key];
  if (existing) return existing;
  const snapshot = state.sessions[session];
  snapshot.submission_revision++;
  for (const run of snapshot.runs) if (['queued', 'running'].includes(run.status)) run.status = 'cancelled';
  const receipt = { ok: true, client_stop_id: identifier, submission_revision: snapshot.submission_revision };
  state.stopReceipts[key] = receipt; emit(session); return receipt;
}
window.fetch = async (url, options) => {
  const path = String(url), method = options?.method || 'GET';
  const payload = options?.body ? JSON.parse(options.body) : undefined;
  state.requests.push({ path, method, owner: state.owner, ...(payload ? { payload } : {}) });
  if (path === '/api/auth/me') return response({ user: { id: state.owner, username: state.owner, developer_mode: false }, configured: true, has_users: true });
  if (path.startsWith('/api/service')) return response({ configured: true });
  if (path === '/api/sessions') return response(Object.keys(state.sessions).map(id => ({ id, title: id === 's1' ? 'Original task' : 'Another task', pending: 0 })));
  if (path.startsWith('/api/activity')) return response({ owner_id: state.owner, items: [], has_more: false });
  if (path === '/api/frontend/observation' || path.endsWith('/report')) return response({ accepted: true });
  if (path === '/api/voice/status') return response({ asr: false, tts: false });
  const session = /^\/api\/sessions\/([^/?]+)/.exec(path)?.[1], snapshot = state.sessions[session];
  if (snapshot && path === `/api/sessions/${session}`) {
    const value = structuredClone(snapshot);
    if (state.staleSnapshotRevision !== undefined) value.submission_revision = state.staleSnapshotRevision;
    return response(value);
  }
  const identifier = /^\/api\/sessions\/[^/]+\/submissions\/([^/?]+)/.exec(path)?.[1];
  if (snapshot && identifier) {
    if (state.holdLookup) await new Promise(resolve => { releaseLookup = resolve; });
    const accepted = state.receipts[`${state.owner}:${session}:${identifier}`];
    return response(accepted ? { found: true, ...accepted.receipt, status: snapshot.runs.find(run => run.id === accepted.receipt.run_id).status,
      submission_revision: snapshot.submission_revision, replayed: true } : { found: false, client_submission_id: identifier, submission_revision: snapshot.submission_revision });
  }
  const stopIdentifier = /^\/api\/sessions\/[^/]+\/stops\/([^/?]+)/.exec(path)?.[1];
  if (snapshot && stopIdentifier) {
    const receipt = state.stopReceipts[`${state.owner}:${session}:${stopIdentifier}`];
    return response(receipt ? { found: true, client_stop_id: stopIdentifier, submission_revision: receipt.submission_revision }
      : { found: false, client_stop_id: stopIdentifier });
  }
  if (snapshot && path.endsWith('/messages') && method === 'POST') {
    if (!payload.client_submission_id || !Number.isSafeInteger(payload.expected_submission_revision)) return response({ code: 'invalid_submission_intent' }, 422);
    const key = `${state.owner}:${session}:${payload.client_submission_id}`, existing = state.receipts[key];
    if (existing) return fingerprint(payload) !== existing.fingerprint ? response({ code: 'submission_conflict' }, 409)
      : response({ ...existing.receipt, status: snapshot.runs.find(run => run.id === existing.receipt.run_id).status, submission_revision: snapshot.submission_revision, replayed: true });
    if (payload.expected_submission_revision !== snapshot.submission_revision) return response({ code: 'submission_revision_conflict' }, 409);
    if (state.postOutcome === 'drop-before') { state.postOutcome = 'normal'; throw new TypeError('synthetic response lost before acceptance'); }
    if (payload.mode === 'interrupt') { snapshot.submission_revision++; for (const run of snapshot.runs) if (['queued', 'running'].includes(run.status)) run.status = 'cancelled'; }
    const run = { id: `run${++state.acceptances}`, status: 'running', phase: 'thinking', created_at: '2026-10-05T00:00:00Z' };
    snapshot.runs.push(run);
    snapshot.messages.push({ id: `message${state.acceptances}`, role: 'user', content: payload.text, run_id: run.id, status: 'complete', attachments: [] });
    snapshot.cursor++;
    const receipt = { accepted: true, run_id: run.id, status: run.status, client_submission_id: payload.client_submission_id, submission_revision: snapshot.submission_revision, replayed: false };
    state.receipts[key] = { fingerprint: fingerprint(payload), receipt, frontend_client_id: payload.frontend_client_id };
    emit(session);
    if (state.postOutcome === 'drop-after') { state.postOutcome = 'normal'; throw new TypeError('synthetic response lost after acceptance'); }
    if (state.postOutcome === 'hold-after') { state.postOutcome = 'normal'; await new Promise(resolve => { releasePost = resolve; }); }
    return response(receipt);
  }
  if (snapshot && path.endsWith('/stop')) {
    if (!payload.client_stop_id) return response({ code: 'invalid_stop_intent' }, 422);
    if (state.stopOutcome === 'drop-before') {
      state.stopOutcome = 'normal'; state.delayedStops.push({ owner: state.owner, session, id: payload.client_stop_id });
      throw new TypeError('synthetic Stop connection lost before handler');
    }
    const receipt = applyStop(state.owner, session, payload.client_stop_id);
    if (state.loseStopResponse) { state.loseStopResponse = false; throw new TypeError('synthetic Stop response lost'); }
    return response(receipt);
  }
  if (snapshot && path.includes('/trace')) return response({ run: { status: 'running' }, entries: [], total: 0, next_after: 0, has_more: false });
  throw new Error(`unhandled synthetic request: ${method} ${path}`);
};
const posts = () => state.requests.filter(request => request.method === 'POST' && request.path.endsWith('/messages'));
const receiptReads = () => state.requests.filter(request => request.method === 'GET' && request.path.includes('/submissions/'));
const stopReads = () => state.requests.filter(request => request.method === 'GET' && request.path.includes('/stops/'));
const stopPosts = () => state.requests.filter(request => request.method === 'POST' && request.path.endsWith('/stop'));
const root = createRoot(document.getElementById('root'), {
  onUncaughtError: error => report({ react_error: String(error), stack: error.stack }),
});
root.render(<I18nProvider><App /></I18nProvider>);

const pending = () => document.querySelector('[data-submission-recovery]');
const input = () => document.querySelector('.composer textarea');
const stop = () => {
  const control = document.querySelector('.composer button.stop');
  if (!control) throw new Error('Stop control missing');
  control.click();
};
const retry = () => {
  const control = document.querySelector('[data-submission-retry]');
  if (!control || control.disabled) throw new Error('Explicit retry unavailable');
  control.click();
};
async function navigate(title) {
  document.querySelector('button[aria-label="Menu"]').click(); await delay();
  [...document.querySelectorAll('.menu-items button')].find(button => button.textContent === 'Previous conversations').click();
  await until(() => [...document.querySelectorAll('.session')].some(button => button.textContent.includes(title)), 'conversation history');
  [...document.querySelectorAll('.session')].find(button => button.textContent.includes(title)).click(); await delay(100);
}
async function ready() {
  await until(() => !document.hidden && input() && state.requests.some(request => request.path === `/api/sessions/${state.owner === 'probe' ? 's1' : 's2'}`), 'visible loaded conversation');
  await delay(100);
}
async function run() {
  try {
    await ready();
    if (state.stage === 'initial') {
      state.postOutcome = 'drop-after'; state.holdLookup = true;
      await edit('One deliberate task'); send();
      await until(() => pending() && state.acceptances === 1, 'uncertain accepted send');
      check('lost_receipt_retains_an_explicit_recovery_path', posts().length === 1 && input().value === 'One deliberate task');
      await capture('message-pending-desktop');
      state.holdLookup = false; state.postsBeforeReload = posts().length; state.readsBeforeReload = receiptReads().length;
      reload('recover-accepted'); return;
    }
    if (state.stage === 'recover-accepted') {
      await until(() => receiptReads().length > state.readsBeforeReload && !pending() && input().value === '', 'receipt recovered after full document reload');
      check('reload_recovers_accepted_receipt_without_reposting', posts().length === state.postsBeforeReload && state.acceptances === 1);
      state.postOutcome = 'hold-after';
      await edit('Original words before editing'); send();
      await until(() => typeof releasePost === 'function' && state.acceptances === 2, 'accepted delayed response');
      await edit('Newer words must survive'); releasePost();
      await until(() => !document.querySelector('.composer button[type="submit"]')?.disabled, 'late receipt settled');
      check('late_receipt_never_erases_a_newer_draft', input().value === 'Newer words must survive' && state.acceptances === 2);
      state.postOutcome = 'drop-before';
      await edit('Repeatable words'); send();
      await until(() => pending() && posts().length === 3, 'missing receipt retains pending send');
      const frozen = posts().at(-1).payload, postCount = posts().length;
      await navigate('Another task');
      check('pending_send_is_not_shown_in_another_conversation', !pending() && input().value === '');
      await navigate('Original task');
      await until(() => pending(), 'return to pending conversation');
      await delay(100);
      check('returning_to_chat_only_checks_receipt', posts().length === postCount && state.acceptances === 2);
      retry();
      await until(() => state.acceptances === 3 && !pending(), 'explicit same-identity retry accepted');
      check('explicit_retry_reuses_frozen_identity_and_payload', posts().length === 4 && posts().at(-1).payload.client_submission_id === frozen.client_submission_id
        && fingerprint(posts().at(-1).payload) === fingerprint(frozen));
      await edit('Repeatable words'); send();
      await until(() => state.acceptances === 4 && input().value === '', 'new deliberate same-text intent');
      check('same_text_new_intention_is_allowed', posts().at(-1).payload.client_submission_id !== frozen.client_submission_id && state.sessions.s1.messages.filter(message => message.content === 'Repeatable words').length === 2);
      state.postOutcome = 'drop-after'; state.holdLookup = true;
      await edit('Accepted task that will be stopped'); send();
      await until(() => pending() && state.acceptances === 5, 'uncertain task before stop');
      const stoppedRun = state.sessions.s1.runs.at(-1).id, beforeStop = posts().length;
      stop(); await until(() => state.sessions.s1.submission_revision === 1, 'stop revision');
      state.holdLookup = false; releaseLookup?.(); await delay(120);
      const checkControl = document.querySelector('[data-submission-check]'); if (checkControl) checkControl.click();
      await delay(120);
      check('stop_does_not_replay_an_old_accepted_send', posts().length === beforeStop && state.acceptances === 5 && state.sessions.s1.runs.find(run => run.id === stoppedRun).status === 'cancelled');
      state.staleSnapshotRevision = 0; emit('s1'); await delay(100);
      await edit('New work after Stop'); send();
      await until(() => state.acceptances === 6 && input().value === '', 'new post-Stop intent with acknowledged revision');
      check('stale_snapshot_cannot_undo_stop_revision', posts().at(-1).payload.expected_submission_revision === 1);
      delete state.staleSnapshotRevision;
      await until(() => document.querySelector('.composer button.stop'), 'new task Stop control');
      const readsBeforeStop = stopReads().length;
      state.loseStopResponse = true; stop(); await until(() => state.sessions.s1.submission_revision === 2, 'unknown Stop accepted');
      await edit('New work after uncertain Stop'); send();
      await until(() => state.acceptances === 7, 'authoritative revision before fresh send');
      check('lost_stop_receipt_is_reconciled_before_new_send', posts().at(-1).payload.expected_submission_revision === 2
        && stopReads().length > readsBeforeStop);
      state.postOutcome = 'drop-after'; state.holdLookup = true;
      await edit('Private pending task for first account'); send();
      await until(() => pending() && state.acceptances === 8, 'private pending send');
      state.holdLookup = false; state.owner = 'other'; state.sessionStorage['athena-session:other'] = 's2';
      state.requestsBeforeOwnerChange = state.requests.length;
      reload('other-account'); return;
    }
    if (state.stage === 'other-account') {
      await delay(180);
      const newRequests = state.requests.slice(state.requestsBeforeOwnerChange);
      check('another_account_never_reads_or_displays_previous_recovery', !newRequests.some(request => request.path.includes('/submissions/'))
        && !pending() && input().value === '' && !document.body.innerText.includes('Private pending task'));
      state.storageUnavailable = true; state.postOutcome = 'drop-before';
      await edit('Recover inside this page without storage'); send();
      await until(() => pending(), 'page-only recovery with unavailable storage');
      const frozen = posts().at(-1).payload;
      retry(); await until(() => !pending() && state.acceptances === 9, 'page-only explicit retry');
      check('restricted_storage_preserves_page_only_same_identity_retry', posts().at(-1).payload.client_submission_id === frozen.client_submission_id && input().value === '');
      check('accepted_messages_have_unique_submission_receipts', Object.keys(state.receipts).length === 9 && Object.values(state.sessions).reduce((count, session) => count + session.messages.length, 0) === 9);
      state.storageUnavailable = false;
      state.stopOutcome = 'drop-before';
      await until(() => document.querySelector('.composer button.stop'), 'active task before missing Stop');
      stop(); await until(() => document.querySelector('[data-stop-recovery]'), 'missing Stop recovery');
      state.missingStop = state.delayedStops.at(-1); state.messagesBeforeMissingStop = posts().length;
      const checksBeforeSend = stopReads().length;
      await edit('New draft after Stop that never reached the server'); send();
      await until(() => stopReads().length > checksBeforeSend, 'send verifies exact missing Stop'); await delay(120);
      check('missing_stop_receipt_blocks_new_send_despite_old_snapshot', state.sessions.s2.submission_revision === 0
        && posts().length === state.messagesBeforeMissingStop && input().value === 'New draft after Stop that never reached the server'
        && Boolean(document.querySelector('[data-stop-recovery]')));
      const stopWarning = document.querySelector('[data-stop-recovery] > p').innerText;
      const visibleWarnings = [...document.querySelectorAll('.notice')].filter(element => element.getClientRects().length
        && element.innerText.includes(stopWarning));
      check('unconfirmed_stop_shows_one_actionable_warning', stopWarning.length > 0 && visibleWarnings.length === 1,
        { visibleWarnings: visibleWarnings.length });
      await capture('stop-pending-desktop');
      await resize(390, 844);
      const geometry = ['.composer', '.composer textarea', '[data-stop-recovery]', '[data-stop-check]', '[data-stop-retry]'].map(visibleGeometry);
      check('narrow_pending_stop_preserves_visible_composer_and_recovery', geometry.every(item => item.visible), geometry);
      const notice = document.querySelector('[data-stop-recovery]');
      check('narrow_pending_stop_has_no_horizontal_overflow', document.documentElement.scrollWidth <= innerWidth
        && document.body.scrollWidth <= innerWidth && notice.scrollWidth <= notice.clientWidth + 1,
      { viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth,
        notice: notice.clientWidth, noticeScroll: notice.scrollWidth });
      await capture('stop-pending-narrow');
      await resize(1080, 780);
      state.stopPostsBeforeReload = stopPosts().length; state.stopReadsBeforeReload = stopReads().length;
      reload('stop-not-received'); return;
    }
    if (state.stage === 'stop-not-received') {
      await until(() => stopReads().length > state.stopReadsBeforeReload && document.querySelector('[data-stop-recovery]'), 'missing Stop survives document reload');
      check('reload_checks_exact_stop_receipt_without_resending_stop_or_message', stopPosts().length === state.stopPostsBeforeReload
        && posts().length === state.messagesBeforeMissingStop && input().value === 'New draft after Stop that never reached the server');
      const checksBeforeSend = stopReads().length;
      send(); await until(() => stopReads().length > checksBeforeSend, 'reloaded draft blocked by missing Stop'); await delay(120);
      check('missing_stop_after_reload_keeps_new_draft_unsubmitted', posts().length === state.messagesBeforeMissingStop
        && input().value === 'New draft after Stop that never reached the server');
      document.querySelector('[data-stop-retry]').click();
      await until(() => !document.querySelector('[data-stop-recovery]') && state.sessions.s2.submission_revision === 1, 'explicit original Stop retry');
      check('explicit_stop_retry_reuses_original_identity', stopPosts().at(-1).payload.client_stop_id === state.missingStop.id
        && stopPosts().length === state.stopPostsBeforeReload + 1);
      send(); await until(() => state.acceptances === 10 && input().value === '', 'new task after confirmed Stop');
      const later = state.sessions.s2.runs.at(-1), count = stopPosts().length;
      // The original request was delayed upstream even though its browser
      // connection failed. Its eventual arrival resolves the same receipt.
      applyStop(state.missingStop.owner, state.missingStop.session, state.missingStop.id); await delay(120);
      check('late_original_stop_does_not_cancel_newer_work_after_same_id_retry', later.status === 'running'
        && state.sessions.s2.submission_revision === 1 && state.acceptances === 10 && stopPosts().length === count
        && posts().at(-1).payload.expected_submission_revision === 1);
    }
  } catch (error) {
    state.results.error = String(error);
    state.results.diagnostic = { stage: state.stage, acceptances: state.acceptances, requests: state.requests,
      text: document.body.innerText, input: input()?.value, storage: state.sessionStorage };
  }
  report(state.results);
}
void run();
