import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import { act, createElement as h } from 'react';

const window = new Window({ url: 'https://athena.invalid', settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true, disableIframePageLoading: true, enableImageFileLoading: false } });
// Control media completion explicitly, including happy-dom's scheduled srcDoc
// load, so an old iframe's late event can be delivered before the current one.
window.requestAnimationFrame = () => 0;
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url), modules = new Map();
function load(name) {
  if (modules.has(name)) return modules.get(name).exports;
  const compiled = ts.transpileModule(readFileSync(new URL(`../src/${name}.tsx`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} }; modules.set(name, module);
  vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: name + '.tsx' })(dependency => {
    if (dependency.endsWith('.css')) return {};
    if (dependency === './i18n') return { useI18n: () => ({ t: key => key }) };
    if (dependency === './MessageBody') return { MarkdownContent: props => h('article', { className: 'markdown-content' }, props.content) };
    if (dependency.startsWith('./')) return load(dependency.slice(2));
    return require(dependency);
  }, module, module.exports);
  return module.exports;
}
const { FilePreview } = load('FilePreview'), { ArtifactResult } = load('ArtifactResult');
const file = (patch = {}) => ({ id: 'result', session_id: 'session', name: 'Report.md', path: 'Report.md', size: 8,
  media_type: 'text/markdown', source: 'output', created_at: '2026-10-04T00:00:00Z', content_url: '/api/files/result/content',
  preview_url: '/api/files/result/preview', content_revision: 's1-first', ...patch });
async function fixture() {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  return { host, render: async element => { await act(async () => root.render(element)); },
    close: async () => { await act(async () => root.unmount()); host.remove(); } };
}
const result = (content, kind = 'markdown') => ({ ok: true, json: async () => ({ kind, content, truncated: false }) });
const fire = async (node, type) => { assert.ok(node); await act(async () => node.dispatchEvent(new window.Event(type, { bubbles: true }))); };
after(() => window.happyDOM.abort());

test('inline artifacts retain unchanged previews through stream cursors and reload same-size edited Markdown', async () => {
  const requests = [], receipts = [], observe = value => receipts.push(value);
  globalThis.fetch = async url => { requests.push(url); return result(url.includes('s1-first') ? '# First' : '# Other'); };
  const page = await fixture();
  try {
    await page.render(h(ArtifactResult, { file: file(), selected: true, observe }));
    const original = page.host.querySelector('article');
    assert.equal(original.textContent, '# First');
    for (let cursor = 1; cursor <= 5; cursor++) {
      await page.render(h(ArtifactResult, { file: file(), selected: true, observe }));
      assert.equal(page.host.querySelector('article'), original);
    }
    assert.deepEqual(requests, ['/api/files/result/preview?revision=s1-first']);
    assert.deepEqual(receipts.map(item => item.status), ['loading', 'ready']);
    await page.render(h(ArtifactResult, { file: file({ content_revision: 's1-second' }), selected: true, observe }));
    assert.equal(page.host.querySelector('article').textContent, '# Other');
    assert.notEqual(page.host.querySelector('article'), original);
    assert.deepEqual(requests, ['/api/files/result/preview?revision=s1-first', '/api/files/result/preview?revision=s1-second']);
    assert.deepEqual(receipts.map(item => item.status), ['loading', 'ready', 'loading', 'ready']);
    assert.equal(page.host.querySelector('a[download]').getAttribute('href'), '/api/files/result/content?download=1');
    assert.equal(page.host.querySelector('dialog'), null);
  } finally { await page.close(); }
});

test('a late fetch for the old bytes is aborted and cannot replace a newer revision of the same file', async () => {
  const requests = [], pending = [], page = await fixture();
  globalThis.fetch = (url, options) => { requests.push({ url, signal: options.signal }); return new Promise(resolve => pending.push(resolve)); };
  try {
    await page.render(h(FilePreview, { file: file() }));
    await page.render(h(FilePreview, { file: file({ content_revision: 's1-second' }) }));
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests[1].signal.aborted, false);
    await act(async () => pending[1](result('# Current')));
    assert.equal(page.host.querySelector('article').textContent, '# Current');
    await act(async () => pending[0](result('# Outdated')));
    assert.equal(page.host.querySelector('article').textContent, '# Current');
  } finally { await page.close(); }
});

for (const kind of ['html', 'image']) {
  test(`the previous ${kind} load event cannot acknowledge a new revision`, async () => {
    const page = await fixture(), receipts = [], observe = value => receipts.push(value);
    globalThis.fetch = async () => ({ ok: true, json: async () => kind === 'image'
      ? { kind: 'image', url: '/api/files/result/content' }
      : { kind: 'html', content: '<h1>Report</h1>', truncated: false } });
    try {
      await page.render(h(FilePreview, { file: file(), onObservation: observe }));
      const previous = page.host.querySelector(kind === 'html' ? 'iframe' : 'img');
      await page.render(h(FilePreview, { file: file({ content_revision: 's1-second' }), onObservation: observe }));
      const current = page.host.querySelector(kind === 'html' ? 'iframe' : 'img');
      assert.notEqual(current, previous);
      if (kind === 'image') assert.equal(current.getAttribute('src'), '/api/files/result/content?revision=s1-second');
      await fire(previous, 'load');
      assert.equal(receipts.at(-1).status, 'loading');
      await fire(current, 'load');
      assert.equal(receipts.at(-1).status, 'ready');
    } finally { await page.close(); }
  });
}

test('a changed file conditional-read failure remains visible and does not trigger a retry loop', async () => {
  const page = await fixture(), receipts = []; let requests = 0;
  globalThis.fetch = async () => { requests++; return { ok: false, status: 409 }; };
  try {
    await page.render(h(ArtifactResult, { file: file(), selected: true, observe: value => receipts.push(value) }));
    assert.match(page.host.textContent, /files.unavailable/);
    assert.equal(receipts.at(-1).status, 'failed');
    assert.ok(page.host.querySelector('a[download]'));
    await page.render(h(ArtifactResult, { file: file(), selected: true }));
    assert.equal(requests, 1);
  } finally { await page.close(); }
});

test('an image artifact forwards revision changes without replacing its containing result', async () => {
  const page = await fixture(), patch = { name: 'Picture.png', media_type: 'image/png' };
  try {
    await page.render(h(ArtifactResult, { file: file(patch), selected: true }));
    const artifact = page.host.querySelector('.artifact-result'), first = artifact.querySelector('img');
    await fire(first, 'load');
    await page.render(h(ArtifactResult, { file: file({ ...patch, content_revision: 's1-second' }), selected: true }));
    assert.equal(page.host.querySelector('.artifact-result'), artifact);
    assert.notEqual(artifact.querySelector('img'), first);
    assert.equal(artifact.querySelector('img').getAttribute('src'), '/api/files/result/content?revision=s1-second');
    assert.equal(page.host.querySelector('dialog'), null);
  } finally { await page.close(); }
});
