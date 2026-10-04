// Production App with a complete renderer. Offline WKWebView; synthetic accounts only.
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '../../src/App';
import { I18nProvider } from '../../src/i18n';
import { FilePreview } from '../../src/FilePreview';
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
let posts = 0;
const observations = [], unexpected = [];
window.fetch = async (path, init) => {
  path = String(path); const body = init?.body ? JSON.parse(init.body) : undefined;
  let value;
  if (path === '/api/auth/me') value = { user: { id: 'probe', username: 'synthetic', developer_mode: false }, configured: true, has_users: true };
  else if (path.startsWith('/api/service')) value = { configured: true };
  else if (path === '/api/sessions') value = [{ id: 's1', title: 'Synthetic workspace', pending: 0 }];
  else if (path === '/api/sessions/s1') value = structuredClone(snapshot);
  else if (path.startsWith('/api/activity')) value = { owner_id: 'probe', items: [], has_more: false };
  else if (path === '/api/frontend/observation') { observations.push(body); value = { accepted: true }; }
  else if (path.endsWith('/report')) value = { accepted: true };
  else if (path.endsWith('/messages')) { posts++; value = { run_id: 'confirmed-run' }; }
  else if (path === '/api/files/modal-note/preview') value = { kind: 'markdown', content: '![Synthetic diagram](/api/files/modal-image/content)', truncated: false };
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
const resize = async (width, height) => {
  window.webkit.messageHandlers.probe.postMessage({ resize: { width, height } });
  await until(() => innerWidth === width && innerHeight === height, `viewport ${width}x${height}`);
  await delay(450);
};
const rect = selector => {
  const value = document.querySelector(selector).getBoundingClientRect();
  return { x: value.x, y: value.y, width: value.width, height: value.height, bottom: value.bottom };
};
const results = {};
const html = `<style>html,body{height:100%;overflow:hidden}body{display:flex;flex-direction:column;padding:12px!important}#read{flex:1;min-height:0;overflow:auto}article{padding:18px;border-bottom:1px solid #ccc}textarea{height:70px;flex-shrink:0;width:100%;margin:10px 0}#shark{width:80px;height:35px;background:#8b95a7;clip-path:polygon(0 50%,80% 0,80% 35%,100% 10%,100% 90%,80% 65%,80% 100%)}</style><div id="read"><div id="shark"></div><div id="messages"></div></div><textarea aria-label="Surface draft"></textarea><script>
const field=document.querySelector('textarea'), read=document.querySelector('#read'), node=document.querySelector('#messages');
let draft, writes=Promise.resolve(), refreshes=0, inputs=0, enters=0;
const mount=String(Math.random());
async function refresh(){const page=await Athena.surface.messages();for(const message of page.items){let item=document.getElementById('m-'+message.id);if(!item){item=document.createElement('article');item.id='m-'+message.id;node.append(item)}item.textContent=message.content}refreshes++}
field.addEventListener('input',event=>{if(event.isTrusted)inputs++;const text=field.value;writes=writes.then(async()=>{draft=await Athena.surface.writeDraft({text,expected_revision:draft.revision})})});
field.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();if(event.isTrusted)enters++}});
const state=()=>({mount,inputs,enters,refreshes,active:document.activeElement===field,documentFocused:document.hasFocus(),scroll:read.scrollTop,text:field.value,selection:[field.selectionStart,field.selectionEnd],rect:{x:field.getBoundingClientRect().x,y:field.getBoundingClientRect().y,width:field.getBoundingClientRect().width,height:field.getBoundingClientRect().height},last:node.lastElementChild?.textContent});
addEventListener('message',async event=>{if(event.source!==parent||!event.data.command)return;const {command,token}=event.data;try{
if(command==='open')await Athena.surface.request({action:'open',target:'history'});
if(command==='scroll'){read.scrollTop=180;field.setSelectionRange(3,8)}
if(command==='state')await writes;
parent.postMessage({probe:'modal-reply',token,value:state()},'*');
}catch(error){parent.postMessage({probe:'modal-reply',token,error:String(error)},'*')}});
(async()=>{await refresh();draft=await Athena.surface.draft();draft=await Athena.surface.writeDraft({text:'A retained custom draft',expected_revision:draft.revision});field.value=draft.text;Athena.surface.subscribe(()=>void refresh());parent.postMessage({probe:'modal-ready'},'*')})();
</script>`;
const replies = new Map(); let sequence = 0, ready = false;
const nativeKeys = []; document.addEventListener('keydown', event => nativeKeys.push({ key: event.key, trusted: event.isTrusted }), true);
window.addEventListener('message', event => {
  if (event.source !== document.querySelector('.session-surface iframe')?.contentWindow) return;
  if (event.data?.probe === 'modal-ready') ready = true;
  if (event.data?.probe === 'modal-reply') replies.set(event.data.token, event.data);
});
const command = async value => {
  const token = ++sequence;
  document.querySelector('.session-surface iframe').contentWindow.postMessage({ command: value, token }, '*');
  await until(() => replies.has(token), value); const reply = replies.get(token); replies.delete(token);
  if (reply.error) throw new Error(reply.error); return reply.value;
};
const nativeKey = async (key, shift = false) => { window.webkit.messageHandlers.probe.postMessage({ nativeKey: key, shift }); await delay(100); };
const nativeClick = async element => {
  const bounds = element.getBoundingClientRect();
  window.webkit.messageHandlers.probe.postMessage({ nativeClick: { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 } });
  await delay(100);
};
const clickField = async () => {
  const field = (await command('state')).rect, frame = rect('.session-surface iframe');
  window.webkit.messageHandlers.probe.postMessage({ nativeClick: { x: frame.x + field.x + Math.min(20, field.width / 2), y: frame.y + field.y + Math.min(20, field.height / 2) } });
  await delay(100);
};
const inertWithin = element => Boolean(element?.closest('[inert]'));
createRoot(document.getElementById('root'), { onUncaughtError: error => window.webkit.messageHandlers.probe.postMessage({ react_error: String(error) }) }).render(<I18nProvider><App /></I18nProvider>);
async function run() {
  try {
    await until(() => document.querySelector('[data-message-id="m2"]'), 'initial');
    snapshot.messages.push(...Array.from({ length: 25 }, (_, index) => ({ id: `long-${index}`, role: 'assistant', content: `Reading passage ${index + 1}. The custom surface owns this layout.`, status: 'complete' })));
    snapshot.components = [{ id: 'board', title: 'Modal probe', html, placement: 'surface', version: 1, view_revision: 0, created_at: '2026-01-02' }]; emit();
    await until(() => ready, 'surface ready');
    const iframe = document.querySelector('.session-surface iframe');
    const instance = document.querySelector('.session-surface .widget').dataset.componentInstance;
    const initial = await command('state');
    for (const [label, width, height] of [['desktop', 1080, 780], ['mobile_short', 390, 500]]) {
      await resize(width, height); await clickField();
      const beforeInput = await command('state'); await nativeKey('z');
      const focused = await command('state');
      check(`${label}_native_input_calibrated`, focused.active && focused.inputs === beforeInput.inputs + 1 && focused.text !== beforeInput.text,
        { active: focused.active, beforeInputs: beforeInput.inputs, afterInputs: focused.inputs });
      await command('scroll'); const before = await command('state');
      await command('open'); await until(() => document.querySelector('[data-app-view="history"]'), 'history'); await delay(80);
      const dialog = document.querySelector('[data-app-view="history"]');
      check(`${label}_modal_owns_focus_and_background_is_inert`, dialog.contains(document.activeElement) && inertWithin(iframe), { activeTag: document.activeElement.tagName, inert: inertWithin(iframe) });
      await nativeKey('z'); await nativeKey('Enter');
      // Native Tab events reach WebKit, unlike dispatching a synthetic DOM keydown.
      for (let index = 0; index < 6; index++) { await nativeKey('Tab', index % 2 === 0); check(`${label}_tab_${index}_stays_in_dialog`, nativeKeys.at(-1)?.key === 'Tab' && nativeKeys.at(-1)?.trusted && dialog.contains(document.activeElement), nativeKeys.at(-1)); }
      const covered = await command('state');
      check(`${label}_covered_renderer_receives_no_native_input`, covered.inputs === before.inputs && covered.enters === before.enters && covered.text === before.text, { inputDelta: covered.inputs - before.inputs, enterDelta: covered.enters - before.enters });
      snapshot.messages.at(-1).content = `${label}: streamed while the fixed dialog is open`; snapshot.components[0].view_revision++; emit();
      await delay(180); const streamed = await command('state');
      check(`${label}_stream_and_layout_revision_preserve_renderer`, document.querySelector('.session-surface iframe') === iframe
        && document.querySelector('.session-surface .widget').dataset.componentInstance === instance && streamed.mount === initial.mount
        && streamed.last === snapshot.messages.at(-1).content && streamed.scroll === before.scroll && streamed.text === before.text
        && observations.at(-1)?.surface?.state === 'mounted');
      // Close through the dedicated native test window, as a foreground user would.
      await nativeClick(dialog.querySelector('.dialog-top button')); await until(() => !document.querySelector('[data-app-view="history"]'), 'closed'); await delay(100);
      const restored = await command('state');
      check(`${label}_closing_restores_same_surface_and_draft`, document.activeElement === iframe && restored.active && !inertWithin(iframe)
        && restored.mount === initial.mount && restored.scroll === before.scroll && restored.text === before.text
        && JSON.stringify(restored.selection) === JSON.stringify(before.selection),
        { parentActive: document.activeElement.tagName, parentFocused: document.hasFocus(), childFocused: restored.documentFocused, fieldActive: restored.active, sameMount: restored.mount === initial.mount, beforeSelection: before.selection, afterSelection: restored.selection });
      await nativeKey('z'); const resumed = await command('state');
      check(`${label}_native_input_resumes_once`, resumed.inputs === before.inputs + 1 && resumed.text !== before.text);
      const reset = document.querySelector('.topbar button[aria-label^="Reset"]'), bounds = reset.getBoundingClientRect();
      check(`${label}_independent_reset_is_reachable`, !inertWithin(reset) && !reset.disabled && bounds.top >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight);

      const menuButton = document.querySelector('.topbar button[aria-label="Menu"]');
      // macOS WebKit does not necessarily focus a button on pointer activation.
      // Give this keyboard-navigation case an explicit, observable invoker.
      menuButton.focus();
      check(`${label}_menu_invoker_focus_calibrated`, document.activeElement === menuButton);
      await nativeClick(menuButton); await until(() => document.querySelector('[data-app-view="menu"]'), 'menu');
      const appearanceButton = [...document.querySelectorAll('[data-app-view="menu"] .menu-items button')].find(button => button.textContent.includes('Language & motion'));
      appearanceButton.focus(); appearanceButton.click();
      await until(() => document.querySelector('[data-app-view="appearance"]') && !document.querySelector('[data-app-view="menu"]'), 'menu replaced');
      const appearance = document.querySelector('[data-app-view="appearance"]');
      check(`${label}_replacement_dialog_owns_focus`, appearance.contains(document.activeElement) && inertWithin(iframe));
      await nativeClick(appearance.querySelector('.dialog-top button')); await until(() => !document.querySelector('[data-app-view="appearance"]'), 'appearance closed'); await delay(80);
      check(`${label}_replacement_returns_to_original_menu_invoker`, document.activeElement === menuButton && !inertWithin(iframe), { activeTag: document.activeElement.tagName, activeLabel: document.activeElement.getAttribute('aria-label') });

      await clickField(); await command('scroll'); const beforeZoom = await command('state');
      await command('open'); await until(() => document.querySelector('[data-app-view="history"]'), 'history for native modal');
      const parentDialog = document.querySelector('[data-app-view="history"]');
      // Exercise the production FilePreview -> Markdown -> ImageZoom portal in a
      // real host dialog. Only image readiness is synthetic; this fixture tests
      // keyboard routing and native showModal, not resource loading or decoding.
      const mount = document.createElement('div'); parentDialog.append(mount);
      const previewRoot = createRoot(mount);
      previewRoot.render(<I18nProvider><FilePreview file={{ id: 'modal-note', name: 'Synthetic note.md', size: 64, media_type: 'text/markdown', source: 'output', preview_url: '/api/files/modal-note/preview', content_url: '/api/files/modal-note/content' }} /></I18nProvider>);
      await until(() => mount.querySelector('.inline-image-open img'), 'markdown image');
      mount.querySelector('.inline-image-open img').dispatchEvent(new Event('load'));
      await until(() => !mount.querySelector('.inline-image-open').disabled, 'synthetic image ready');
      const imageButton = mount.querySelector('.inline-image-open'); imageButton.focus(); imageButton.click();
      await until(() => document.querySelector('dialog.image-lightbox:modal'), 'native modal'); await delay(80);
      const lightbox = document.querySelector('dialog.image-lightbox');
      check(`${label}_native_portal_owns_focus_above_host_dialog`, lightbox.contains(document.activeElement) && !inertWithin(lightbox));
      for (let index = 0; index < 4; index++) {
        const keyCount = nativeKeys.length, documentFocused = document.hasFocus();
        await nativeKey('Tab', index % 2 === 0);
        // Native dialog Tab may give focus to the browser/window itself. It must
        // never be redirected into the underlying host dialog or surface.
        check(`${label}_native_portal_tab_${index}`, !parentDialog.contains(document.activeElement) && document.activeElement !== iframe
          && (nativeKeys.length > keyCount || document.hasFocus() !== documentFocused)
          && nativeKeys.at(-1)?.key === 'Tab' && nativeKeys.at(-1)?.trusted,
          { activeTag: document.activeElement.tagName, insideLightbox: lightbox.contains(document.activeElement), documentFocused: document.hasFocus(), receivedKeyCount: nativeKeys.length - keyCount, lastKey: nativeKeys.at(-1) });
      }
      await nativeClick(lightbox.querySelector('button')); await until(() => !document.querySelector('dialog.image-lightbox'), 'native modal closed'); await delay(80);
      check(`${label}_native_portal_returns_to_image_control`, document.activeElement === imageButton && inertWithin(iframe));
      previewRoot.unmount(); mount.remove();
      await nativeClick(parentDialog.querySelector('.dialog-top button')); await until(() => !document.querySelector('[data-app-view="history"]'), 'history after native modal closed'); await delay(80);
      const afterZoom = await command('state');
      check(`${label}_native_portal_preserves_surface_draft_scroll_and_selection`, document.activeElement === iframe && afterZoom.active && afterZoom.mount === initial.mount
        && afterZoom.scroll === beforeZoom.scroll && afterZoom.text === beforeZoom.text && JSON.stringify(afterZoom.selection) === JSON.stringify(beforeZoom.selection));
    }
    check('no_messages_submitted_by_modal_navigation', posts === 0);
    check('all_requests_are_synthetic', unexpected.length === 0, unexpected);
  } catch (error) { results.error = String(error); }
  window.webkit.messageHandlers.probe.postMessage(results);
}
void run();
