import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { widgetDocument } from '../src/widget-document.ts';

function fixture({ text = 'Ready', hidden = false } = {}) {
  const handlers = new Map(), dom = new Map(), sent = [], timers = new Map();
  let clock = 0, serial = 0, mutation, resize, content = text;
  const counts = { inspections: 0, boxes: 0, sizes: 0 };
  const body = {
    firstElementChild: null,
    get innerText() { counts.inspections++; return content; },
    getBoundingClientRect() { counts.sizes++; return { width: 320, height: 240 }; },
    querySelectorAll() { assert.fail('Health must not collect the entire descendant list'); },
  };
  function children(parent, nodes) {
    parent.firstElementChild = nodes[0] || null;
    for (let i = 0; i < nodes.length; i++) {
      nodes[i].parentElement = parent;
      nodes[i].nextElementSibling = nodes[i + 1] || null;
    }
  }
  function shape({ width = 30, height = 30, display = 'block', visibility = 'visible', opacity = '1', descendants = [] } = {}) {
    const element = { firstElementChild: null, style: { display, visibility, opacity },
      getBoundingClientRect() { counts.boxes++; return { width: element.width, height: element.height }; }, width, height };
    children(element, descendants);
    return element;
  }
  const setTimeout = (callback, delay = 0) => { timers.set(++serial, { callback, at: clock + delay }); return serial; };
  const clearTimeout = id => timers.delete(id);
  const parent = { postMessage: value => sent.push(JSON.parse(JSON.stringify(value))) };
  const document = { body, hidden, addEventListener: (name, callback) => dom.set(name, callback), querySelectorAll: () => [] };
  const context = { parent, document, innerWidth: 320, innerHeight: 240,
    addEventListener: (name, callback) => handlers.set(name, callback),
    Date: { now: () => clock }, setTimeout, clearTimeout,
    requestAnimationFrame: callback => setTimeout(callback, 16), cancelAnimationFrame: clearTimeout,
    ErrorEvent: class {},
    MutationObserver: class { constructor(callback) { mutation = callback; } observe() {} },
    ResizeObserver: class { constructor(callback) { resize = callback; } observe() {} },
    getComputedStyle: element => element.style,
  };
  context.window = context;
  vm.runInNewContext(widgetDocument('', 'en', 'health-fixture').match(/<script>([\s\S]*?)<\/script>/)[1], context);
  const advance = milliseconds => {
    const end = clock + milliseconds;
    for (;;) {
      const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!next) break;
      const [id, timer] = next; timers.delete(id); clock = timer.at; timer.callback();
    }
    clock = end;
  };
  return { counts, sent, body, shape, children, context, advance,
    start: () => handlers.get('DOMContentLoaded')(),
    text: value => { content = value; mutation([{ type: 'characterData' }]); },
    mutate: (type = 'attributes') => mutation([{ type }]), resize: () => resize(),
    inspect: () => handlers.get('message')({ source: parent, data: { type: 'athena:inspect', instance: 'health-fixture' } }),
    visible: value => { document.hidden = !value; dom.get('visibilitychange')(); },
    error: () => handlers.get('error')(new context.ErrorEvent()),
    health: () => sent.filter(item => item.type === 'athena:health'),
  };
}

test('continuous attribute animation has bounded health work without starving its deadline', () => {
  const f = fixture(); f.start();
  for (let i = 0; i < 200; i++) { f.mutate(); f.advance(10); }
  assert.equal(f.counts.inspections, 5, 'initial inspection plus one per 500ms, never a 50ms animation scan');
  assert.equal(f.health().length, 1, 'unchanged health does not become repeated messages');
  const before = f.counts.inspections;
  f.advance(900);
  assert.equal(f.counts.inspections, before, 'an idle document has no periodic polling');
});

test('content removal advances a pending attribute check and recovering content clears only empty failure', () => {
  const f = fixture(); f.start(); f.advance(3000);
  f.mutate(); f.advance(10); f.text(''); f.advance(49);
  assert.equal(f.health().at(-1).has_content, true);
  f.advance(1);
  assert.deepEqual(f.health().at(-1), { type: 'athena:health', instance: 'health-fixture', state: 'failed', has_content: false, error_code: 'empty_document' });
  f.text('Restored'); f.advance(50);
  assert.equal(f.health().at(-1).state, 'mounted');
  assert.equal(f.health().at(-1).error_code, null);
  f.error(); f.text('Still present'); f.advance(50);
  assert.equal(f.health().at(-1).error_code, 'script_error', 'content cannot erase a script failure');
});

test('CSS-only content and dimensions remain observable without rescanning every animation frame', () => {
  const f = fixture({ text: '' }), shape = f.shape();
  f.children(f.body, [shape]); f.start(); f.advance(3000);
  assert.equal(f.health().at(-1).has_content, true);
  shape.style.opacity = '0';
  for (let i = 0; i < 50; i++) { f.mutate(); f.resize(); f.advance(10); }
  assert.equal(f.health().at(-1).error_code, 'empty_document');
  shape.style.opacity = '1'; shape.width = 0; f.resize(); f.advance(500);
  assert.equal(f.health().at(-1).has_content, false);
  shape.width = 30; f.resize(); f.advance(500);
  assert.equal(f.health().at(-1).state, 'mounted');
});

test('hidden documents cancel pending health and sizing work, then inspect once on return', () => {
  const f = fixture(); f.start(); f.mutate(); f.visible(false);
  const before = { ...f.counts };
  f.text(''); f.resize(); f.inspect(); f.advance(4000);
  assert.deepEqual(f.counts, before, 'including the initial empty deadline, hidden content forces no layout');
  f.visible(true);
  assert.equal(f.counts.inspections, before.inspections + 1);
  assert.equal(f.health().at(-1).error_code, 'empty_document');
  f.advance(16);
  assert.equal(f.counts.sizes, before.sizes + 1);
});

test('a script failure while hidden is reported immediately without a content scan', () => {
  const f = fixture(); f.start(); f.visible(false);
  const before = f.counts.inspections;
  f.error();
  assert.equal(f.health().at(-1).state, 'failed');
  assert.equal(f.health().at(-1).error_code, 'script_error');
  assert.equal(f.counts.inspections, before);
  f.visible(true);
  assert.equal(f.health().at(-1).error_code, 'script_error');
});

test('an initially hidden empty document waits for visibility before an empty verdict', () => {
  const f = fixture({ text: '', hidden: true }); f.start(); f.advance(4000);
  assert.equal(f.counts.inspections, 0); assert.equal(f.health().length, 0);
  f.visible(true);
  assert.equal(f.health().at(-1).error_code, 'empty_document');
});

test('bounded lazy traversal preserves descendant order and excludes the body itself', () => {
  const f = fixture({ text: '' });
  const leaf = f.shape(), first = f.shape({ width: 0, descendants: [leaf] });
  const untouched = f.shape(); untouched.getBoundingClientRect = () => assert.fail('walk should stop after a visible descendant');
  f.children(f.body, [first, untouched]); f.start();
  assert.equal(f.counts.boxes, 2); assert.equal(f.health().at(-1).has_content, true);
  const nodes = Array.from({ length: 5000 }, () => f.shape({ width: 0 }));
  const outside = f.shape(); outside.getBoundingClientRect = () => assert.fail('node 5001 is outside the bound');
  f.children(f.body, [...nodes, outside]);
  const before = f.counts.boxes; f.inspect();
  assert.equal(f.counts.boxes - before, 5000); assert.equal(f.health().at(-1).has_content, false);
  f.children(f.body, []); f.inspect();
  assert.equal(f.health().at(-1).has_content, false, 'body padding is not content');
});
