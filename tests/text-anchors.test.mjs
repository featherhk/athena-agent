import assert from 'node:assert/strict';
import test from 'node:test';
import { locateQuote, measureTextTargets, parseTextTargets } from '../src/text-anchors.ts';

const target = (extra = {}) => ({ id: 'deadline', message_id: 'message-one', quote: 'Friday at noon', ...extra });

test('text targets accept owned message references, retain exact quotes and reject selector escape hatches', () => {
  const input = [target({ quote: '  周五\n中午  ', occurrence: 0 }), target({ id: 'second', occurrence: 1023 })];
  assert.deepEqual(parseTextTargets(input), input);
  assert.notEqual(parseTextTargets(input)[0], input[0]);
  assert.deepEqual(parseTextTargets(null), []);
  assert.deepEqual(parseTextTargets([]), []);
  for (const invalid of [undefined, {}, 'deadline', [null], [[]], [target(), target()],
    [target({ selector: '.composer' })], [target({ message_id: '* [data-password]' })],
    [target({ id: '../other' })], [target({ id: '' })], [target({ message_id: 'x'.repeat(65) })],
    [target({ quote: ' \n\t' })], [target({ quote: 12 })],
    ...[-1, 1024, 0.5, Infinity, NaN, '1', null].map(occurrence => [target({ occurrence })])]) {
    assert.equal(parseTextTargets(invalid), undefined);
  }
});

test('target limits bound both independent subscriptions and total quoted text', () => {
  const many = (count, quote = 'x') => Array.from({ length: count }, (_, i) => target({ id: `target-${i}`, quote }));
  assert.equal(parseTextTargets(many(32)).length, 32);
  assert.equal(parseTextTargets(many(33)), undefined);
  assert.equal(parseTextTargets(many(1, 'x'.repeat(512)))[0].quote.length, 512);
  assert.equal(parseTextTargets(many(1, 'x'.repeat(513))), undefined);
  assert.equal(parseTextTargets(many(16, 'x'.repeat(512))).length, 16);
  assert.equal(parseTextTargets([...many(16, 'x'.repeat(512)), target({ id: 'over-budget', quote: 'x' })]), undefined);
});

test('quote matching returns original UTF-16 offsets for Chinese, emoji and combining characters', () => {
  for (const quote of ['周五中午', '👩🏽‍💻', 'e\u0301', 'العربية', '日本語']) {
    const text = `前言 👋 ${quote} 后文`;
    const result = locateQuote(text, quote);
    assert.deepEqual(result, { start: text.indexOf(quote), end: text.indexOf(quote) + quote.length });
    assert.equal(text.slice(result.start, result.end), quote);
  }
  assert.equal(locateQuote('Cafe\u0301', 'Café'), 'missing', 'only whitespace is normalized, not Unicode composition');
});

test('quotes normalize layout whitespace while preserving ranges in the original text', () => {
  const text = 'Heading\n  Friday\t \n at\u00a0noon\nEnd';
  const match = locateQuote(text, '  Friday at\nnoon  ');
  assert.deepEqual(match, { start: text.indexOf('Friday'), end: text.indexOf('noon') + 4 });
  assert.equal(text.slice(match.start, match.end), 'Friday\t \n at\u00a0noon');
  assert.equal(locateQuote('alpha\nbeta', 'alphabeta'), 'missing');
  assert.equal(locateQuote('Friday at noon', 'friday at noon'), 'missing');
  assert.equal(locateQuote('anything', ' \n\t'), 'missing');
});

test('repeated and overlapping quotes require an explicit zero-based occurrence', () => {
  const text = 'Friday at noon. Friday\n at noon. Friday at noon.';
  assert.equal(locateQuote(text, 'Friday at noon'), 'ambiguous');
  const starts = [...text.matchAll(/Friday/g)].map(match => match.index);
  for (let occurrence = 0; occurrence < starts.length; occurrence++) {
    const range = locateQuote(text, 'Friday at noon', occurrence);
    assert.equal(range.start, starts[occurrence]);
    assert.equal(text.slice(range.start, range.end).replace(/\s+/g, ' '), 'Friday at noon');
  }
  assert.equal(locateQuote(text, 'Friday at noon', 3), 'missing');
  assert.equal(locateQuote('aaaa', 'aa'), 'ambiguous');
  assert.deepEqual(locateQuote('aaaa', 'aa', 1), { start: 1, end: 3 });
  assert.equal(locateQuote(text, 'never mentioned'), 'missing');
});

// Deliberately small DOM double. Geometry comes from explicit text ranges rather
// than a layout engine; real wrapping, font changes and scrolling are browser QA.
const box = (x = 0, y = 0, width = 500, height = 500) => ({ x, y, left: x, top: y, right: x + width, bottom: y + height, width, height });
function element(tag = 'div', options = {}, children = []) {
  const node = { tagName: tag.toUpperCase(), nodeType: 1, dataset: options.dataset || {}, parentElement: null,
    style: { overflowX: 'visible', overflowY: 'visible', display: 'block', visibility: 'visible', ...options.style },
    attrs: options.attrs || {}, children, box: options.box || box(),
    getBoundingClientRect() { return this.box; },
    getClientRects() { return options.noRect ? [] : [this.box]; },
    matches(selector) {
      if (selector.startsWith('[')) {
        const [, name, value] = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/) || [];
        return name in this.attrs && (value === undefined || this.attrs[name] === value);
      }
      return this.tagName.toLowerCase() === selector;
    },
    closest(selector) { for (let current = this; current; current = current.parentElement) if (selector.split(',').some(part => current.matches(part))) return current; return null; },
    querySelector(selector) { return descendants(this).find(child => child.nodeType === 1 && child.matches(selector)) || null; },
  };
  for (const child of children) child.parentElement = node;
  return node;
}
const textNode = (value, rects = [box(20, 120, 120, 20)]) => ({ nodeType: 3, textContent: value, parentElement: null, rects });
function descendants(node) { return (node.children || []).flatMap(child => [child, ...descendants(child)]); }
function environment(t, messages, options = {}) {
  const app = element('main', {}, messages);
  app.querySelectorAll = selector => {
    assert.equal(selector, '[data-message-id][data-message-role]', 'only owned message roots may be discovered');
    return descendants(app).filter(node => node.dataset?.messageId && node.dataset?.messageRole);
  };
  const seenRoots = [], ranges = [];
  const document = {
    createTreeWalker(root, kind) {
      assert.equal(kind, 5); seenRoots.push(root);
      const nodes = descendants(root).filter(node => node.nodeType === 3 || node.nodeType === 1); let index = 0;
      return { nextNode: () => nodes[index++] || null };
    },
    createRange() {
      let node, start, end;
      return { setStart(value, offset) { node = value; start = offset; },
        setEnd(value, offset) { assert.equal(value, node); end = offset; },
        getClientRects() { ranges.push({ node, start, end }); return typeof node.rects === 'function' ? node.rects(start, end) : node.rects; } };
    },
  };
  for (const node of [app, ...descendants(app)]) node.ownerDocument = document;
  for (const [name, value] of [['NodeFilter', { SHOW_TEXT: 4, SHOW_ELEMENT: 1 }], ['Node', { ELEMENT_NODE: 1 }], ['getComputedStyle', node => node.style]]) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
    t.after(() => { if (previous) Object.defineProperty(globalThis, name, previous); else delete globalThis[name]; });
  }
  const frame = { clientWidth: options.width || 500, clientHeight: options.height || 500,
    getBoundingClientRect: () => options.frame || box() };
  return { app, frame, seenRoots, ranges, measure: targets => measureTextTargets(app, frame, targets) };
}
function message(children, options = {}) {
  const body = element('div', { attrs: { 'data-message-text': '' }, ...options.body }, children);
  const wrapper = element('article', { dataset: { messageId: options.id || 'message-one', messageRole: options.role || 'assistant' } }, [body, ...(options.outside || [])]);
  return { body, wrapper };
}

test('a quote spans Markdown inline leaves and emits individual clipped line fragments in frame coordinates', t => {
  const first = textNode('Friday ', [box(110, 90, 50, 30)]);
  const second = textNode('at ', [box(190, 120, 90, 20)]);
  const third = textNode('noon', [box(130, 200, 70, 20)]);
  const { wrapper } = message([element('p', {}, [first, element('strong', {}, [second]), element('a', {}, [third])])]);
  const scroller = element('section', { style: { overflowX: 'hidden', overflowY: 'auto' }, box: box(120, 100, 140, 80) }, [wrapper]);
  const env = environment(t, [scroller], { frame: box(100, 50, 200, 200), width: 400, height: 400 });
  assert.deepEqual(env.measure([target()]), [{ id: 'deadline', message_id: 'message-one', status: 'visible', rects: [
    { x: 40, y: 100, width: 80, height: 40 }, { x: 180, y: 140, width: 140, height: 40 },
  ] }]);
  assert.deepEqual(env.ranges.map(({ node, start, end }) => [node.textContent, start, end]), [['Friday ', 0, 7], ['at ', 0, 3], ['noon', 0, 4]]);
});

test('Unicode ranges preserve exact leaf offsets without including surrounding message text', t => {
  const lead = '前言 ', emoji = '👩🏽‍💻';
  const first = textNode(lead + emoji), second = textNode(' \t joins 后文');
  const { wrapper } = message([element('p', {}, [first, element('strong', {}, [second])])]);
  const env = environment(t, [wrapper]);
  assert.equal(env.measure([target({ quote: `${emoji} joins` })])[0].status, 'visible');
  assert.deepEqual(env.ranges.map(({ start, end }) => [start, end]), [[lead.length, lead.length + emoji.length], [0, second.textContent.indexOf(' 后文')]]);
});

test('text occurrence is resolved before clipping, so scrolling never retargets a repeated quote', t => {
  let scrolled = false;
  const words = textNode('deadline and deadline', start => [box(20, (start === 0) !== scrolled ? 20 : 130, 100, 20)]);
  const { wrapper } = message([element('p', {}, [words])]);
  const scroller = element('section', { style: { overflowY: 'auto' }, box: box(0, 100, 500, 100) }, [wrapper]);
  const env = environment(t, [scroller]);
  const targets = [target({ quote: 'deadline' }), target({ id: 'first', quote: 'deadline', occurrence: 0 }), target({ id: 'second', quote: 'deadline', occurrence: 1 })];
  assert.deepEqual(env.measure(targets).map(value => value.status), ['ambiguous', 'offscreen', 'visible']);
  scrolled = true;
  assert.deepEqual(env.measure(targets).map(value => value.status), ['ambiguous', 'visible', 'offscreen']);
});

test('nested code and table scrollers clip each text fragment before reporting its visible position', t => {
  const codeText = textNode('Friday at noon', [box(220, 110, 120, 20)]);
  const code = element('pre', { style: { overflowX: 'auto' }, box: box(20, 100, 100, 40) }, [codeText]);
  const cellText = textNode('Monday morning', [box(200, 210, 80, 20)]);
  const table = element('div', { style: { overflowX: 'auto' }, box: box(100, 200, 140, 40) }, [
    element('table', {}, [element('tr', {}, [element('td', {}, [cellText])])]),
  ]);
  const { wrapper } = message([code, table]);
  const env = environment(t, [wrapper]);
  const targets = [target(), target({ id: 'table', quote: 'Monday morning' })];
  assert.deepEqual(env.measure(targets), [
    { id: 'deadline', message_id: 'message-one', status: 'offscreen', rects: [] },
    { id: 'table', message_id: 'message-one', status: 'visible', rects: [{ x: 200, y: 210, width: 40, height: 20 }] },
  ]);
  codeText.rects = [box(70, 110, 120, 20)];
  cellText.rects = [box(20, 210, 80, 20)];
  assert.deepEqual(env.measure(targets), [
    { id: 'deadline', message_id: 'message-one', status: 'visible', rects: [{ x: 70, y: 110, width: 50, height: 20 }] },
    { id: 'table', message_id: 'message-one', status: 'offscreen', rects: [] },
  ], 'horizontal scrolling must update partial fragments without retargeting the quote');
});

test('only message body text is indexed; attachments, hidden text and form controls cannot become targets', t => {
  const secret = value => ({ nodeType: 3, get textContent() { assert.fail(`Private text read: ${value}`); } });
  const { body, wrapper } = message([element('p', {}, [textNode('Public answer')]),
    element('script', {}, [secret('script')]), element('style', {}, [secret('style')]),
    element('span', { attrs: { hidden: '' } }, [secret('hidden')]),
    element('span', { attrs: { 'aria-hidden': 'true' } }, [secret('aria-hidden')]),
    ...['button', 'input', 'textarea', 'select'].map(tag => element(tag, {}, [secret(tag)])),
  ], { outside: [element('div', {}, [secret('attachment')])] });
  const account = message([secret('account')], { role: 'account', id: 'account' }).wrapper;
  const composer = element('textarea', {}, [secret('draft')]);
  const env = environment(t, [wrapper, account, composer]);
  const result = env.measure([target({ quote: 'Public answer' }), target({ id: 'private', quote: 'attachment' }), target({ id: 'account', message_id: 'account', quote: 'account' })]);
  assert.deepEqual(result.map(value => value.status), ['visible', 'missing', 'missing']);
  assert.deepEqual(env.seenRoots, [body], 'one body index is shared by targets in the same measurement');
  assert.ok(!JSON.stringify(result).includes('Public answer'), 'observations report geometry, never body text');
});

test('paragraph boundaries do not invent continuous text, but whitespace-tolerant quotations span paragraphs', t => {
  const { wrapper } = message([element('p', {}, [textNode('first')]), element('p', {}, [textNode('second')])]);
  const env = environment(t, [wrapper]);
  assert.deepEqual(env.measure([target({ quote: 'firstsecond' }), target({ id: 'spaced', quote: 'first second' })]).map(value => value.status), ['missing', 'visible']);
});

test('a Markdown hard break remains a text boundary within the same paragraph', t => {
  const { wrapper } = message([element('p', {}, [textNode('first'), element('br'), textNode('second')])]);
  const env = environment(t, [wrapper]);
  assert.deepEqual(env.measure([target({ quote: 'firstsecond' }), target({ id: 'spaced', quote: 'first second' })]).map(value => value.status), ['missing', 'visible']);
});

test('hidden or removed messages clear old geometry and oversized bodies report limited instead of guessing', t => {
  const hidden = message([textNode('Friday at noon')], { id: 'hidden', body: { attrs: { 'data-message-text': '', hidden: '' } } });
  const long = message([textNode('Friday at noon'), textNode('x'.repeat(64001))], { id: 'long' });
  const env = environment(t, [hidden.wrapper, long.wrapper]);
  assert.deepEqual(env.measure([target({ message_id: 'missing' }), target({ id: 'hidden', message_id: 'hidden' }), target({ id: 'long', message_id: 'long' })]), [
    { id: 'deadline', message_id: 'missing', status: 'missing', rects: [] },
    { id: 'hidden', message_id: 'hidden', status: 'offscreen', rects: [] },
    { id: 'long', message_id: 'long', status: 'limited', rects: [] },
  ]);
  assert.equal(env.ranges.length, 0);
});

test('fragment overflow is explicit instead of silently showing a partially resolved quote', t => {
  const { wrapper } = message([textNode('Friday at noon', Array.from({ length: 129 }, (_, i) => box(1, i * 2, 20, 1)))]);
  const env = environment(t, [wrapper]);
  assert.deepEqual(env.measure([target()]), [{ id: 'deadline', message_id: 'message-one', status: 'limited', rects: [] }]);
});

test('empty element forests cannot bypass the bounded body scan', t => {
  const { wrapper } = message([textNode('Friday at noon'), ...Array.from({ length: 8193 }, () => element('span'))]);
  const env = environment(t, [wrapper]);
  assert.equal(env.measure([target()])[0].status, 'limited');
  assert.equal(env.ranges.length, 0);
});
