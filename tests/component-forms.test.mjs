import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { parseFormDraft } from '../src/component-forms.ts';
import { widgetDocument } from '../src/widget-document.ts';

function bridge(fields = []) {
  const handlers = new Map(), dom = new Map(), sent = [], frames = [];
  let height = 150, observer;
  const parent = { postMessage: value => sent.push(value) };
  const context = {
    parent, innerWidth: 320, innerHeight: 240,
    addEventListener: (name, callback) => handlers.set(name, callback),
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    setTimeout: () => {}, ErrorEvent: class {},
    MutationObserver: class { observe() {} },
    ResizeObserver: class { constructor(callback) { observer = callback; } observe() {} },
    document: { querySelectorAll: () => fields, addEventListener: (name, callback) => dom.set(name, callback),
      body: { innerText: 'A working surface', querySelectorAll: () => [], getBoundingClientRect: () => ({ height }) } },
  };
  context.window = context;
  vm.runInNewContext(widgetDocument('<p>Notes</p>', 'en', 'one').match(/<script>([\s\S]*?)<\/script>/)[1], context);
  handlers.get('DOMContentLoaded')();
  const flush = () => { for (const callback of frames.splice(0)) callback(); };
  return { context, handlers, dom, sent, parent, flush, resize: value => { height = value; observer(); flush(); } };
}

test('putting a surface away retains ordinary native form values without copying credentials', () => {
  const fields = [{ type: 'textarea', value: 'Unsent draft', checked: false }, { type: 'checkbox', value: 'hotel', checked: true },
    { type: 'password', value: 'never retain this', checked: false }, { type: 'text', autocomplete: 'cc-number', value: 'private' },
    { type: 'file', value: 'local file' }, { type: 'hidden', value: 'private token' }];
  const first = bridge(fields);
  first.dom.get('input')();
  const draft = parseFormDraft(JSON.parse(JSON.stringify(first.sent.findLast(item => item.type === 'athena:form-state').draft)));
  assert.deepEqual(draft, [{ index: 0, value: 'Unsent draft', checked: false }, { index: 1, value: 'hotel', checked: true }]);
  fields[0].value = ''; fields[1].checked = false;
  const reopened = bridge(fields);
  reopened.handlers.get('message')({ source: {}, data: { type: 'athena:restore-form', instance: 'one', draft } });
  assert.equal(fields[0].value, '');
  reopened.handlers.get('message')({ source: reopened.parent, data: { type: 'athena:restore-form', instance: 'one', draft } });
  assert.equal(fields[0].value, 'Unsent draft'); assert.equal(fields[1].checked, true);
  assert.equal(reopened.sent.some(item => item.type === 'athena:submit'), false);
});

test('form bridge rejects unbounded or malformed input and does not retain extra fields', () => {
  for (const value of [null, {}, Array(129).fill({}), [{ index: -1, value: '', checked: false }], [{ index: 0, value: 'a'.repeat(32001), checked: false }], [{ index: 0, value: '', checked: false, selected: [-1] }]]) assert.equal(parseFormDraft(value), undefined);
  assert.deepEqual(parseFormDraft([{ index: 0, value: 'hello', checked: false, password: 'discard' }]), [{ index: 0, value: 'hello', checked: false }]);
});

test('intrinsic sizing fits content and a viewport-height document cannot grow its parent forever', () => {
  const widget = bridge(); widget.flush();
  assert.equal(widget.sent.findLast(item => item.type === 'athena:resize').height, 150);
  widget.resize(300); // New document content may legitimately grow.
  assert.equal(widget.sent.findLast(item => item.type === 'athena:resize').height, 300);
  widget.context.innerHeight = 300;
  widget.resize(324); // A 100vh + padding document responds to the new iframe height.
  assert.equal(widget.sent.findLast(item => item.type === 'athena:resize').height, 300);
  widget.context.Athena.resize(500);
  widget.resize(400);
  assert.equal(widget.sent.findLast(item => item.type === 'athena:resize').height, 500, 'Explicit sizing owns the height thereafter');
});
