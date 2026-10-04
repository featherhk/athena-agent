import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import { act, createElement as h, useEffect } from 'react';
import * as scrollModule from '../src/conversation-scroll.ts';
const { ConversationScroll } = scrollModule;
const position = (scrollTop = 400) => ({ scrollTop, scrollHeight: 1000, clientHeight: 600 });

test('even a one-pixel upward gesture stops following before the next stream commit', () => {
  const policy = new ConversationScroll(), area = position();
  policy.follow(area); policy.intent(-1, area, 10);
  assert.equal(policy.following, false);
  area.scrollTop = 399; policy.observe(area, 11);
  area.scrollHeight += 50; policy.follow(area);
  assert.equal(area.scrollTop, 399);
});

test('downward intent resumes only at the true bottom, never anywhere in a 160px band', () => {
  const policy = new ConversationScroll(), area = position(340);
  policy.pause(); policy.intent(1, area, 10); policy.observe(area, 11);
  assert.equal(policy.following, false);
  area.scrollTop = 390; policy.observe(area, 12); assert.equal(policy.following, false);
  area.scrollTop = 399; policy.observe(area, 13); assert.equal(policy.following, true);
  area.scrollHeight += 80; policy.follow(area); assert.equal(area.scrollTop, 480);
});

test('programmatic activity positioning, stale gestures and upward touch bounce do not resume', () => {
  const policy = new ConversationScroll(), area = position(300);
  policy.intent(1, area, 10); policy.pause();
  area.scrollTop = 400; policy.observe(area, 11); assert.equal(policy.following, false);
  area.scrollTop = 300; policy.intent(1, area, 20);
  area.scrollTop = 400; policy.observe(area, 2000); assert.equal(policy.following, false);
  policy.intent(-1, area, 2010); area.scrollTop = 399; policy.observe(area, 2011);
  area.scrollTop = 400; policy.observe(area, 2012); assert.equal(policy.following, false);
  // A resize or positioning change after a downward gesture also cannot use
  // that stale direction to resume when it has moved the reader upward first.
  policy.intent(1, area, 2020); area.scrollTop = 350; policy.observe(area, 2021);
  area.scrollTop = 400; policy.observe(area, 2022); assert.equal(policy.following, false);
});

test('scrollbar dragging stops immediately and can resume when dragged to the bottom', () => {
  const policy = new ConversationScroll(), area = position();
  policy.startDrag(area, 1); assert.equal(policy.following, false);
  area.scrollTop = 330; policy.observe(area, 2); policy.follow(area); assert.equal(area.scrollTop, 330);
  area.scrollTop = 380; policy.observe(area, 3); assert.equal(policy.following, false);
  area.scrollTop = 400; policy.observe(area, 4); assert.equal(policy.following, true);
  policy.endDrag();
});

test('a continuous momentum scroll can reach the bottom beyond the initial gesture timeout', () => {
  const policy = new ConversationScroll(), area = position(200);
  policy.pause(); policy.intent(1, area, 0);
  area.scrollTop = 260; policy.observe(area, 1000);
  area.scrollTop = 340; policy.observe(area, 2000);
  area.scrollTop = 400; policy.observe(area, 2900);
  assert.equal(policy.following, true);
});

test('sending or ordinary navigation resumes; activity navigation can override immediately', () => {
  const policy = new ConversationScroll(), area = position(200);
  policy.pause(); policy.resume(); policy.follow(area); assert.equal(area.scrollTop, 400);
  policy.resume(); policy.pause(); policy.observe(area); policy.follow(area);
  assert.equal(policy.following, false);
});

test('viewport growth and collapsed content keep following when WebKit clamps to a shorter bottom', () => {
  const policy = new ConversationScroll(), area = position(); policy.follow(area);
  area.clientHeight = 650; area.scrollTop = 350; policy.observe(area);
  assert.equal(policy.following, true);
  area.scrollHeight = 900; area.scrollTop = 250; policy.observe(area);
  assert.equal(policy.following, true);
  area.scrollHeight += 40; policy.follow(area); assert.equal(area.scrollTop, 290);
  policy.intent(-1, area); area.scrollHeight = 800; area.scrollTop = 150; policy.observe(area);
  assert.equal(policy.following, false, 'geometry clamping cannot override an explicit upward gesture');
});

const window = new Window({ url: 'https://athena.invalid' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'MutationObserver']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/useConversationScroll.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const module = { exports: {} };
vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`)(name => name === './conversation-scroll' ? scrollModule : require(name), module, module.exports);
const { useConversationScroll } = module.exports;
after(() => window.happyDOM.abort());
async function mounted() {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host); let control, revision = 0;
  function Harness({ revision }) {
    control = useConversationScroll(() => {});
    useEffect(() => control.follow(), [revision]);
    return h('main', { ref: control.ref, ...control.handlers }, h('p', null, `Stream ${revision}`),
      h('textarea', { 'aria-label': 'draft', defaultValue: '仍在输入' }), h('div', { contentEditable: true, suppressContentEditableWarning: true }, 'editable'),
      h('pre', { style: { overflowY: 'auto' } }, 'Nested scroll'), h('button', null, 'Action'));
  }
  await act(async () => root.render(h(Harness, { revision })));
  const area = host.querySelector('main');
  Object.defineProperties(area, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 600 } });
  area.getBoundingClientRect = () => ({ left: 0, right: 600, top: 0, bottom: 600 });
  control.policy.follow(area);
  const event = async (name, init = {}, target = area) => {
    const Constructor = name.startsWith('key') ? window.KeyboardEvent : name === 'wheel' ? window.WheelEvent : name.startsWith('pointer') ? window.PointerEvent : window.Event;
    const event = new Constructor(name, { bubbles: true, cancelable: true, ...init });
    if ('touches' in init) Object.defineProperty(event, 'touches', { value: init.touches });
    await act(async () => target.dispatchEvent(event));
    return event;
  };
  return { host, area, event, policy: control.policy, stream: async () => { await act(async () => root.render(h(Harness, { revision: ++revision }))); },
    close: async () => { await act(async () => root.unmount()); host.remove(); } };
}

test('real React wheel binding preserves a reader 60px from the bottom through stream updates', async () => {
  const fixture = await mounted();
  try {
    await fixture.event('wheel', { deltaY: -60 }); assert.equal(fixture.policy.following, false);
    fixture.area.scrollTop = 340; await fixture.event('scroll'); await fixture.stream();
    assert.equal(fixture.area.scrollTop, 340);
    await fixture.event('wheel', { deltaY: 60 }); fixture.area.scrollTop = 400; await fixture.event('scroll');
    assert.equal(fixture.policy.following, true);
    Object.defineProperty(fixture.area, 'scrollHeight', { value: 1100 }); await fixture.stream(); assert.equal(fixture.area.scrollTop, 500);
  } finally { await fixture.close(); }
});

test('touch direction and post-touch momentum keep user control', async () => {
  const fixture = await mounted();
  try {
    await fixture.event('touchstart', { touches: [{ clientY: 200 }] });
    await fixture.event('touchmove', { touches: [{ clientY: 240 }] }); assert.equal(fixture.policy.following, false);
    fixture.area.scrollTop = 360; await fixture.event('scroll'); await fixture.event('touchend', { touches: [] });
    await fixture.stream(); assert.equal(fixture.area.scrollTop, 360);
    await fixture.event('touchstart', { touches: [{ clientY: 240 }] });
    await fixture.event('touchmove', { touches: [{ clientY: 220 }] });
    fixture.area.scrollTop = 380; await fixture.event('scroll'); await fixture.event('touchend', { touches: [] });
    fixture.area.scrollTop = 400; await fixture.event('scroll'); assert.equal(fixture.policy.following, true);
  } finally { await fixture.close(); }
});

test('keyboard scroll controls do not hijack text editing, IME or button activation', async () => {
  const fixture = await mounted();
  try {
    const field = fixture.host.querySelector('textarea'); field.focus();
    await fixture.event('keydown', { key: 'ArrowUp' }, field);
    await fixture.event('keydown', { key: 'PageUp' }, fixture.host.querySelector('[contenteditable]'));
    await fixture.event('keydown', { key: 'ArrowUp', isComposing: true });
    await fixture.event('keydown', { key: ' ', shiftKey: true }, fixture.host.querySelector('button'));
    assert.equal(fixture.policy.following, true); assert.equal(field.value, '仍在输入');
    const up = await fixture.event('keydown', { key: 'PageUp' });
    assert.equal(up.defaultPrevented, false, 'the browser still performs normal keyboard scrolling');
    assert.equal(fixture.policy.following, false);
    fixture.area.scrollTop = 340; await fixture.event('scroll'); await fixture.stream(); assert.equal(fixture.area.scrollTop, 340);
    await fixture.event('keydown', { key: 'End' }); fixture.area.scrollTop = 400; await fixture.event('scroll');
    assert.equal(fixture.policy.following, true);
  } finally { await fixture.close(); }
});

test('React scrollbar events pause streaming while dragging and restore only at the end', async () => {
  const fixture = await mounted();
  try {
    await fixture.event('pointerdown', { pointerType: 'mouse', button: 0, clientX: 598 });
    assert.equal(fixture.policy.following, false);
    fixture.area.scrollTop = 320; await fixture.event('scroll'); await fixture.stream(); assert.equal(fixture.area.scrollTop, 320);
    fixture.area.scrollTop = 400; await fixture.event('scroll'); await fixture.event('pointerup', {}, window);
    assert.equal(fixture.policy.following, true);
  } finally { await fixture.close(); }
});

test('a nested scrolling block consumes its gestures until its own edge', async () => {
  const fixture = await mounted();
  try {
    const block = fixture.host.querySelector('pre');
    Object.defineProperties(block, { scrollHeight: { value: 700 }, clientHeight: { value: 200 } }); block.scrollTop = 80;
    await fixture.event('wheel', { deltaY: -30 }, block); assert.equal(fixture.policy.following, true);
    block.scrollTop = 0; await fixture.event('wheel', { deltaY: -30 }, block); assert.equal(fixture.policy.following, false);
  } finally { await fixture.close(); }
});
