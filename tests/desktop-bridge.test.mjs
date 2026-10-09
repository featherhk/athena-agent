import test from 'node:test';
import assert from 'node:assert/strict';
import { Window } from 'happy-dom';
import { desktopRole, desktopPost, readDesktopState, desktopScene, interactionVisible, setDesktopPresented, companionHitRegions } from '../src/desktop-bridge.ts';

const rect = (change = {}) => ({ x: -120, y: 37.5, width: 1728, height: 1117, ...change });
const screen = (change = {}) => ({ id: 'display-1', frame: rect(), visible_frame: rect({ height: 1015.5 }), scale: 2, ...change });
const component = (change = {}) => ({ id: 'collector', version: 2, view_revision: 3, state: 'mounted', visible: true,
  rect: rect({ x: 30.5, y: 40, width: 64, height: 80 }), screen_id: 'display-1', ...change });
const state = (change = {}) => ({ platform: 'macos', supported: true, presented: true,
  screens: [screen()], components: [component()], ...change });
const nativeWindow = (search = '?desktop=companion') => ({ location: { search }, __ATHENA_DESKTOP__: { version: 1 },
  webkit: { messageHandlers: { athenaDesktop: { postMessage() {} } } } });

test('native pointer regions include overlay actions without swallowing transparent desktop or blocked controls', () => {
  const window = new Window(), doc = window.document;
  doc.body.innerHTML = '<div class="scene-overlay"><button class="canvas-action">Move</button></div><div data-desktop-hit style="visibility:hidden">Behind login</div><div data-desktop-hit hidden>Put away</div>';
  const nodes = [...doc.querySelectorAll('div,button')];
  nodes.forEach((node, index) => {
    node.getClientRects = () => [{ x: index * 20, y: 10, width: 10, height: 12 }];
    node.getBoundingClientRect = () => ({ x: index * 20, y: 10, width: 10, height: 12 });
  });
  assert.deepEqual(companionHitRegions(doc), [{ x: 20, y: 10, width: 10, height: 12 }]);
  doc.querySelector('.canvas-action').hidden = true;
  assert.deepEqual(companionHitRegions(doc), []);
  window.happyDOM.abort();
});

test('desktop presentation requires the native marker and handler, never just a query', () => {
  assert.equal(desktopRole({ location: { search: '?desktop=companion' } }), null);
  assert.equal(desktopRole({ location: { search: '?desktop=widget' }, __ATHENA_DESKTOP__: { version: 1 } }), null);
  assert.equal(desktopRole({ ...nativeWindow(), __ATHENA_DESKTOP__: { version: 2 } }), null);
  assert.equal(desktopRole(nativeWindow('?desktop=other')), null);
  assert.equal(desktopRole(nativeWindow('')), null);
  assert.equal(desktopRole(nativeWindow()), 'companion');
  assert.equal(desktopRole(nativeWindow('?desktop=widget&component=collector')), 'widget');
});

test('native post reports unavailable and throwing transports without executing a browser fallback', () => {
  const calls = [], host = nativeWindow();
  host.webkit.messageHandlers.athenaDesktop.postMessage = value => calls.push(value);
  assert.equal(desktopPost({ action: 'hide' }, { location: { search: '?desktop=companion' } }), false);
  assert.equal(desktopPost({ action: 'hide' }, host), true);
  assert.deepEqual(calls, [{ action: 'hide' }]);
  host.webkit.messageHandlers.athenaDesktop.postMessage = () => { throw new Error('unavailable'); };
  assert.equal(desktopPost({ action: 'hide' }, host), false);
});

test('valid native observations preserve fractional points and own their bounded data', () => {
  const input = state(), parsed = readDesktopState(input);
  assert.deepEqual(parsed, input);
  input.screens[0].frame.x = 400;
  input.components[0].rect.width = 500;
  assert.equal(parsed.screens[0].frame.x, -120);
  assert.equal(parsed.components[0].rect.width, 64);
  assert.deepEqual(readDesktopState(state({ screens: [], components: [] })), state({ screens: [], components: [] }));
  assert.equal(readDesktopState(state({ presented: false, components: [component({ visible: false })] })).presented, false);
});

test('malformed native objects return null without throwing', () => {
  const cases = [null, undefined, true, 1, 'native', [], {},
    state({ screens: null }), state({ components: {} }),
    ...[null, undefined, false, 1, 'screen', [], {}].flatMap(value => [state({ screens: [value] }), state({ components: [value] })]),
    state({ screens: [screen({ frame: null })] }), state({ components: [component({ rect: [] })] }),
    state({ platform: 'windows' }), state({ supported: false }), state({ presented: 1 }),
    state({ private_note: 'must not enter observations' }),
    state({ screens: [screen({ private_note: 'no' })] }),
    state({ components: [component({ html: '<p>not an observation</p>' })] }),
    state({ screens: [screen({ frame: { ...rect(), extra: 1 } })] }),
  ];
  for (const value of cases) {
    assert.doesNotThrow(() => readDesktopState(value));
    assert.equal(readDesktopState(value), null);
  }
});

test('screen and component identifiers are unique, bounded, and refer to an observed display', () => {
  for (const id of ['', 'a'.repeat(65), '../file', 'one two', '\n', 1, true]) {
    assert.equal(readDesktopState(state({ screens: [screen({ id })] })), null);
    assert.equal(readDesktopState(state({ components: [component({ id })] })), null);
  }
  assert.equal(readDesktopState(state({ screens: [screen(), screen()] })), null);
  assert.equal(readDesktopState(state({ components: [component(), component()] })), null);
  assert.equal(readDesktopState(state({ components: [component({ screen_id: 'missing' })] })), null);
  assert.equal(readDesktopState(state({ screens: [], components: [component()] })), null);
  const screens = Array.from({ length: 16 }, (_, index) => screen({ id: `display-${index}` }));
  const components = Array.from({ length: 32 }, (_, index) => component({ id: `item-${index}` }));
  assert.equal(readDesktopState(state({ screens, components })).components.length, 32);
  assert.equal(readDesktopState(state({ screens: [...screens, screen({ id: 'extra' })], components })), null);
  assert.equal(readDesktopState(state({ screens, components: [...components, component({ id: 'extra' })] })), null);
});

test('native point, scale and revision bounds match the authenticated observation contract', () => {
  const edge = rect({ x: -131072, y: 131072, width: 32768, height: 0 });
  assert.notEqual(readDesktopState(state({ screens: [screen({ frame: edge, scale: .5 })] })), null);
  assert.notEqual(readDesktopState(state({ screens: [screen({ scale: 8 })] })), null);
  for (const scale of [.25, 0, 8.1, true, '2', NaN, Infinity]) {
    assert.equal(readDesktopState(state({ screens: [screen({ scale })] })), null);
  }
  for (const [key, value] of [['x', -131072.1], ['y', 131072.1], ['width', -1], ['height', 32768.1],
    ['width', true], ['x', '1'], ['height', NaN], ['x', Infinity], ['y', -Infinity]]) {
    assert.equal(readDesktopState(state({ components: [component({ rect: rect({ [key]: value }) })] })), null);
  }
  for (const version of [0, -1, .5, true, '1', Number.MAX_SAFE_INTEGER + 1, NaN, Infinity]) {
    assert.equal(readDesktopState(state({ components: [component({ version })] })), null);
  }
  for (const view_revision of [-1, .5, true, '0', Number.MAX_SAFE_INTEGER + 1, NaN, Infinity]) {
    assert.equal(readDesktopState(state({ components: [component({ view_revision })] })), null);
  }
  assert.notEqual(readDesktopState(state({ components: [component({ view_revision: 0 })] })), null);
});

test('native health and visibility cannot contradict the reported scene', () => {
  const errors = ['native_unavailable', 'panel_failed', 'navigation_failed', 'script_error',
    'unhandled_rejection', 'blocked_resource', 'empty_document', 'load_timeout'];
  for (const error_code of errors) {
    assert.equal(readDesktopState(state({ components: [component({ state: 'failed', visible: false, error_code })] })).components[0].error_code, error_code);
    assert.equal(readDesktopState(state({ components: [component({ error_code })] })), null);
  }
  for (const error_code of [undefined, null, '', 'private exception text', false]) {
    assert.equal(readDesktopState(state({ components: [component({ state: 'failed', error_code })] })), null);
  }
  assert.equal(readDesktopState(state({ components: [component({ state: 'hidden' })] })), null);
  assert.equal(readDesktopState(state({ components: [component({ state: 'missing' })] })), null);
  assert.equal(readDesktopState(state({ presented: false })), null);
  assert.equal(readDesktopState(state({ components: [component({ visible: 1 })] })), null);
  const healthy = readDesktopState(state({ components: [component({ error_code: null })] }));
  assert.equal('error_code' in healthy.components[0], false);
  assert.equal(readDesktopState(state({ components: [component({ state: 'hidden', visible: false })] })).components[0].state, 'hidden');
});

test('native scenes project registered layout only, honoring effective placement and disposal', () => {
  const components = [{ id: 'collector', version: 2, view_revision: 4, title: 'Collector', placement: 'desktop',
    source: 'private component code', provider_key: 'must not pass',
    view: { x: .5, y: .2, width: 64, height: 80, screen_id: 'display-1', hidden: true, arbitrary: 'must not pass' } },
  { id: 'moved-in', version: 1, title: 'Moved in', placement: 'inline', view: { placement: 'desktop' } },
  { id: 'moved-out', version: 1, title: 'Moved out', placement: 'desktop', view: { placement: 'inline' } }];
  assert.deepEqual(desktopScene('alice', 'trip', components), { action: 'scene', scope: 'alice:trip', session_id: 'trip', items: [
    { id: 'collector', version: 2, view_revision: 4, title: 'Collector', x: .5, y: .2, width: 64, height: 80, screen_id: 'display-1', hidden: true },
    { id: 'moved-in', version: 1, view_revision: 0, title: 'Moved in' },
  ] });
  assert.deepEqual(desktopScene('alice', 'trip', components, true), { action: 'scene', scope: 'alice:trip', session_id: 'trip', items: [] });
  for (const [owner, session] of [[null, 'trip'], ['alice', null], [null, null]]) {
    assert.deepEqual(desktopScene(owner, session, components), { action: 'scene', scope: '', session_id: '', items: [] });
  }
  assert.equal(desktopScene('alice', 'trip', Array.from({ length: 40 }, (_, index) => ({ ...components[0], id: `item-${index}` }))).items.length, 32);
});

test('native hide changes interaction visibility even if WebKit still reports the document visible', () => {
  const window = new Window(), document = window.document;
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  let changes = 0;
  document.addEventListener('visibilitychange', () => changes++);
  assert.equal(interactionVisible(document), true);
  setDesktopPresented(false, document);
  assert.equal(document.hidden, false);
  assert.equal(interactionVisible(document), false);
  assert.equal(changes, 1);
  setDesktopPresented(false, document);
  assert.equal(changes, 1);
  setDesktopPresented(true, document);
  assert.equal(interactionVisible(document), true);
  assert.equal(changes, 2);
  Object.defineProperty(document, 'hidden', { value: true });
  assert.equal(interactionVisible(document), false);
  window.happyDOM.abort();
});


test('transient scene forwards its absolute deadline without extending on reconciliation', () => {
  const deadline = '2026-10-10T00:00:00+00:00';
  const effect = { id: 'fireworks', version: 3, title: 'Fireworks', placement: 'desktop', expires_at: deadline };
  assert.equal(desktopScene('alice', 'trip', [effect]).items[0].expires_at, deadline);
  assert.deepEqual(desktopScene('alice', 'trip', [effect]), desktopScene('alice', 'trip', [effect]));
  assert.equal('expires_at' in desktopScene('alice', 'trip', [{ ...effect, expires_at: null }]).items[0], false);
});
