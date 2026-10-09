import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import { act, createElement as h } from 'react';

const window = new Window({ url: 'https://athena.invalid', settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true, disableIframePageLoading: true, enableImageFileLoading: false } });
window.requestAnimationFrame = () => 0;
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node'])
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url), modules = new Map(), native = [];
function load(name) {
  if (modules.has(name)) return modules.get(name).exports;
  const compiled = ts.transpileModule(readFileSync(new URL(`../src/${name}.tsx`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} }; modules.set(name, module);
  vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: name + '.tsx' })(dependency => {
    if (dependency.endsWith('.css')) return {};
    if (dependency === './i18n') return { useI18n: () => ({ t: key => key }) };
    if (dependency === './desktop-bridge') return { desktopPost: message => { native.push(message); return true; } };
    if (dependency === './MessageBody') return { MarkdownContent: props => h('article', { className: 'markdown-content' }, props.content) };
    if (dependency.startsWith('./')) return load(dependency.slice(2));
    return require(dependency);
  }, module, module.exports);
  return module.exports;
}
const DesktopArtifact = load('DesktopArtifact').default;
const file = (patch = {}) => ({ id: 'result', session_id: 'session', name: 'Report.md', path: 'Report.md', size: 8,
  media_type: 'text/markdown', source: 'output', created_at: '2026-10-10T00:00:00Z', content_url: '/api/files/result/content',
  preview_url: '/api/files/result/preview', content_revision: 's1-first', ...patch });
async function fixture() {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  return { host, render: async element => { await act(async () => root.render(element)); },
    close: async () => { await act(async () => root.unmount()); host.remove(); } };
}
const fire = async (node, type) => { assert.ok(node); await act(async () => node.dispatchEvent(new window.Event(type, { bubbles: true }))); };
after(() => window.happyDOM.abort());

test('desktop surface renders canonical spreadsheet, document, image, HTML and text previews without a chat wrapper', async () => {
  const samples = [
    ['Book.xlsx', { kind: 'spreadsheet', sheets: [{ name: 'Expenses', rows: [['67']], row_numbers: [5], truncated: false }], truncated: false }, 'table'],
    ['Plan.docx', { kind: 'document', blocks: [{ kind: 'paragraph', heading: 1, text: 'A useful plan' }], truncated: false }, 'h1'],
    ['Chart.html', { kind: 'html', content: '<h1>My chart</h1>', truncated: false }, 'iframe[sandbox=""]'],
    ['Photo.png', { kind: 'image', url: '/api/files/result/content' }, 'img'],
    ['Notes.md', { kind: 'markdown', content: 'A useful note', truncated: false }, '.markdown-content'],
  ];
  for (const [name, preview, selector] of samples) {
    const page = await fixture(), observations = [], requests = [];
    globalThis.fetch = async url => { requests.push(url); return { ok: true, json: async () => preview }; };
    try {
      await page.render(h(DesktopArtifact, { file: file({ name }), onObservation: value => observations.push(value) }));
      const surface = page.host.querySelector('.desktop-artifact');
      assert.ok(surface.querySelector(selector), name);
      assert.equal(surface.closest('.companion-result,.message,dialog'), null);
      assert.deepEqual(requests, ['/api/files/result/preview?revision=s1-first']);
      if (preview.kind === 'image' || preview.kind === 'html') {
        assert.notEqual(observations.at(-1).status, 'ready', 'Media must load before confirming it is ready');
        await fire(surface.querySelector(selector), 'load');
      }
      assert.equal(observations.at(-1).status, 'ready');
      assert.equal(page.host.querySelector('[data-desktop-drag] button'), null, 'Native drag overlay must not cover controls');
    } finally { await page.close(); }
  }
});

test('desktop controls route to native host and maximize state follows its real receipt', async () => {
  const page = await fixture(); native.length = 0;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ kind: 'text', content: 'hello' }) });
  try {
    await page.render(h(DesktopArtifact, { file: file(), onObservation: () => {} }));
    assert.ok(native.some(item => item.action === 'hitRegions'));
    assert.ok(native.some(item => item.action === 'surfaceDragRegions' && item.regions.length === 1));
    await fire(page.host.querySelector('button[aria-label="files.download"]'), 'click');
    await fire(page.host.querySelector('button[aria-label="desktop.maximize"]'), 'click');
    assert.ok(page.host.querySelector('button[aria-label="desktop.maximize"]'), 'No optimistic success before native receipt');
    await act(async () => window.dispatchEvent(new window.CustomEvent('athena:surface', { detail: { maximized: true } })));
    await fire(page.host.querySelector('button[aria-label="desktop.restoreSize"]'), 'click');
    await fire(page.host.querySelector('button[aria-label="files.close"]'), 'click');
    assert.deepEqual(native.filter(item => !item.action.endsWith('Regions')).map(item => item.action), ['downloadFile', 'maximizeWidget', 'maximizeWidget', 'closeWidget']);
  } finally { await page.close(); }
});

test('failed file preview offers retry and preserves the desktop controls throughout', async () => {
  const page = await fixture(), observations = []; let available = false;
  globalThis.fetch = async () => ({ ok: available, json: async () => ({ kind: 'text', content: 'Recovered result' }) });
  try {
    await page.render(h(DesktopArtifact, { file: file(), onObservation: value => observations.push(value) }));
    const close = page.host.querySelector('button[aria-label="files.close"]');
    assert.equal(observations.at(-1).status, 'failed');
    available = true;
    await fire([...page.host.querySelectorAll('button')].find(button => button.textContent === 'retry'), 'click');
    assert.equal(observations.at(-1).status, 'ready');
    assert.equal(page.host.querySelector('.file-text-preview').textContent, 'Recovered result');
    assert.equal(page.host.querySelector('button[aria-label="files.close"]'), close);
  } finally { await page.close(); }
});
