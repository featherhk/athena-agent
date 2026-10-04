// Entire production App, synthetic transport, offline nonpersistent WKWebView.
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '../../src/App';
import { I18nProvider } from '../../src/i18n';
const delay = (ms = 40) => new Promise(resolve => setTimeout(resolve, ms));
const store = () => { const data = new Map(); return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key) }; };
Object.defineProperty(window, 'sessionStorage', { value: store() });
Object.defineProperty(window, 'localStorage', { value: store() });
sessionStorage.setItem('athena-session:probe', 's1');
localStorage.setItem('athena-language', 'en');
let uuid = 0;
// about:blank has no secure-context UUID API; this identifier is only for synthetic clients.
if (!crypto.randomUUID) Object.defineProperty(crypto, 'randomUUID', { value: () => '00000000-0000-4000-8000-' + String(++uuid).padStart(12, '0') });
const sources = [];
class Source {
  static CLOSED = 2; readyState = 1; onopen; onmessage; onerror; url;
  constructor(url) { this.url = url; sources.push(this); setTimeout(() => this.onopen?.(), 0); }
  addEventListener() {} removeEventListener() {} close() { this.readyState = 2; }
}
window.EventSource = Source;
let snapshot = { cursor: 1, components: [], messages: [
  { id: 'm1', role: 'user', content: 'Keep this conversation intact.', status: 'complete' },
  { id: 'm2', role: 'assistant', content: 'Your workspace can change while your conversation stays.', status: 'complete' },
], runs: [], files: [], tasks: [], help_requests: [] };
let loseReceipt = false, posts = 0, failHide = false, expandedNote = false;
const observations = [], unexpected = [];
window.fetch = async (path, init) => {
  path = String(path); if(path.includes('/files/')) window.webkit.messageHandlers.probe.postMessage({progress:'fetch:'+path}); const body = init?.body ? JSON.parse(init.body) : undefined;
  let value;
  if (path === '/api/auth/me') value = { user: { id: 'probe', username: 'synthetic', developer_mode: false }, configured: true, has_users: true };
  else if (path.startsWith('/api/service')) value = { configured: true };
  else if (path === '/api/sessions') value = [{ id: 's1', title: 'Synthetic workspace', pending: 0 }];
  else if (path === '/api/sessions/s1') value = structuredClone(snapshot);
  else if (path.startsWith('/api/activity')) value = { owner_id: 'probe', items: [], has_more: false };
  else if (path === '/api/frontend/observation') { observations.push(body); value = { accepted: true }; }
  else if (path.endsWith('/report')) value = { accepted: true };
  else if (path.endsWith('/view')) {
    const component = snapshot.components.find(item => path.includes('/' + item.id + '/'));
    if (failHide && component.id === 'older') { await delay(220); throw new Error('synthetic offline'); }
    component.view = { ...component.view, ...body.view }; component.view_revision++; value = structuredClone(component);
  }
  else if (path.endsWith('/messages')) { posts++; if (loseReceipt) throw new Error('synthetic lost response'); value = { run_id: 'confirmed-run' }; }
  else if (path.endsWith('/stop')) value = { status: 'stopped' };
  else if (path === '/api/reset') { snapshot.components = []; snapshot.cursor++; value = { ok: true }; }
  else if (path === '/api/files/sheet/preview') value = { kind: 'spreadsheet', sheets: [{ name: 'Budget', rows: [['Item', 'Amount'], ['Travel', '120']], row_numbers: [1, 2], truncated: false }], truncated: false };
  else if (path.startsWith('/api/files/note/preview')) {
    if (expandedNote) await delay(120);
    value = { kind: 'markdown', content: expandedNote ? '# An updated result\n\n' + Array.from({ length: 18 }, (_, index) => `Paragraph ${index + 1}. Keep reading earlier messages while this document arrives.`).join('\n\n') : '# A result in the conversation\nNo separate workspace is needed.', truncated: false };
  }
  else if (path === '/api/voice/status') value = { asr: false, tts: false };
  else { unexpected.push(path); value = {}; }
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
};
const emit = () => { snapshot.cursor++; for (const source of sources.filter(item => item.readyState === 1 && item.url.startsWith('/api/sessions/s1/events'))) source.onmessage?.({ data: JSON.stringify({ seq: snapshot.cursor, type: 'component', payload: {} }) }); };
const check = (name, pass, detail = undefined) => { window.webkit.messageHandlers.probe.postMessage({progress:name}); results[name] = { pass: Boolean(pass), detail }; if (!pass) throw new Error(name); };
const until = async (read, name) => {
  const deadline = performance.now() + 6000;
  do { if (read()) return; await delay(); } while (performance.now() < deadline);
  throw new Error(`timeout:${name}; visibility=${document.visibilityState}; artifacts=${document.querySelectorAll('.artifact-result').length}`);
};
const capture = async (name = 'surface') => {
  const captured = new Promise((resolve, reject) => window.addEventListener('probe-captured', event => {
    if (event.detail?.error) reject(new Error(event.detail.error)); else resolve(undefined);
  }, { once: true }));
  window.webkit.messageHandlers.probe.postMessage({ capture: name });
  await captured;
};
const resize = async (width, height) => {
  window.webkit.messageHandlers.probe.postMessage({ resize: { width, height } });
  await until(() => innerWidth === width && innerHeight === height, `viewport ${width}x${height}`);
  await delay(450);
};
const rect = selector => {
  const value = document.querySelector(selector).getBoundingClientRect();
  return { x: value.x, y: value.y, width: value.width, height: value.height, bottom: value.bottom };
};
const shellRects = () => ({ brand: rect('.brand'), presence: rect('.agent-presence'), emblem: rect('.orb-scene'), composer: rect('.composer-area'), input: rect('.composer textarea') });
const sameRects = (first, second) => Object.keys(first).every(key => Object.keys(first[key]).every(axis => Math.abs(first[key][axis] - second[key][axis]) < 1));
async function checkFixedShell(name) {
  const scroll = document.querySelector('.scroll-area');
  const textarea = document.querySelector('.composer textarea');
  const changeDraft = (value, composing = false) => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, value);
    if (composing) textarea.setSelectionRange(10, 18);
    textarea.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', isComposing: composing }));
  };
  changeDraft(''); await delay(80);
  scroll.dispatchEvent(new WheelEvent('wheel', { deltaY: -800, bubbles: true }));
  scroll.scrollTo({ top: 0 }); await delay(450);
  const before = shellRects(), messageBefore = rect('[data-message-id="m1"]');
  scroll.dispatchEvent(new WheelEvent('wheel', { deltaY: 240, bubbles: true }));
  scroll.scrollTo({ top: 240 }); await delay(450);
  const after = shellRects(), messageAfter = rect('[data-message-id="m1"]');
  check(`${name}_shell_stays_fixed_while_messages_scroll`, sameRects(before, after) && messageBefore.y - messageAfter.y > 200,
    { viewport: [innerWidth, innerHeight], before, after, messageDelta: messageBefore.y - messageAfter.y });
  // Programmatic over-scroll proves the document has no scrollable range.
  // Synthetic wheel events exercise application handlers, not physical trackpad momentum.
  for (const top of [-1000, 100000]) {
    scroll.dispatchEvent(new WheelEvent('wheel', { deltaY: top, bubbles: true }));
    scroll.scrollTo({ top }); window.scrollTo({ top, left: 100000 }); await delay(100);
  }
  check(`${name}_document_cannot_scroll_or_move_shell`, window.scrollY === 0 && window.scrollX === 0 && document.documentElement.scrollTop === 0 && sameRects(before, shellRects())
    && document.documentElement.scrollHeight <= innerHeight && document.body.scrollHeight <= innerHeight
    && document.documentElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth && getComputedStyle(scroll).overscrollBehavior === 'none',
    { windowY: scrollY, documentHeight: document.documentElement.scrollHeight, bodyHeight: document.body.scrollHeight, viewportHeight: innerHeight });
  changeDraft(Array.from({ length: 12 }, (_, index) => `Draft line ${index + 1}`).join('\n')); await delay(80);
  const grown = shellRects(), maximum = parseFloat(getComputedStyle(textarea).maxHeight);
  check(`${name}_multiline_draft_keeps_input_visible`, sameRects({ brand: before.brand, presence: before.presence }, { brand: grown.brand, presence: grown.presence })
    && Math.abs(before.composer.bottom - grown.composer.bottom) < 1 && Math.abs(before.input.bottom - grown.input.bottom) < 1
    && grown.input.height > before.input.height + 40 && grown.input.height <= maximum && rect('.composer').bottom <= innerHeight && textarea.scrollHeight > textarea.clientHeight,
    { composer: rect('.composer'), input: rect('.composer textarea'), draftLines: textarea.value.split('\n').length });
  textarea.focus({ preventScroll: true });
  textarea.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
  changeDraft(textarea.value + '\n中文', true); await delay(80);
  const enter = new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }), previousPosts = posts;
  textarea.dispatchEvent(enter);
  check(`${name}_composition_keeps_focus_selection_and_draft`, document.activeElement === textarea && textarea.selectionStart === 10 && textarea.selectionEnd === 18
    && textarea.value.endsWith('中文') && !enter.defaultPrevented && posts === previousPosts);
  textarea.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
  let styleChanges = 0;
  const observer = new MutationObserver(records => { styleChanges += records.length; });
  observer.observe(textarea, { attributes: true, attributeFilter: ['style'] });
  snapshot.messages[1].content += ' A streamed update.'; emit();
  scroll.dispatchEvent(new WheelEvent('wheel', { deltaY: -80, bubbles: true })); scroll.scrollBy({ top: -80 }); await delay(150);
  observer.disconnect();
  check(`${name}_streaming_and_reading_do_not_resize_draft`, styleChanges === 0 && sameRects(grown, shellRects()), { styleChanges });
  await capture(`shell-${name}`);
  changeDraft(''); await delay(80);
  const empty = shellRects();
  check(`${name}_empty_draft_returns_to_single_line`, Math.abs(empty.input.height - before.input.height) < 1 && Math.abs(empty.input.bottom - before.input.bottom) < 1 && textarea.value === '' && textarea.scrollTop === 0,
    { singleLine: before.input, cleared: empty.input });
}
const results = {};
const html = `<style>body{background:#f3efe7;color:#302d37;font-family:Georgia,serif;padding:32px!important}h1{font-weight:400}#messages{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{padding:24px;border-top:1px solid #857789}textarea{display:block;width:100%;height:80px;margin:24px 0}#shark{width:180px;height:66px;background:#798c99;clip-path:polygon(0 50%,80% 0,80% 35%,100% 10%,100% 90%,80% 65%,80% 100%);margin-bottom:24px}</style><h1>A different way to work</h1><div id="shark"></div><div id="messages"></div><textarea aria-label="Custom draft"></textarea><button id="send">Send</button><script>
const surface=Athena.surface;
let draft;
async function ready(){ const page=await surface.messages(); for(const m of page.items){const item=document.createElement('article');item.textContent=m.content;document.querySelector('#messages').append(item)} draft=await surface.draft();document.querySelector('textarea').value=draft.text;parent.postMessage({probe:'ready',text:draft.text,count:page.items.length},'*'); }
addEventListener('message',async event=>{ if(event.source!==parent || !event.data.probeCommand)return; try {
 const command=event.data.probeCommand;
 if(command==='edit'){draft=await surface.writeDraft({text:'A draft from the custom surface',expected_revision:draft.revision}); document.querySelector('textarea').value=draft.text;parent.postMessage({probe:'edited'},'*')}
 if(command==='send'){try{const receipt=await surface.request({action:'send',expected_revision:draft.revision});parent.postMessage({probe:'sent',receipt},'*')}catch(error){parent.postMessage({probe:'send-error',code:error.message},'*')}}
 if(command==='crash')setTimeout(()=>{throw new Error('synthetic renderer crash')},0);
 if(command==='inspect'){const rows=[...document.querySelectorAll('article')].map(el=>({x:el.getBoundingClientRect().x,y:el.getBoundingClientRect().y}));parent.postMessage({probe:'layout',rows},'*')}
 if(command==='compose'){const rows=[...document.querySelectorAll('article')].map((el,index)=>{el.style.transform=index?'translate(-40px, 74px) rotate(17deg)':'translate(60px, 32px) rotate(-11deg)';el.style.opacity=index?'0.42':'0.73';const rect=el.getBoundingClientRect(),style=getComputedStyle(el);return {x:rect.x,y:rect.y,width:rect.width,height:rect.height,transform:style.transform,opacity:style.opacity,text:el.textContent}});parent.postMessage({probe:'composed',rows},'*')}
 }catch(error){parent.postMessage({probe:'failed',error:String(error)},'*')} }); ready();
</script>`;
let replies = [];
window.addEventListener('message', event => { if (event.source === document.querySelector('.session-surface iframe')?.contentWindow && event.data?.probe) replies.push(event.data); });
const command = value => document.querySelector('.session-surface iframe').contentWindow.postMessage({ probeCommand: value }, '*');
const reply = name => replies.find(item => item.probe === name);
createRoot(document.getElementById('root'), { onUncaughtError: error => window.webkit.messageHandlers.probe.postMessage({react_error:String(error),stack:error.stack}) }).render(<I18nProvider><App /></I18nProvider>);
async function run() {
  try {
    await until(() => document.querySelector('[data-message-id="m2"]'), 'initial conversation');
    const textarea = document.querySelector('.composer textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, 'Keep this unsent draft');
    textarea.dispatchEvent(new Event('input', { bubbles: true })); await delay();
    snapshot.components = [
      { id: 'older', title: 'Standby', html: '<p>Standby</p>', placement: 'surface', version: 1, view_revision: 0, created_at: '2026-01-01T00:00:00' },
      { id: 'board', title: 'Conversation board', html, placement: 'surface', version: 1, view_revision: 0, created_at: '2026-01-02T00:00:00' },
    ]; emit();
    await until(() => reply('ready'), 'live registered renderer');
    check('registration_mounts_without_refresh', document.querySelector('.session-surface') && !document.querySelector('.conversation'));
    check('canonical_messages_and_draft_available', reply('ready').count === 2 && reply('ready').text === 'Keep this unsent draft', reply('ready'));
    command('inspect'); await until(() => reply('layout'), 'arbitrary layout');
    check('messages_have_independent_layout', reply('layout').rows[0].x !== reply('layout').rows[1].x, reply('layout'));
    command('compose'); await until(() => reply('composed'), 'free message composition');
    const composed = reply('composed').rows, positions = reply('layout').rows;
    check('messages_have_free_position_rotation_and_opacity', composed.every((row, index) => {
      const matrix = new DOMMatrixReadOnly(row.transform), radians = (index ? 17 : -11) * Math.PI / 180;
      return Math.abs(row.x - positions[index].x) > 1 && Math.abs(row.y - positions[index].y) > 1
        && Math.abs(matrix.a - Math.cos(radians)) < 0.001 && Math.abs(matrix.b - Math.sin(radians)) < 0.001
        && Math.abs(matrix.e - (index ? -40 : 60)) < 0.001 && Math.abs(matrix.f - (index ? 74 : 32)) < 0.001
        && Number(row.opacity) === (index ? 0.42 : 0.73) && row.text === snapshot.messages[index].content;
    }) && snapshot.messages.length === 2, composed);
    check('protected_stop_reset_remain', document.querySelector('.topbar button[aria-label^="Reset"]') && [...document.querySelectorAll('.topbar button')].some(item => item.textContent === 'Stop'));
    await capture();
    command('edit'); await until(() => reply('edited'), 'shared draft write');
    snapshot.components[1].view_revision++; emit(); await delay(500);
    check('layout_revision_keeps_mounted_receipt', observations.at(-1)?.surface?.state === 'mounted', observations.at(-1)?.surface);
    loseReceipt = true; command('send'); await until(() => reply('send-error'), 'unknown delivery');
    check('unknown_send_not_retried', reply('send-error').code === 'delivery_unknown' && posts === 1, reply('send-error'));
    failHide = true;
    [...document.querySelectorAll('.topbar button')].find(item => item.textContent === 'Back to conversation').click();
    await delay(400);
    check('return_never_mounts_standby_even_when_hide_fails', !document.querySelector('.session-surface'));
    check('default_draft_survives_unknown_receipt_and_return', document.querySelector('.composer textarea').value === 'A draft from the custom surface');
    failHide = false; replies = [];
    snapshot.components[1].version++; snapshot.components[1].view = { hidden: false }; emit();
    await until(() => reply('ready'), 'new renderer version'); command('crash');
    await until(() => !document.querySelector('.session-surface'), 'failed renderer fallback');
    check('script_failure_restores_default_and_draft', document.querySelector('.composer textarea').value === 'A draft from the custom surface');
    check('history_not_rewritten_by_renderer', snapshot.messages.length === 2 && document.querySelector('[data-message-id="m2"]'));
    snapshot.components = [{ id: 'chart', title: 'An inline chart', placement: 'workspace', version: 1, view_revision: 0, html: '<svg viewBox="0 0 200 80" role="img"><path stroke="#857789" fill="none" d="M0 70L40 45L80 65L140 15L200 35"/></svg>' }]; emit();
    await until(() => document.querySelector('[data-component-id="chart"]'), 'inline workspace chart');
    check('workspace_content_does_not_replace_conversation', document.querySelector('.conversation:not([hidden])') && !document.querySelector('.workspace-panel'));
    snapshot.files = ['sheet', 'note'].map((id, index) => ({ id, session_id: 's1', name: index ? 'result.md' : 'budget.xlsx', source: 'output', size: 40, path: '', media_type: index ? 'text/markdown' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', created_at: '', content_url: '/api/files/' + id + '/content', preview_url: '/api/files/' + id + '/preview' })); emit();
    await until(() => document.querySelector('.spreadsheet-preview table') && document.querySelector('.artifact-result h1'), 'inline documents');
    check('spreadsheet_and_markdown_render_in_conversation', document.querySelector('.conversation .spreadsheet-preview').textContent.includes('Travel') && document.querySelector('.conversation .artifact-result h1').textContent === 'A result in the conversation' && !document.querySelector('.preview-overlay'));
    const scroll = document.querySelector('.scroll-area');
    await until(() => scroll.scrollTop > 30, 'new output follows to bottom');
    const previousBottom = scroll.scrollHeight - scroll.clientHeight;
    // Synthetic wheel intent plus actual WebKit scrolling, not an OS input event.
    scroll.dispatchEvent(new WheelEvent('wheel', { deltaY: -600, bubbles: true }));
    scroll.scrollTo({ top: 0 }); await delay(80);
    const readingPosition = scroll.scrollTop;
    const shellBeforeUpdate = shellRects();
    expandedNote = true;
    snapshot.files = snapshot.files.map(file => file.id === 'note' ? { ...file, content_revision: 'expanded-note' } : file); emit();
    await until(() => document.querySelector('.artifact-result h1')?.textContent === 'An updated result', 'updated document body');
    await delay(200);
    check('new_document_does_not_pull_reader_away_from_history', previousBottom > 30 && scroll.scrollHeight - scroll.clientHeight > previousBottom + 30 && Math.abs(scroll.scrollTop - readingPosition) <= 1,
      { readingPosition, after: scroll.scrollTop, previousBottom, nextBottom: scroll.scrollHeight - scroll.clientHeight });
    check('late_document_preview_keeps_shell_fixed', sameRects(shellBeforeUpdate, shellRects()), { before: shellBeforeUpdate, after: shellRects() });
    await checkFixedShell('desktop');
    document.documentElement.style.setProperty('--presence-size', '112px'); await delay(450);
    check('explicit_presence_size_is_still_supported', Math.abs(rect('.orb-scene').width - 112) < 1, rect('.orb-scene'));
    document.documentElement.style.removeProperty('--presence-size'); await delay(450);
    await resize(390, 844);
    await checkFixedShell('narrow');
    // Native iOS keyboardLayoutGuide reduces WKWebView bounds. A shortened
    // viewport tests the same CSS response without claiming a real iOS keyboard.
    await resize(390, 500);
    await checkFixedShell('shortened');
    await resize(390, 844);
    check('viewport_restores_after_keyboard_sized_resize', Math.abs(rect('.composer-area').bottom - 844) < 1 && rect('.brand').y >= 0, shellRects());
    check('all_requests_handled_offline', unexpected.length === 0, unexpected);
  } catch (error) { results.error = String(error); }
  window.webkit.messageHandlers.probe.postMessage(results);
}
void run();
