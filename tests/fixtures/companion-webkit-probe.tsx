// Real production App/WebKit, synthetic account, transport and native scene receipts.
// No user data, external network, provider calls or installed Athena application.
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '../../src/App';
import { I18nProvider } from '../../src/i18n';

window.webkit.messageHandlers.probe.postMessage({ progress: 'companion fixture boot' });
const delay = (ms = 40) => new Promise(resolve => setTimeout(resolve, ms));
const store = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) }; };
Object.defineProperty(window, 'sessionStorage', { value: store() });
Object.defineProperty(window, 'localStorage', { value: store() });
sessionStorage.setItem('athena-session:probe', 's1');
localStorage.setItem('athena-language', 'en');
let uuid = 0;
if (!crypto.randomUUID) Object.defineProperty(crypto, 'randomUUID', { value: () => '00000000-0000-4000-8000-' + String(++uuid).padStart(12, '0') });
const sources = [];
class Source {
  static CLOSED = 2; readyState = 1; onopen; onmessage; onerror; url;
  constructor(url) { this.url = url; sources.push(this); setTimeout(() => this.onopen?.(), 0); }
  addEventListener() {} removeEventListener() {} close() { this.readyState = 2; }
}
window.EventSource = Source;
const emptySnapshot = () => ({ cursor: 1, submission_revision: 0, components: [], messages: [], runs: [], files: [], tasks: [], help_requests: [] });
let snapshot = emptySnapshot();
const secondSnapshot = emptySnapshot();
const observations = [], calls = [], unexpected = [], nativePosts = [], results = {};
const receipts = new Map(), stops = new Map();
const appActions = new Map(), actionReceipts = new Map(), previewRequests = [];
const resumeEntries = { 'athena-session:probe': 's1' };
let resumeSequence = 0;
let failNext = false, failNextPreview = false, presented = true, nativeComponents = [], backgroundEvents = 0, signedIn = false, nativeScope = '', activityItems = [];
window.addEventListener('athena:background', () => backgroundEvents++);
const dispatch = detail => window.dispatchEvent(new CustomEvent('athena:desktop', { detail }));
const nativeState = () => ({ platform: 'macos', supported: true, presented,
  screens: [{ id: '1', frame: { x: 0, y: 0, width: 1512, height: 982 }, visible_frame: { x: 0, y: 25, width: 1512, height: 930 }, scale: 2 }], components: nativeComponents });
const publishNative = () => dispatch({ action: 'state', scope: nativeScope, desktop: nativeState() });
window.addEventListener('probe-native-message', event => {
  const body = event.detail; nativePosts.push(body);
  if (body.action === 'resize') window.webkit.messageHandlers.probe.postMessage({ resize: {
    width: 560, height: Math.min(680, Math.max(260, typeof body.height === 'number' ? body.height : body.mode === 'expanded' ? 680 : 260)),
  } });
  if (body.action === 'observe') publishNative();
  if (body.action === 'hide') { presented = false; dispatch({ action: 'hidden' }); publishNative(); }
  if (body.action === 'scene') {
    nativeScope = body.scope;
    nativeComponents = body.items.map(item => ({ id: item.id, version: item.version, view_revision: item.view_revision,
      state: item.hidden ? 'hidden' : 'mounted', visible: presented && !item.hidden,
      rect: { x: 100, y: 60, width: item.width || 200, height: item.height || 100 }, screen_id: '1' }));
    publishNative();
  }
});
window.fetch = async (path, init) => {
  path = String(path); const body = init?.body ? JSON.parse(init.body) : undefined;
  if (body) calls.push({ path, body });
  let value, status = 200;
  if (path === '/api/auth/me') value = { user: signedIn ? { id: 'probe', username: 'synthetic', developer_mode: false } : null, configured: true, has_users: true };
  else if (path === '/api/auth/login') { signedIn = true; value = { user: { id: 'probe', username: 'synthetic', developer_mode: false } }; }
  else if (path === '/api/desktop/resume/claim') { resumeSequence = 0; value = { writer: body.writer, sequence: 0, entries: { ...resumeEntries } }; }
  else if (path === '/api/desktop/resume/save') {
    for (const [key, entry] of Object.entries(body.changes)) { if (entry === null) delete resumeEntries[key]; else resumeEntries[key] = entry; }
    resumeSequence = body.sequence; value = { writer: body.writer, sequence: resumeSequence };
  }
  else if (path.startsWith('/api/service')) value = { configured: true };
  else if (path === '/api/sessions') value = body ? { id: 's2' } : [{ id: 's1', title: 'Organize my documents', pending: 0 }, { id: 's2', title: 'Another task', pending: 0 }];
  else if (path === '/api/sessions/s1') value = structuredClone(snapshot);
  else if (path === '/api/sessions/s2') value = structuredClone(secondSnapshot);
  else if (path.startsWith('/api/activity')) value = { owner_id: 'probe', items: structuredClone(activityItems), has_more: false };
  else if (path === '/api/frontend/observation') { observations.push(body); value = { accepted: true }; }
  else if (path.includes('/app-actions/') && path.endsWith('/claim')) {
    const id = path.split('/').at(-2); value = { accepted: !actionReceipts.has(id), request: appActions.get(id) };
  }
  else if (path.includes('/app-actions/') && path.endsWith('/ack')) {
    const id = path.split('/').at(-2); actionReceipts.set(id, body); value = { completed: true };
  }
  else if (path.startsWith('/api/files/report/preview')) {
    previewRequests.push(path);
    if (failNextPreview) { failNextPreview = false; status = 503; value = { error: 'Synthetic preview unavailable' }; }
    else value = { kind: 'markdown', content: path.includes('revision=revision-three') ? '# Recovered notes\nPreview recovered in place.' : path.includes('revision=revision-two') ? '# Updated notes\nCanonical file revision.' : '# Original notes\nInitial file revision.', truncated: false };
  }
  else if (path.startsWith('/api/files/provided-invoice/preview')) value = { kind: 'markdown', content: '# Selected invoice\nKeep this attachment accessible.', truncated: false };
  else if (path === '/api/sessions/s1/components/reader/view') {
    const component = snapshot.components.find(item => item.id === 'reader');
    component.view = { ...component.view, ...body.view }; component.view_revision++;
    value = { version: component.version, view: component.view, view_revision: component.view_revision }; setTimeout(emit, 0);
  }
  else if (path === '/api/reset') { snapshot.components = []; value = { ok: true }; setTimeout(emit, 0); }
  else if (path.endsWith('/report')) value = { accepted: true };
  else if (path.endsWith('/messages')) {
    if (failNext) { failNext = false; status = 503; value = { error: 'Synthetic service unavailable', code: 'service_unavailable' }; }
    else {
      const target = path.startsWith('/api/sessions/s2/') ? secondSnapshot : snapshot;
      if (body.expected_submission_revision !== target.submission_revision || !body.frontend_client_id) throw new Error('Unbound or stale submission');
      const run = 'r' + (receipts.size + 1), queued = target.runs.some(item => item.status === 'running');
      value = { accepted: true, run_id: run, status: 'queued', client_submission_id: body.client_submission_id,
        submission_revision: target.submission_revision, replayed: false };
      receipts.set(body.client_submission_id, value);
      target.messages.push({ id: 'u' + receipts.size, role: 'user', content: body.text, status: queued ? 'queued' : 'complete', run_id: run });
      target.runs.unshift({ id: run, status: queued ? 'queued' : 'running', phase: 'executing' });
    }
  }
  else if (path.includes('/submissions/')) {
    const id = path.split('/').at(-1), receipt = receipts.get(id);
    value = receipt ? { ...receipt, found: true, replayed: true, submission_revision: snapshot.submission_revision }
      : { found: false, client_submission_id: id, submission_revision: snapshot.submission_revision };
  }
  else if (path.endsWith('/stop')) {
    value = { ok: true, client_stop_id: body.client_stop_id, submission_revision: ++snapshot.submission_revision };
    stops.set(body.client_stop_id, value); snapshot.runs = snapshot.runs.map(run => ({ ...run, status: 'interrupted' }));
  }
  else if (path === '/api/sessions/s1/help/h1/answer') { value = { id: 'h1', status: 'answered' }; snapshot.help_requests = []; snapshot.runs = [{ id: 'r1', status: 'complete', phase: 'responding' }]; setTimeout(emit, 0); }
  else if (path.includes('/stops/')) { const id = path.split('/').at(-1); value = stops.has(id) ? { ...stops.get(id), found: true } : { found: false, client_stop_id: id }; }
  else if (path === '/api/sessions/s1/runs/reminder-run/trace?after=0&limit=50') {
    const trigger = snapshot.messages.find(item => item.id === 'reminder-trigger');
    value = { run: { status: 'complete' }, entries: [{ ...trigger, seq: 1, created_at: '2026-10-09', content_chars: trigger.content.length,
      reasoning: '', reasoning_chars: 0, tool_calls: '', tool_calls_chars: 0 }], total: 1, next_after: 1, has_more: false };
  }
  else if (path.includes('/trace')) value = { run: snapshot.runs[0] || { status: 'complete' }, entries: [], total: 0, next_after: 0, has_more: false };
  else if (path === '/api/voice/status') value = { asr: false, tts: false };
  else { unexpected.push(path); value = {}; }
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
};
const emit = () => { snapshot.cursor++; for (const source of sources.filter(item => item.readyState === 1 && item.url.startsWith('/api/sessions/s1/events'))) source.onmessage?.({ data: JSON.stringify({ seq: snapshot.cursor, type: 'component', payload: {} }) }); };
const applyAction = async (action, payload) => {
  const request = { id: 'action-' + (appActions.size + 1), session_id: 's1', action, payload, status: 'pending' };
  appActions.set(request.id, request); snapshot.app_actions = [request]; emit();
  await until(() => actionReceipts.has(request.id), 'app action ' + action);
  snapshot.app_actions = [];
  return actionReceipts.get(request.id);
};
const until = async (read, name) => { const deadline = performance.now() + 6000; do { if (read()) return; await delay(); } while (performance.now() < deadline); throw new Error(`timeout:${name}`); };
const check = (name, pass, detail = undefined) => { window.webkit.messageHandlers.probe.postMessage({ progress: name }); results[name] = { pass: Boolean(pass), detail }; if (!pass) throw new Error(name); };
const capture = async name => {
  const captured = new Promise((resolve, reject) => window.addEventListener('probe-captured', event => event.detail?.error ? reject(new Error(event.detail.error)) : resolve(undefined), { once: true }));
  window.webkit.messageHandlers.probe.postMessage({ capture: name }); await captured;
};
const input = () => document.querySelector('.composer textarea');
const result = () => document.querySelector('.companion-result:not([hidden])');
const visibleMessage = id => result()?.querySelector(`[data-message-id="${id}"]`);
const escape = (composing = false) => document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true, isComposing: composing }));
const edit = async text => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input(), text); input().dispatchEvent(new Event('input', { bubbles: true })); await delay(70); };
const send = () => document.querySelector('.composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
const nativeClick = async element => { const bounds = element.getBoundingClientRect(); window.webkit.messageHandlers.probe.postMessage({ nativeClick: { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 } }); await delay(100); };
const nativeShow = () => { presented = true; dispatch({ action: 'show' }); publishNative(); };
const inside = element => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight + 1; };
const shellAnchors = () => Object.fromEntries(['.companion-avatar', '.composer'].map(selector => {
  const rect = document.querySelector(selector).getBoundingClientRect();
  return [selector, { left: rect.left, bottom: innerHeight - rect.bottom, width: rect.width, height: rect.height }];
}));
const sameAnchors = (before, after) => Object.keys(before).every(selector => Object.keys(before[selector]).every(axis => Math.abs(before[selector][axis] - after[selector][axis]) <= 1));
const widgetReplies = [];
window.addEventListener('message', event => {
  if (event.source === document.querySelector('[data-component-id="note"] iframe')?.contentWindow && event.data?.probe === 'companion-note') widgetReplies.push(event.data);
});
const noteHTML = `<label>Working note<textarea aria-label="Working note">A note to retain</textarea></label><script>
const identity = 'note-' + Math.random();
const inspect = () => { const field=document.querySelector('textarea');parent.postMessage({probe:'companion-note',identity,text:field.value,start:field.selectionStart,end:field.selectionEnd},'*'); };
window.addEventListener('message',event=>{if(event.data?.probeCommand==='edit'){const field=document.querySelector('textarea');field.value='Keep my selected words';field.focus();field.setSelectionRange(5,7);field.dispatchEvent(new Event('input',{bubbles:true}));inspect();}else if(event.data?.probeCommand==='inspect'){inspect();}});inspect();
</script>`;
const inspectWidget = async (frame, action = 'inspect') => {
  const previous = widgetReplies.length;
  frame.contentWindow.postMessage({ probeCommand: action }, '*');
  await until(() => widgetReplies.length > previous, 'widget ' + action);
  return widgetReplies.at(-1);
};
createRoot(document.getElementById('root'), { onUncaughtError: error => window.webkit.messageHandlers.probe.postMessage({ react_error: String(error), stack: error.stack }) }).render(<I18nProvider><App /></I18nProvider>);
async function run() {
  try {
    await until(() => input() && innerWidth === 560, 'initial compact companion'); await delay(180);
    check('default_has_no_header_or_chat_transcript', !document.querySelector('.topbar') && !document.querySelector('.conversation') && !result() && document.querySelector('.companion-avatar'));
    check('compact_input_and_presence_fit', innerHeight === 260 && inside(document.querySelector('.composer')) && inside(document.querySelector('.companion-avatar')), { viewport: [innerWidth, innerHeight] });
    await capture('companion-idle');
    await edit('Keep this question through login.'); send();
    await until(() => document.querySelector('[data-app-view="authentication"]') && innerWidth === 560, 'login form'); await delay(250);
    check('login_is_native_companion_content_and_draft_survives', inside(document.querySelector('[data-app-view="authentication"]')) && input().value === 'Keep this question through login.',
      { scroll: { window: scrollY, app: document.querySelector('.app').scrollTop, body: document.body.scrollTop, root: document.querySelector('#root').scrollTop },
        avatar: document.querySelector('.companion-avatar')?.getBoundingClientRect().toJSON(), dialog: document.querySelector('[data-app-view="authentication"]').getBoundingClientRect().toJSON() });
    check('blocking_login_hides_background_controls_and_keeps_focus_in_dialog', getComputedStyle(document.querySelector('.companion-presence')).visibility === 'hidden'
      && getComputedStyle(document.querySelector('.composer')).visibility === 'hidden' && document.querySelector('[data-app-view="authentication"]').contains(document.activeElement));
    await capture('companion-login');
    await nativeClick(document.querySelector('[data-app-view="authentication"] .dialog-top button'));
    await until(() => !document.querySelector('[data-app-view="authentication"]'), 'native login close');
    check('cancelling_login_preserves_unsent_question', !document.querySelector('[data-app-view="authentication"]') && input().value === 'Keep this question through login.');
    check('cancelling_login_restores_companion_controls', getComputedStyle(document.querySelector('.companion-presence')).visibility === 'visible'
      && getComputedStyle(document.querySelector('.composer')).visibility === 'visible' && inside(document.querySelector('.composer')));
    send(); await until(() => document.querySelector('#username'), 'login reopened');
    for (const [selector, text] of [['#username', 'synthetic'], ['#password', 'fixture-password']]) {
      const field = document.querySelector(selector); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, text); field.dispatchEvent(new Event('input', { bubbles: true }));
    }
    await delay(); document.querySelector('[data-app-view="authentication"]').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await until(() => observations.some(item => item.session_id === 's1') && !document.querySelector('[data-app-view="authentication"]'), 'logged in');
    check('login_restores_same_question_without_sending_it', input().value === 'Keep this question through login.' && !calls.some(item => item.path.endsWith('/messages')));
    await edit('Please organize these documents.'); const draft = input().value, field = input();
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'native hidden observation');
    check('hiding_keeps_draft_and_does_not_stop_or_send', input() === field && input().value === draft && !calls.some(item => item.path.endsWith('/messages') || item.path.endsWith('/stop')) && backgroundEvents > 0);
    nativeShow(); await until(() => observations.at(-1)?.foreground === true, 'shown observation'); await delay(80);
    check('recall_keeps_same_input_and_restores_focus', input() === field && input().value === draft && document.activeElement === field);
    const compactAnchors = shellAnchors();
    send(); await until(() => receipts.size === 1 && input().value === '', 'accepted send'); emit();
    await until(() => document.querySelector('[data-task-active="true"]') && innerWidth === 560, 'active task');
    check('send_has_one_bound_durable_receipt', calls.filter(item => item.path.endsWith('/messages')).length === 1 && receipts.size === 1 && snapshot.messages[0].content === draft);
    await until(() => result(), 'running result open'); await delay(200);
    await nativeClick(document.querySelector('.companion-avatar')); await until(() => !result() && innerHeight === 260, 'collapse running task'); await delay(180);
    await capture('companion-compact-running');
    check('compact_running_task_keeps_input_and_stop_inside_panel', inside(document.querySelector('.composer')) && inside(document.querySelector('.send.stop')) && inside(input()),
      { viewport: [innerWidth, innerHeight], composer: document.querySelector('.composer').getBoundingClientRect().toJSON(), stop: document.querySelector('.send.stop').getBoundingClientRect().toJSON(), display: getComputedStyle(document.querySelector('.composer')).display });
    await edit('First line of my next request.\nSecond line adds context.\nThird line stays editable.'); await delay(220);
    await capture('companion-compact-multiline');
    check('collapsed_running_task_fits_multiline_draft_and_all_actions', !result() && inside(document.querySelector('.composer')) && inside(document.querySelector('.send.stop')) && inside(input())
      && [...document.querySelectorAll('.send-actions button')].every(inside), { viewport: [innerWidth, innerHeight], composer: document.querySelector('.composer').getBoundingClientRect().toJSON() });
    await edit(''); await delay(180);
    await nativeClick(document.querySelector('.companion-avatar')); await until(() => result(), 'reopen running task');
    await edit('Do not lose this next request.');
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'hidden working');
    check('hidden_task_remains_running_once', snapshot.runs[0].status === 'running' && !calls.some(item => item.path.endsWith('/stop')));
    nativeShow(); await until(() => observations.at(-1)?.foreground === true, 'working recalled');
    dispatch({ action: 'stop' }); await until(() => stops.size === 1, 'explicit native stop'); emit();
    check('explicit_stop_is_distinct_and_preserves_draft', calls.filter(item => item.path.endsWith('/stop')).length === 1 && input().value === 'Do not lose this next request.');
    snapshot.runs = [{ id: 'r1', status: 'complete', phase: 'responding' }];
    snapshot.messages.push({ id: 'a1', role: 'assistant', content: 'Your files are ready.\n\n| Folder | Items |\n| --- | --- |\n| Invoices | 12 |\n| Notes | 4 |\n\nThe originals are unchanged.', status: 'complete', run_id: 'r1' }); emit();
    await until(() => result()?.querySelector('table'), 'readable result'); await delay(220);
    check('result_uses_markdown_and_keeps_input', result().querySelectorAll('td').length === 4 && input() === field && inside(document.querySelector('.composer')));
    check('short_result_fits_without_full_height_blank_card', innerHeight < 680 && inside(result()), { height: innerHeight });
    check('opening_result_keeps_avatar_and_composer_anchored', sameAnchors(compactAnchors, shellAnchors()), { before: compactAnchors, after: shellAnchors() });
    await capture('companion-result');
    document.querySelector('.companion-result-heading button').click(); await until(() => !result() && innerWidth === 560, 'put result away');
    check('putting_result_away_returns_to_companion_without_erasing_it', snapshot.messages.some(item => item.id === 'a1') && input().value === 'Do not lose this next request.');
    await nativeClick(document.querySelector('.companion-avatar')); await until(() => visibleMessage('a1'), 'avatar reopens result');
    check('avatar_reopens_previous_result_without_new_request', document.querySelector('.companion-avatar').getAttribute('aria-expanded') === 'true' && calls.filter(item => item.path.endsWith('/messages')).length === 1 && visibleMessage('u1')?.textContent === draft);
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'hide reading result');
    nativeShow(); await until(() => observations.at(-1)?.foreground === true && visibleMessage('a1'), 'recall reading result');
    check('recall_restores_reading_without_activity_feed_item', visibleMessage('u1') && visibleMessage('a1') && input() === field && input().value === 'Do not lose this next request.');
    document.querySelector('.companion-result-heading button').click(); await until(() => !result(), 'collapse before help');
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'hidden before help');
    snapshot.runs = [{ id: 'r1', status: 'waiting_user', phase: 'waiting' }];
    snapshot.help_requests = [{ id: 'h1', run_id: 'r1', session_id: 's1', status: 'pending', questions: [{ id: 'q1', question: 'Which month should I start with?', options: [{ label: 'October' }] }] }]; emit();
    await delay(200); nativeShow(); await until(() => document.querySelector('.user-help') && innerWidth === 560, 'recalled pending help');
    check('hidden_question_is_visible_on_recall', document.querySelector('.user-help').textContent.includes('Which month should I start with?') && inside(document.querySelector('.composer')));
    await edit('October'); send(); await until(() => !document.querySelector('.user-help'), 'help answered');
    const answers = calls.filter(item => item.path === '/api/sessions/s1/help/h1/answer');
    check('help_answer_resumes_same_run_once', answers.length === 1 && answers[0].body.answers.q1 === 'October' && snapshot.runs[0].id === 'r1');
    await edit('Do not lose this next request.');
    dispatch({ action: 'history' }); await until(() => document.querySelector('[data-app-view="history"]'), 'history');
    check('history_is_reachable_without_replacing_input', document.querySelector('.session-list')?.textContent.includes('Organize my documents') && input() === field);
    await nativeClick(document.querySelector('.session-list .session.selected')); await delay(250);
    check('selecting_current_history_keeps_completed_reply_and_unsent_draft', !document.querySelector('[data-app-view="history"]') && visibleMessage('a1')?.textContent.includes('Your files are ready.')
      && input() === field && input().value === 'Do not lose this next request.');
    dispatch({ action: 'menu' }); await until(() => document.querySelector('[data-app-view="menu"]'), 'menu');
    check('menu_retains_account_and_recovery', document.querySelector('[data-app-view="menu"]')?.textContent.includes('Account') && document.querySelector('[data-app-view="menu"]')?.textContent.includes('Reset'));
    await nativeClick(document.querySelector('[data-app-view="menu"] .dialog-top button'));
    const back = document.querySelector('.topbar nav > button'); if (back) { back.click(); await until(() => !document.querySelector('.topbar'), 'back to companion'); }
    check('history_return_preserves_same_input', input() === field && input().value === 'Do not lose this next request.');
    const activityReceipt = await applyAction('open', { target: 'activity' });
    await until(() => document.querySelector('[data-app-view="activity"]'), 'agent opened activity');
    check('agent_can_open_real_activity_view_in_companion', activityReceipt.status === 'applied' && inside(document.querySelector('[data-app-view="activity"]'))
      && observations.at(-1).open_views.includes('activity') && getComputedStyle(document.querySelector('[data-app-view="activity"]')).visibility === 'visible');
    escape(); await until(() => !document.querySelector('[data-app-view="activity"]'), 'close activity with escape');
    check('closing_activity_restores_companion_and_draft', getComputedStyle(document.querySelector('.companion-presence')).visibility === 'visible'
      && input() === field && input().value === 'Do not lose this next request.' && presented);
    await edit('How many notes should I review first?'); send(); await until(() => receipts.size === 2 && input().value === '', 'follow-up accepted'); emit();
    await until(() => visibleMessage('u2') && visibleMessage('a1'), 'follow-up context retained');
    check('followup_echoes_question_and_keeps_prior_answer', visibleMessage('u1')?.textContent === draft && visibleMessage('u2')?.textContent === 'How many notes should I review first?' && visibleMessage('a1')?.textContent.includes('Your files are ready.'));
    snapshot.messages.push({ id: 'a2', role: 'assistant', content: 'Start with the four notes.', status: 'streaming', run_id: 'r2' });
    snapshot.messages.push({ id: 'tool2', role: 'tool', content: 'INTERNAL_TOOL_RESULT_MUST_STAY_IN_ACTIVITY', status: 'complete', run_id: 'r2' }); emit();
    await until(() => visibleMessage('a2'), 'running follow-up reply');
    await edit('Then show the next step.'); send(); await until(() => receipts.size === 3 && input().value === '', 'next request queued'); emit();
    await until(() => visibleMessage('u3') && snapshot.runs[0].status === 'queued', 'queued question visible');
    check('queue_keeps_running_reply_and_ordered_conversation', [...result().querySelectorAll('[data-message-id]')].map(item => item.dataset.messageId).join(',') === 'u1,a1,u2,a2,u3' && visibleMessage('a2')?.textContent === 'Start with the four notes.' && !result().textContent.includes('INTERNAL_TOOL_RESULT_MUST_STAY_IN_ACTIVITY'));
    check('activity_has_one_trace_per_run', result().querySelectorAll('.run-trace').length === 3);
    const beforeStream = shellAnchors();
    snapshot.messages.find(message => message.id === 'a2').content += '\n\n' + Array.from({ length: 12 }, (_, index) => `Note ${index + 1}: inspect the source before making a decision.`).join('\n\n'); emit();
    await until(() => visibleMessage('a2')?.textContent.includes('Note 12:'), 'long stream reaches reading view'); await delay(180);
    check('growing_stream_keeps_controls_fixed_and_scrolls_only_reading', sameAnchors(beforeStream, shellAnchors()) && result().scrollHeight > result().clientHeight && inside(document.querySelector('.composer')), { before: beforeStream, after: shellAnchors() });
    snapshot.runs = snapshot.runs.map(run => ({ ...run, status: 'complete', phase: 'responding' }));
    snapshot.messages = snapshot.messages.map(message => ({ ...message, status: 'complete' }));
    snapshot.messages.push({ id: 'a3', role: 'assistant', content: 'Read one note, then decide what to keep.', status: 'complete', run_id: 'r3' }); emit();
    await until(() => visibleMessage('a3'), 'queued task complete');
    await edit('Keep this component draft too.');
    snapshot.components = [{ id: 'note', title: 'Working note', placement: 'inline', version: 1, view_revision: 0, html: noteHTML }]; emit();
    await until(() => document.querySelector('[data-component-id="note"] iframe') && widgetReplies.length > 0, 'inline working note');
    const noteFrame = document.querySelector('[data-component-id="note"] iframe');
    const editedNote = await inspectWidget(noteFrame, 'edit');
    document.querySelector('.companion-result-heading button').click(); await until(() => !result(), 'collapse with inline component');
    check('collapse_hides_reading_surface_even_with_inline_component', document.querySelector('.companion-result')?.hidden && document.querySelector('[data-component-id="note"] iframe') === noteFrame && input().value === 'Keep this component draft too.');
    await nativeClick(document.querySelector('.companion-avatar')); await until(() => result(), 'reopen inline component');
    const recalledNote = await inspectWidget(noteFrame);
    check('collapse_reopen_keeps_iframe_document_form_and_selection', document.querySelector('[data-component-id="note"] iframe') === noteFrame && recalledNote.identity === editedNote.identity && recalledNote.text === 'Keep my selected words' && recalledNote.start === 5 && recalledNote.end === 7, recalledNote);
    snapshot.components.push({ id: 'ornament', title: 'Independent ornament', placement: 'overlay', version: 1, view_revision: 0, html: '<span>Independent ornament</span><script>Athena.interact([{id:"ornament",label:"Use ornament",rect:{x:2,y:6,width:12,height:12}}],()=>{});</script>' }); emit();
    await until(() => document.querySelector('[data-component-id="ornament"]'), 'overlay registered');
    check('overlay_is_outside_the_default_reading_card', !document.querySelector('[data-component-id="ornament"]').closest('.companion-result'));
    await until(() => document.querySelector('[data-component-id="ornament"] .canvas-action') && nativePosts.filter(item => item.action === 'hitRegions').at(-1)?.regions.some(region => region.x === 2 && region.y === 6 && region.width === 12 && region.height === 12), 'overlay native hit region');
    check('overlay_interactive_rectangle_is_in_native_hit_regions', nativePosts.filter(item => item.action === 'hitRegions').at(-1).regions.some(region => region.x === 2 && region.y === 6 && region.width === 12 && region.height === 12));
    await nativeClick(document.querySelector('.companion-menu')); await until(() => document.querySelector('[data-app-view="menu"]'), 'avatar menu');
    check('companion_menu_exposes_history_and_account', document.querySelector('[data-app-view="menu"]').textContent.includes('Previous conversations') && document.querySelector('[data-app-view="menu"]').textContent.includes('Account'));
    escape(); await until(() => !document.querySelector('[data-app-view="menu"]'), 'escape closes menu');
    check('escape_closes_top_dialog_before_reading_or_native_panel', Boolean(result()) && presented);
    input().focus(); escape(true); await delay(100);
    check('composition_escape_does_not_dismiss_companion', Boolean(result()) && presented && input().value === 'Keep this component draft too.');
    escape(); await until(() => !result(), 'escape closes reading');
    check('escape_collapses_reading_before_hiding', presented && document.querySelector('[data-component-id="note"] iframe') === noteFrame);
    escape(); await until(() => !presented, 'escape hides compact companion');
    check('escape_hides_without_cancelling_or_losing_drafts', calls.filter(item => item.path.endsWith('/stop')).length === 1 && input().value === 'Keep this component draft too.');
    nativeShow(); await until(() => observations.at(-1)?.foreground === true, 'recall compact companion');
    check('recall_preserves_explicitly_collapsed_reading_state', !result() && document.querySelector('.companion-avatar').getAttribute('aria-expanded') === 'false');
    await nativeClick(document.querySelector('.companion-avatar')); await until(() => result(), 'reading restored after escape');
    snapshot.components = [{ id: 'collector', title: 'Desktop collector', placement: 'desktop', version: 1, view_revision: 0, html: '<button>Collect</button>', view: { width: 80, height: 64, x: .8, y: .2 } }]; emit();
    await until(() => nativePosts.some(item => item.action === 'scene' && item.items?.[0]?.id === 'collector') && observations.some(item => item.desktop?.components?.[0]?.id === 'collector'), 'desktop scene receipt');
    const scene = nativePosts.filter(item => item.action === 'scene' && item.items?.[0]?.id === 'collector').at(-1);
    check('desktop_scene_uses_native_bridge_not_conversation_widget', scene.scope === 'probe:s1' && scene.items[0].width === 80 && !('html' in scene.items[0]) && !document.querySelector('[data-component-id="collector"]'));
    check('native_receipt_appears_in_frontend_observation', observations.at(-1).desktop.components[0].state === 'mounted');
    const file = { id: 'report', session_id: 's1', name: 'Notes summary.md', path: 'outputs/notes.md', size: 100, media_type: 'text/markdown', source: 'output', created_at: '2026-10-09T00:00:00Z', content_url: '/api/files/report/content', preview_url: '/api/files/report/preview', content_revision: 'revision-one' };
    snapshot.files = [file]; snapshot.messages.find(message => message.id === 'a3').attachments = [structuredClone(file)]; emit();
    const previewReceipt = await applyAction('preview', { file_id: file.id, file });
    await until(() => result()?.textContent.includes('Original notes'), 'selected attached file preview');
    check('attached_file_preview_is_displayed_once', previewReceipt.status === 'applied' && result().querySelectorAll('.file-preview[aria-label="Notes summary.md"]').length === 1 && result().querySelectorAll('[data-inline-preview-id="report"]').length === 1);
    snapshot.files = [{ ...file, size: 101, content_revision: 'revision-two' }]; emit();
    await until(() => result()?.textContent.includes('Canonical file revision.'), 'canonical attachment file update');
    check('attached_preview_uses_current_canonical_file_revision', previewRequests.at(-1) === '/api/files/report/preview?revision=revision-two' && result().querySelectorAll('.file-preview[aria-label="Notes summary.md"]').length === 1
      && !result().textContent.includes('Initial file revision.') && snapshot.messages.find(message => message.id === 'a3').attachments[0].content_revision === 'revision-one');
    failNextPreview = true; snapshot.files = [{ ...file, content_revision: 'revision-three' }]; emit();
    await until(() => result()?.querySelector('.file-preview .empty-note button'), 'failed file preview retry');
    const previewContainer = result().querySelector('.artifact-result'), beforeRetry = previewRequests.length;
    await delay(100); await nativeClick(result().querySelector('.file-preview .empty-note button'));
    await until(() => result()?.textContent.includes('Preview recovered in place.'), 'retried inline preview');
    check('file_preview_retry_recovers_in_place_without_reopening_chat', result().querySelector('.artifact-result') === previewContainer
      && previewRequests.length === beforeRetry + 1 && previewRequests.at(-1) === '/api/files/report/preview?revision=revision-three&retry=1'
      && result().querySelectorAll('.file-preview').length === 1 && input().value === 'Keep this component draft too.');
    await edit('Keep this failed message.'); failNext = true; send(); await until(() => document.querySelector('.composer-area .notice'), 'failed send');
    check('send_error_is_visible_and_draft_is_retained', input().value === 'Keep this failed message.' && inside(document.querySelector('.composer')) && Boolean(document.querySelector('.composer-area .notice')?.textContent));
    await capture('companion-error');
    const oldMessages = structuredClone(snapshot.messages);
    dispatch({ action: 'new' }); await until(() => !result() && !document.querySelector('.topbar') && !input().value && nativeScope === '', 'native new task');
    check('new_task_clears_visible_context_without_deleting_previous_task', !document.querySelector('[data-message-id="a1"]') && !document.querySelector('.composer-area .notice') && JSON.stringify(snapshot.messages) === JSON.stringify(oldMessages) && !calls.some(item => /delete|reset/.test(item.path)));
    await edit('Check my free disk space.'); send(); await until(() => receipts.size === 4 && input().value === '' && nativeScope === 'probe:s2', 'new task accepted');
    check('new_task_creates_separate_session_and_keeps_old_draft', calls.filter(item => item.path === '/api/sessions').length === 1 && secondSnapshot.messages[0]?.content === 'Check my free disk space.' && snapshot.messages.length === oldMessages.length && !visibleMessage('a1'));
    dispatch({ action: 'history' }); await until(() => document.querySelector('.session-list'), 'return old session history');
    const previousSession = [...document.querySelectorAll('.session-list .session')].find(item => item.textContent.includes('Organize my documents'));
    previousSession.click(); await until(() => input().value === 'Keep this failed message.' && nativeScope === 'probe:s1', 'old task draft restored');
    check('returning_to_previous_task_restores_its_own_unsent_draft', input().value === 'Keep this failed message.' && snapshot.messages.length === oldMessages.length && secondSnapshot.messages.length === 1);
    const beforeAttention = shellAnchors(), requestsBeforeAttention = calls.filter(item => item.path.endsWith('/messages') || item.path.endsWith('/stop') || item.path === '/api/activity/ack').length;
    activityItems = [{ run_id: 'background-help', session_id: 's2', title: 'Choose the documents to organize', status: 'waiting_user', phase: 'waiting',
      needs_user: true, help_id: 'h2', attention_key: 'help-two', created_at: '2026-10-09', updated_at: '2026-10-09' }];
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'hide before background help');
    nativeShow(); await until(() => document.querySelector('.companion-task-attention'), 'background help status'); await delay(180);
    check('background_help_appears_in_existing_status_without_moving_controls', document.querySelector('.companion-task-attention').textContent.includes('Needs your reply')
      && inside(document.querySelector('.companion-task-attention')) && sameAnchors(beforeAttention, shellAnchors()), { before: beforeAttention, after: shellAnchors() });
    const idleRuns = snapshot.runs;
    for (const [phase, label] of [['thinking', 'Thinking it through'], ['executing', 'Taking action'], ['responding', 'Writing a reply']]) {
      snapshot.runs = [{ id: 'foreground-status', status: 'running', phase }, ...idleRuns]; emit();
      await until(() => document.querySelector('.companion-status')?.textContent === label, `foreground ${phase} status`);
      check(`current_${phase}_status_is_not_replaced_by_background_attention`, !document.querySelector('.companion-task-attention')
        && document.querySelector('.companion-status').getAttribute('role') === 'status');
    }
    snapshot.runs = idleRuns; emit();
    await until(() => document.querySelector('.companion-task-attention'), 'idle background help restored');
    check('background_attention_returns_after_current_work_finishes_without_marking_it_read', document.querySelector('.companion-task-attention').textContent.includes('Needs your reply')
      && calls.filter(item => item.path === '/api/activity/ack').length === 0);
    await nativeClick(document.querySelector('.companion-task-attention')); await until(() => document.querySelector('[data-app-view="activity"]'), 'background help opened');
    check('background_attention_opens_pending_question_without_acknowledging_or_executing', document.querySelector('[data-app-view="activity"]').textContent.includes('Choose the documents to organize')
      && calls.filter(item => item.path.endsWith('/messages') || item.path.endsWith('/stop') || item.path === '/api/activity/ack').length === requestsBeforeAttention);
    escape(); await until(() => !document.querySelector('[data-app-view="activity"]'), 'close background help view');
    activityItems = [{ ...activityItems[0], status: 'interrupted', needs_user: false, help_id: null }];
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'hide before stopped update');
    nativeShow(); await until(() => !document.querySelector('.companion-task-attention'), 'stopped update is quiet');
    check('stopped_background_task_does_not_leave_a_permanent_attention_badge', input().value === 'Keep this failed message.' && document.querySelector('.companion-status').textContent.includes('Continue'));
    activityItems = [];
    snapshot.components = [{ id: 'reader', title: 'Custom reader without recovery controls', placement: 'surface', version: 1, view_revision: 0,
      html: '<main><h1>Read my document</h1><p>This generated surface has no stop, reset or navigation controls.</p></main>' }];
    snapshot.runs.unshift({ id: 'surface-run', status: 'running', phase: 'executing' }); emit();
    await until(() => document.querySelector('.companion-surface-controls') && document.querySelector('[data-component-id="reader"] iframe'), 'custom surface recovery controls'); await delay(180);
    const controls = document.querySelector('.companion-surface-controls'), controlButtons = [...controls.querySelectorAll('button')];
    check('custom_surface_keeps_visible_trusted_recovery_outside_generated_frame', controlButtons.length === 3 && controlButtons.every(inside)
      && controls.parentElement.classList.contains('desktop-companion') && !document.querySelector('.topbar') && nativePosts.filter(item => item.action === 'hitRegions').at(-1).regions.some(region => Math.abs(region.y - controls.getBoundingClientRect().y) < 1 && region.width > 500));
    const stopsBeforeSurface = stops.size;
    await nativeClick(controlButtons[1]); await until(() => stops.size === stopsBeforeSurface + 1, 'trusted surface Stop');
    check('custom_surface_stop_reaches_backend_without_frame_cooperation', snapshot.runs.every(run => run.status === 'interrupted') && Boolean(document.querySelector('[data-component-id="reader"] iframe')));
    await nativeClick(controlButtons[0]); await until(() => input() && !document.querySelector('.companion-surface-controls'), 'return from generated surface');
    check('trusted_return_restores_chat_and_unsent_draft', input().value === 'Keep this failed message.' && visibleMessage('a1') && snapshot.components[0].view.hidden);
    snapshot.components[0] = { ...snapshot.components[0], version: 2, view: { hidden: false } }; emit();
    await until(() => document.querySelector('.companion-surface-controls'), 'replacement custom surface');
    const messagesBeforeReset = JSON.stringify(snapshot.messages), filesBeforeReset = JSON.stringify(snapshot.files);
    await nativeClick(document.querySelector('.companion-surface-controls button:last-child')); await until(() => !document.querySelector('.companion-surface-controls') && input(), 'trusted reset');
    check('trusted_surface_reset_preserves_chat_files_and_draft', calls.filter(item => item.path === '/api/reset').length === 1 && snapshot.components.length === 0
      && JSON.stringify(snapshot.messages) === messagesBeforeReset && JSON.stringify(snapshot.files) === filesBeforeReset && input().value === 'Keep this failed message.');
    const reminderTrigger = '[已登记任务到期：Water the plants；任务 ID private-timer-id，第 2 次。请按之前登记的要求执行。]';
    snapshot.runs.unshift({ id: 'reminder-run', status: 'complete', phase: 'responding' });
    snapshot.messages.push({ id: 'reminder-trigger', role: 'user', content: reminderTrigger, status: 'complete', run_id: 'reminder-run',
      origin: { kind: 'task', trigger: 'time', task_id: 'private-timer-id', title: 'Water the plants', occurrence: 2 } },
      { id: 'reminder-answer', role: 'assistant', content: 'It is time to water the plants.', status: 'complete', run_id: 'reminder-run' }); emit();
    await until(() => visibleMessage('reminder-answer'), 'scheduled reminder displayed');
    const reminderTrace = [...result().querySelectorAll('.run-trace')].find(trace => trace.querySelector('summary').textContent.includes('Water the plants'));
    check('reminder_uses_human_title_without_injected_instruction_in_default_view', reminderTrace && !reminderTrace.open
      && !visibleMessage('reminder-trigger') && !result().textContent.includes('private-timer-id') && !result().textContent.includes('请按之前登记')
      && document.querySelector('.companion-status').title === 'Water the plants' && snapshot.messages.find(item => item.id === 'reminder-trigger').content === reminderTrigger);
    reminderTrace.querySelector('summary').click(); await until(() => reminderTrace.textContent.includes('private-timer-id'), 'original reminder trace');
    check('original_reminder_trigger_remains_inspectable_in_explicit_activity', reminderTrace.open && reminderTrace.textContent.includes(reminderTrigger));
    reminderTrace.querySelector('summary').click();
    snapshot.messages.push({ id: 'delegated-result', role: 'user', content: 'INTERNAL_DELEGATED_RESULT_INSTRUCTIONS', status: 'complete',
      origin: { kind: 'agent_results', wait_id: 'wait-one', agents: [{ id: 'child-one', title: 'Review the quote', status: 'complete', session_id: 'child-session', run_id: 'child-run' }] } },
      { id: 'help-reply', role: 'user', content: 'Use this invoice.', status: 'complete',
        help_reply: { id: 'answered-help', reason: 'Choose an invoice', items: [{ id: 'invoice-choice', question: 'Which invoice?', answer: 'This one' }] },
        attachments: [{ ...file, id: 'provided-invoice', source: 'output', name: 'Invoice.md', content_url: '/api/files/provided-invoice/content', preview_url: '/api/files/provided-invoice/preview' }] }); emit();
    await until(() => result()?.querySelector('.agent-receipt') && result()?.querySelector('.help-response-files'), 'delegation and help receipts');
    check('delegation_and_help_use_receipts_without_losing_provided_files', !visibleMessage('delegated-result') && !result().textContent.includes('INTERNAL_DELEGATED_RESULT_INSTRUCTIONS')
      && result().querySelector('.agent-receipt').textContent.includes('Review the quote') && result().querySelector('.help-response-files').textContent.includes('Invoice.md')
      && result().querySelector('.help-receipt').textContent.includes('This one') && !result().textContent.includes('private-timer-id'));
    result().querySelector('.help-response-files .attachment-open').click(); await until(() => result()?.textContent.includes('Keep this attachment accessible.'), 'help attachment preview');
    check('help_provided_output_file_can_still_open_its_inline_preview', result().querySelectorAll('.file-preview[aria-label="Invoice.md"]').length === 1
      && result().querySelector('.help-response-files').textContent.includes('Invoice.md'));
    check('no_unexpected_transport', unexpected.length === 0, unexpected);
    results.evidence = { real_backend: false, real_provider: false, native_scene_effects: 'synthetic', keyboard_mouse: 'dedicated WKWebView fixture', installed_application_touched: false };
    window.webkit.messageHandlers.probe.postMessage(results);
  } catch (error) { window.webkit.messageHandlers.probe.postMessage({ ...results, error: String(error), stack: error.stack, unexpected, recent_paths: calls.slice(-6).map(item => item.path) }); }
}
void run();
