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
let snapshot = { cursor: 1, submission_revision: 0, components: [], messages: [], runs: [], files: [], tasks: [], help_requests: [] };
const observations = [], calls = [], unexpected = [], nativePosts = [], results = {};
const receipts = new Map(), stops = new Map();
let failNext = false, presented = true, nativeComponents = [], backgroundEvents = 0, signedIn = false, nativeScope = '';
window.addEventListener('athena:background', () => backgroundEvents++);
const dispatch = detail => window.dispatchEvent(new CustomEvent('athena:desktop', { detail }));
const nativeState = () => ({ platform: 'macos', supported: true, presented,
  screens: [{ id: '1', frame: { x: 0, y: 0, width: 1512, height: 982 }, visible_frame: { x: 0, y: 25, width: 1512, height: 930 }, scale: 2 }], components: nativeComponents });
const publishNative = () => dispatch({ action: 'state', scope: nativeScope, desktop: nativeState() });
window.addEventListener('probe-native-message', event => {
  const body = event.detail; nativePosts.push(body);
  if (body.action === 'resize') window.webkit.messageHandlers.probe.postMessage({ resize: body.mode === 'expanded'
    ? { width: 760, height: Math.min(680, Math.max(260, typeof body.height === 'number' ? body.height : 680)) }
    : { width: 560, height: 260 } });
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
  else if (path.startsWith('/api/service')) value = { configured: true };
  else if (path === '/api/sessions') value = [{ id: 's1', title: 'Organize my documents', pending: 0 }];
  else if (path === '/api/sessions/s1') value = structuredClone(snapshot);
  else if (path.startsWith('/api/activity')) value = { owner_id: 'probe', items: [], has_more: false };
  else if (path === '/api/frontend/observation') { observations.push(body); value = { accepted: true }; }
  else if (path.endsWith('/report')) value = { accepted: true };
  else if (path.endsWith('/messages')) {
    if (failNext) { failNext = false; status = 503; value = { error: 'Synthetic service unavailable', code: 'service_unavailable' }; }
    else {
      if (body.expected_submission_revision !== snapshot.submission_revision || !body.frontend_client_id) throw new Error('Unbound or stale submission');
      value = { accepted: true, run_id: 'r1', status: 'queued', client_submission_id: body.client_submission_id,
        submission_revision: snapshot.submission_revision, replayed: false };
      receipts.set(body.client_submission_id, value);
      snapshot.messages.push({ id: 'u1', role: 'user', content: body.text, status: 'complete', run_id: 'r1' });
      snapshot.runs = [{ id: 'r1', status: 'running', phase: 'executing' }];
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
  else if (path.includes('/trace')) value = { run: snapshot.runs[0] || { status: 'complete' }, entries: [], total: 0, next_after: 0, has_more: false };
  else if (path === '/api/voice/status') value = { asr: false, tts: false };
  else { unexpected.push(path); value = {}; }
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
};
const emit = () => { snapshot.cursor++; for (const source of sources.filter(item => item.readyState === 1 && item.url.startsWith('/api/sessions/s1/events'))) source.onmessage?.({ data: JSON.stringify({ seq: snapshot.cursor, type: 'component', payload: {} }) }); };
const until = async (read, name) => { const deadline = performance.now() + 6000; do { if (read()) return; await delay(); } while (performance.now() < deadline); throw new Error(`timeout:${name}`); };
const check = (name, pass, detail = undefined) => { window.webkit.messageHandlers.probe.postMessage({ progress: name }); results[name] = { pass: Boolean(pass), detail }; if (!pass) throw new Error(name); };
const capture = async name => {
  const captured = new Promise((resolve, reject) => window.addEventListener('probe-captured', event => event.detail?.error ? reject(new Error(event.detail.error)) : resolve(undefined), { once: true }));
  window.webkit.messageHandlers.probe.postMessage({ capture: name }); await captured;
};
const input = () => document.querySelector('.composer textarea');
const edit = async text => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input(), text); input().dispatchEvent(new Event('input', { bubbles: true })); await delay(70); };
const send = () => document.querySelector('.composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
const nativeClick = async element => { const bounds = element.getBoundingClientRect(); window.webkit.messageHandlers.probe.postMessage({ nativeClick: { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 } }); await delay(100); };
const nativeShow = () => { presented = true; dispatch({ action: 'show' }); publishNative(); };
const inside = element => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight + 1; };
createRoot(document.getElementById('root'), { onUncaughtError: error => window.webkit.messageHandlers.probe.postMessage({ react_error: String(error), stack: error.stack }) }).render(<I18nProvider><App /></I18nProvider>);
async function run() {
  try {
    await until(() => input() && innerWidth === 560, 'initial compact companion'); await delay(180);
    check('default_has_no_header_or_chat_transcript', !document.querySelector('.topbar') && !document.querySelector('.conversation') && !document.querySelector('.companion-result') && document.querySelector('.companion-avatar'));
    check('compact_input_and_presence_fit', innerHeight === 260 && inside(document.querySelector('.composer')) && inside(document.querySelector('.companion-avatar')), { viewport: [innerWidth, innerHeight] });
    await capture('companion-idle');
    await edit('Keep this question through login.'); send();
    await until(() => document.querySelector('[data-app-view="authentication"]') && innerWidth === 760 && innerHeight === 680, 'login form'); await delay(250);
    check('login_is_native_companion_content_and_draft_survives', inside(document.querySelector('[data-app-view="authentication"]')) && input().value === 'Keep this question through login.',
      { scroll: { window: scrollY, app: document.querySelector('.app').scrollTop, body: document.body.scrollTop, root: document.querySelector('#root').scrollTop },
        avatar: document.querySelector('.companion-avatar')?.getBoundingClientRect().toJSON(), dialog: document.querySelector('[data-app-view="authentication"]').getBoundingClientRect().toJSON() });
    await capture('companion-login');
    await nativeClick(document.querySelector('[data-app-view="authentication"] .dialog-top button'));
    await until(() => !document.querySelector('[data-app-view="authentication"]'), 'native login close');
    check('cancelling_login_preserves_unsent_question', !document.querySelector('[data-app-view="authentication"]') && input().value === 'Keep this question through login.');
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
    send(); await until(() => receipts.size === 1 && input().value === '', 'accepted send'); emit();
    await until(() => document.querySelector('[data-task-active="true"]') && innerWidth === 760, 'active task');
    check('send_has_one_bound_durable_receipt', calls.filter(item => item.path.endsWith('/messages')).length === 1 && receipts.size === 1 && snapshot.messages[0].content === draft);
    await edit('Do not lose this next request.');
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'hidden working');
    check('hidden_task_remains_running_once', snapshot.runs[0].status === 'running' && !calls.some(item => item.path.endsWith('/stop')));
    nativeShow(); await until(() => observations.at(-1)?.foreground === true, 'working recalled');
    dispatch({ action: 'stop' }); await until(() => stops.size === 1, 'explicit native stop'); emit();
    check('explicit_stop_is_distinct_and_preserves_draft', calls.filter(item => item.path.endsWith('/stop')).length === 1 && input().value === 'Do not lose this next request.');
    snapshot.runs = [{ id: 'r1', status: 'complete', phase: 'responding' }];
    snapshot.messages.push({ id: 'a1', role: 'assistant', content: 'Your files are ready.\n\n| Folder | Items |\n| --- | --- |\n| Invoices | 12 |\n| Notes | 4 |\n\nThe originals are unchanged.', status: 'complete', run_id: 'r1' }); emit();
    await until(() => document.querySelector('.companion-result table'), 'readable result'); await delay(220);
    check('result_uses_markdown_and_keeps_input', document.querySelectorAll('.companion-result td').length === 4 && input() === field && inside(document.querySelector('.composer')));
    check('short_result_fits_without_full_height_blank_card', innerHeight < 680 && inside(document.querySelector('.companion-result')), { height: innerHeight });
    await capture('companion-result');
    document.querySelector('.companion-result-heading button').click(); await until(() => !document.querySelector('.companion-result') && innerWidth === 560, 'put result away');
    check('putting_result_away_returns_to_companion_without_erasing_it', snapshot.messages.some(item => item.id === 'a1') && input().value === 'Do not lose this next request.');
    await nativeClick(document.querySelector('.companion-hide')); await until(() => observations.at(-1)?.foreground === false, 'hidden before help');
    snapshot.runs = [{ id: 'r1', status: 'waiting_user', phase: 'waiting' }];
    snapshot.help_requests = [{ id: 'h1', run_id: 'r1', session_id: 's1', status: 'pending', questions: [{ id: 'q1', question: 'Which month should I start with?', options: [{ label: 'October' }] }] }]; emit();
    await delay(200); nativeShow(); await until(() => document.querySelector('.user-help') && innerWidth === 760, 'recalled pending help');
    check('hidden_question_is_visible_on_recall', document.querySelector('.user-help').textContent.includes('Which month should I start with?') && inside(document.querySelector('.composer')));
    await edit('October'); send(); await until(() => !document.querySelector('.user-help'), 'help answered');
    const answers = calls.filter(item => item.path === '/api/sessions/s1/help/h1/answer');
    check('help_answer_resumes_same_run_once', answers.length === 1 && answers[0].body.answers.q1 === 'October' && snapshot.runs[0].id === 'r1');
    await edit('Do not lose this next request.');
    dispatch({ action: 'history' }); await until(() => document.querySelector('[data-app-view="history"]'), 'history');
    check('history_is_reachable_without_replacing_input', document.querySelector('.session-list')?.textContent.includes('Organize my documents') && input() === field);
    await nativeClick(document.querySelector('[data-app-view="history"] .dialog-top button'));
    dispatch({ action: 'menu' }); await until(() => document.querySelector('[data-app-view="menu"]'), 'menu');
    check('menu_retains_account_and_recovery', document.querySelector('[data-app-view="menu"]')?.textContent.includes('Account') && document.querySelector('[data-app-view="menu"]')?.textContent.includes('Reset'));
    await nativeClick(document.querySelector('[data-app-view="menu"] .dialog-top button'));
    const back = document.querySelector('.topbar nav > button'); if (back) { back.click(); await until(() => !document.querySelector('.topbar'), 'back to companion'); }
    check('history_return_preserves_same_input', input() === field && input().value === 'Do not lose this next request.');
    snapshot.components = [{ id: 'collector', title: 'Desktop collector', placement: 'desktop', version: 1, view_revision: 0, html: '<button>Collect</button>', view: { width: 80, height: 64, x: .8, y: .2 } }]; emit();
    await until(() => nativePosts.some(item => item.action === 'scene' && item.items?.[0]?.id === 'collector') && observations.some(item => item.desktop?.components?.[0]?.id === 'collector'), 'desktop scene receipt');
    const scene = nativePosts.filter(item => item.action === 'scene' && item.items?.[0]?.id === 'collector').at(-1);
    check('desktop_scene_uses_native_bridge_not_conversation_widget', scene.scope === 'probe:s1' && scene.items[0].width === 80 && !('html' in scene.items[0]) && !document.querySelector('[data-component-id="collector"]'));
    check('native_receipt_appears_in_frontend_observation', observations.at(-1).desktop.components[0].state === 'mounted');
    await edit('Keep this failed message.'); failNext = true; send(); await until(() => document.querySelector('.composer-area .notice'), 'failed send');
    check('send_error_is_visible_and_draft_is_retained', input().value === 'Keep this failed message.' && inside(document.querySelector('.composer')) && Boolean(document.querySelector('.composer-area .notice')?.textContent));
    await capture('companion-error');
    check('no_unexpected_transport', unexpected.length === 0, unexpected);
    results.evidence = { real_backend: false, real_provider: false, native_scene_effects: 'synthetic', keyboard_mouse: 'dedicated WKWebView fixture', installed_application_touched: false };
    window.webkit.messageHandlers.probe.postMessage(results);
  } catch (error) { window.webkit.messageHandlers.probe.postMessage({ ...results, error: String(error), stack: error.stack, unexpected, recent_paths: calls.slice(-6).map(item => item.path) }); }
}
void run();
