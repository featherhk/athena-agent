import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import { act, createElement as h } from 'react';

// Real React/image lifecycle events, with no network or provider requests.
const window = new Window({ url: 'https://athena.invalid', settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true, enableImageFileLoading: false } });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/ImageResult.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const module = { exports: {} };
vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: 'ImageResult.tsx' })(name => {
  if (name.endsWith('.css')) return {};
  if (name === './i18n') return { useI18n: () => ({ t: (key, values) => `${key}${values?.name ? ':' + values.name : ''}` }) };
  return require(name);
}, module, module.exports);
const { ImageResult, InlineImage, isImageFile, ownedImageSource } = module.exports;
const file = (patch = {}) => ({ id: 'image_one', session_id: 'session', name: 'Result.png', path: 'Result.png', size: 19000000,
  media_type: 'image/png', source: 'output', created_at: '2026-10-04T00:00:00Z', content_url: '/api/files/image_one/content', preview_url: '/api/files/image_one/preview', ...patch });
async function mount() {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  return { host, render: async element => { await act(async () => root.render(element)); },
    close: async () => { await act(async () => root.unmount()); host.remove(); } };
}
const fire = async (element, type) => { assert.ok(element); await act(async () => element.dispatchEvent(new window.Event(type, { bubbles: true }))); };
const click = async element => { assert.ok(element); await act(async () => element.click()); };
after(() => window.happyDOM.abort());

test('only supported owned raster images enter the inline result path', () => {
  for (const media_type of ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif']) assert.equal(isImageFile(file({ media_type })), true);
  for (const media_type of ['image/svg+xml', 'text/html', 'application/pdf', 'image/unknown']) assert.equal(isImageFile(file({ media_type })), false);
  for (const content_url of ['https://elsewhere.invalid/image.png', '//elsewhere.invalid/image.png', 'data:image/png;base64,AAAA', '/api/files/other/content', '/api/files/image_one/content?token=secret', '/api/files/../content']) assert.equal(isImageFile(file({ content_url })), false);
  assert.equal(ownedImageSource('/api/files/image_one/content'), true);
  assert.equal(ownedImageSource(null), false);
});

test('a large generated image is immediately in the conversation and never auto-opens another view', async () => {
  const fixture = await mount(), observations = []; let opens = 0;
  try {
    await fixture.render(h(ImageResult, { file: file(), onOpen: () => { opens++; }, onObservation: value => observations.push(value) }));
    const image = fixture.host.querySelector('img');
    assert.equal(image.getAttribute('loading'), 'lazy'); assert.equal(image.getAttribute('decoding'), 'async');
    assert.equal(image.getAttribute('src'), '/api/files/image_one/content?revision=19000000');
    assert.equal(fixture.host.querySelector('[data-file-result-id]').dataset.fileResultId, 'image_one');
    assert.equal(document.querySelectorAll('dialog[open]').length, 0); assert.equal(opens, 0);
    assert.deepEqual(observations, [{ file_id: 'image_one', status: 'loading' }]);
    await fire(image, 'load');
    assert.equal(fixture.host.querySelector('[data-image-state]').dataset.imageState, 'ready');
    assert.equal(opens, 0);
    assert.deepEqual(observations.at(-1), { file_id: 'image_one', status: 'ready' });
    const download = fixture.host.querySelector('a[download]');
    assert.equal(download.getAttribute('href'), '/api/files/image_one/content?download=1');
    assert.equal(download.getAttribute('download'), 'Result.png');
    await click(fixture.host.querySelector('button.inline-image-open'));
    assert.equal(opens, 1);
  } finally { await fixture.close(); }
});

test('failed image loading keeps download available, reports failure and retries only when clicked', async () => {
  const fixture = await mount(), observations = [];
  try {
    await fixture.render(h(ImageResult, { file: file(), onObservation: value => observations.push(value) }));
    const image = fixture.host.querySelector('img'); await fire(image, 'error');
    assert.deepEqual(observations.at(-1), { file_id: 'image_one', status: 'failed', error_code: 'preview_unavailable' });
    assert.match(fixture.host.textContent, /files.unavailable/);
    assert.equal(fixture.host.querySelectorAll('a[download]').length, 1);
    assert.equal(fixture.host.querySelector('.inline-image-open').disabled, true);
    assert.equal(image.getAttribute('src').includes('retry='), false);
    await click([...fixture.host.querySelectorAll('button')].find(item => item.textContent === 'retry'));
    const retried = fixture.host.querySelector('img');
    assert.notEqual(retried, image);
    assert.equal(retried.getAttribute('src'), '/api/files/image_one/content?revision=19000000&retry=1');
    assert.deepEqual(observations.at(-1), { file_id: 'image_one', status: 'loading' });
    await fire(retried, 'load');
    assert.deepEqual(observations.at(-1), { file_id: 'image_one', status: 'ready' });
    assert.equal(document.querySelectorAll('dialog[open]').length, 0);
  } finally { await fixture.close(); }
});

test('clicking an inline image explicitly enlarges it and closing restores the original conversation focus', async () => {
  const fixture = await mount();
  try {
    await fixture.render(h(InlineImage, { src: '/api/files/owned/content', name: 'A finished picture' }));
    const trigger = fixture.host.querySelector('.inline-image-open');
    assert.equal(document.querySelectorAll('dialog[open]').length, 0);
    await fire(fixture.host.querySelector('img'), 'load'); trigger.focus();
    await click(trigger);
    const dialog = document.querySelector('dialog[open]'); assert.ok(dialog);
    assert.equal(dialog.getAttribute('aria-label'), 'A finished picture');
    assert.equal(dialog.querySelector('img').getAttribute('src'), '/api/files/owned/content');
    await click(dialog.querySelector('button[aria-label="files.close"]'));
    assert.equal(document.querySelectorAll('dialog').length, 0);
    assert.equal(document.activeElement === trigger, true);
    assert.equal(fixture.host.querySelectorAll('img').length, 1);
  } finally { await fixture.close(); }
});

test('Escape closes explicit enlargement and a failed full image still exposes its download', async () => {
  const fixture = await mount();
  try {
    await fixture.render(h(InlineImage, { src: '/api/files/owned/content', name: 'Picture' }));
    await fire(fixture.host.querySelector('img'), 'load');
    await click(fixture.host.querySelector('.inline-image-open'));
    const dialog = document.querySelector('dialog');
    await fire(dialog.querySelector('img'), 'error');
    assert.match(dialog.textContent, /files.unavailable/);
    assert.equal(dialog.querySelector('a').getAttribute('href'), '/api/files/owned/content?download=1');
    await fire(dialog, 'cancel');
    assert.equal(document.querySelectorAll('dialog').length, 0);
  } finally { await fixture.close(); }
});

test('unrelated parent renders retain the loaded element while changed files start a new load', async () => {
  const fixture = await mount(), observations = [], observe = value => observations.push(value);
  try {
    await fixture.render(h(ImageResult, { file: file(), onObservation: observe }));
    const initial = fixture.host.querySelector('img'); await fire(initial, 'load');
    await fixture.render(h(ImageResult, { file: file(), onObservation: observe }));
    assert.equal(fixture.host.querySelector('img') === initial, true);
    assert.equal(observations.length, 2);
    await fixture.render(h(ImageResult, { file: file({ size: 18000000 }), onObservation: observe }));
    const replacement = fixture.host.querySelector('img');
    assert.notEqual(replacement, initial);
    assert.equal(replacement.getAttribute('src'), '/api/files/image_one/content?revision=18000000');
    assert.equal(fixture.host.querySelector('[data-image-state]').dataset.imageState, 'loading');
    await fire(replacement, 'load');
    await fixture.render(h(ImageResult, { file: file({ id: 'image_two', content_url: '/api/files/image_two/content', name: 'Another.png' }), onObservation: observe }));
    assert.deepEqual(observations.at(-1), { file_id: 'image_two', status: 'loading' });
    assert.equal(fixture.host.querySelector('[data-image-state]').dataset.imageState, 'loading');
  } finally { await fixture.close(); }
});

test('invalid image sources cannot cause cross-origin requests or downloadable links', async () => {
  const fixture = await mount(), observations = [];
  try {
    await fixture.render(h(ImageResult, { file: file({ content_url: 'https://elsewhere.invalid/image.png' }), onObservation: value => observations.push(value) }));
    assert.equal(fixture.host.querySelectorAll('img').length, 0);
    assert.equal(fixture.host.querySelectorAll('a').length, 0);
    assert.deepEqual(observations.at(-1), { file_id: 'image_one', status: 'failed', error_code: 'preview_unavailable' });
  } finally { await fixture.close(); }
});

test('same-size image revisions reload while stable snapshot objects retain the loaded image', async () => {
  const fixture = await mount(), observations = [], observe = value => observations.push(value);
  try {
    await fixture.render(h(ImageResult, { file: file({ content_revision: 's1-first' }), onObservation: observe }));
    const initial = fixture.host.querySelector('img'); await fire(initial, 'load');
    assert.equal(initial.getAttribute('src'), '/api/files/image_one/content?revision=s1-first');
    for (let cursor = 1; cursor < 4; cursor++) {
      await fixture.render(h(ImageResult, { file: file({ content_revision: 's1-first' }), onObservation: observe }));
      assert.equal(fixture.host.querySelector('img'), initial);
    }
    assert.deepEqual(observations.map(item => item.status), ['loading', 'ready']);
    await fixture.render(h(ImageResult, { file: file({ content_revision: 's1-second' }), onObservation: observe }));
    const replacement = fixture.host.querySelector('img');
    assert.notEqual(replacement, initial);
    assert.equal(replacement.getAttribute('src'), '/api/files/image_one/content?revision=s1-second');
    await fire(initial, 'load');
    assert.equal(observations.at(-1).status, 'loading', 'an old revision cannot acknowledge the new image');
    await fire(replacement, 'load');
    assert.equal(observations.at(-1).status, 'ready');
  } finally { await fixture.close(); }
});
