import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { DraftStore } from '../src/draft-store.ts';

const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/ComposerActions.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const module = { exports: {} };
vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`, {})(
  name => name === './i18n' ? { useI18n: () => ({ t: key => key }) } : require(name), module, module.exports,
);
const ComposerActions = module.exports.default;
const defaults = { busy: false, sending: false, questions: 0, text: '', attachmentCount: 0, disabled: false, stop() {}, interrupt() {} };
const buttons = options => ComposerActions({ ...defaults, ...options }).props.children.filter(Boolean);
const stopButton = options => buttons(options).find(button => button.props['aria-label'] === 'stop');
const sendButton = options => buttons(options).find(button => button.props.type === 'submit');
const drafts = [
  { text: '', attachmentCount: 0 }, { text: '  \n', attachmentCount: 0 },
  { text: 'Keep this unsent thought', attachmentCount: 0 }, { text: '', attachmentCount: 1 },
  { text: 'Inspect the attached file later', attachmentCount: 2 },
];

test('every running or waiting state retains a distinct Stop with empty, text and attachment drafts', () => {
  for (const activity of [{ busy: true }, { questions: 1 }, { questions: 3 }, { busy: true, questions: 1 }]) {
    for (const draft of drafts) {
      const options = { ...activity, ...draft }, stop = stopButton(options);
      assert.ok(stop, JSON.stringify(options));
      assert.equal(stop.props.type, 'button', 'Stop must not submit the enclosing composer form');
      assert.equal(stop.props.disabled, undefined);
      assert.equal(stop.props.title, 'stop');
      const html = renderToStaticMarkup(createElement('form', null, createElement(ComposerActions, { ...defaults, ...options })));
      assert.equal((html.match(/aria-label="stop"/g) || []).length, 1);
      assert.match(html, /class="send stop"[^>]+type="button"/);
    }
  }
});

test('idle adds no Stop or interrupt control and only enables Send for a real draft', () => {
  for (const draft of drafts) {
    assert.equal(stopButton(draft), undefined);
    assert.equal(buttons(draft).length, 1);
    assert.equal(sendButton(draft).props.disabled, !Boolean(draft.text.trim() || draft.attachmentCount));
    assert.equal(sendButton(draft).props['aria-label'], 'send');
  }
});

test('sending and uploading can block submission but never disable independent Stop', () => {
  for (const activity of [{ busy: true }, { questions: 2 }]) {
    const options = { ...activity, text: 'Not ready to send', attachmentCount: 1, disabled: true };
    const rendered = buttons(options);
    assert.equal(stopButton(options).props.disabled, undefined);
    assert.equal(sendButton(options).props.disabled, true);
    assert.equal(rendered.find(button => button.props.className === 'interrupt').props.disabled, true);
  }
});

test('the first message can be stopped during preparation before any server run exists', () => {
  for (const draft of drafts) {
    const options = { ...draft, sending: true, disabled: true };
    assert.equal(stopButton(options).props.type, 'button');
    assert.equal(stopButton(options).props.disabled, undefined);
    assert.equal(sendButton(options).props.disabled, true);
    assert.equal(sendButton(options).props['aria-label'], 'send', 'Sending alone must not change queue semantics');
    assert.equal(buttons(options).some(button => button.props.className === 'interrupt'), false);
  }
});

test('clicking Stop only stops the current work; text and file draft stay untouched and nothing is submitted', () => {
  const store = new DraftStore();
  const draft = store.write('owner', 'chat', { text: 'Do not send this yet', attached: [{ id: 'file', name: 'report.md', content_url: '/api/files/file/content' }] });
  let stops = 0, interrupts = 0, submissions = 0;
  const options = { busy: true, text: draft.text, attachmentCount: draft.attached.length,
    stop() { stops++; }, interrupt() { interrupts++; store.submitted('owner', 'chat', draft); } };
  const stop = stopButton(options);
  // Browser default submission only applies to a submit button; the real button
  // remains type=button even when it sits beside Send inside the same form.
  stop.props.onClick();
  if (stop.props.type === 'submit') submissions++;
  assert.equal(stops, 1);
  assert.equal(interrupts, 0);
  assert.equal(submissions, 0);
  assert.deepEqual(store.read('owner', 'chat'), draft);
});

test('Send keeps queue/help semantics and explicit interrupt remains a separate action', () => {
  assert.equal(sendButton({ busy: true, text: 'Next task' }).props['aria-label'], 'queue');
  assert.equal(sendButton({ questions: 1, text: 'Answer' }).props['aria-label'], 'send');
  assert.equal(sendButton({ questions: 2, attachmentCount: 1 }).props['aria-label'], 'queue');
  assert.equal(sendButton({ busy: true }), undefined);
  let stopped = 0, interrupted = 0;
  const rendered = buttons({ busy: true, text: 'Prioritize this', stop() { stopped++; }, interrupt() { interrupted++; } });
  const interrupt = rendered.find(button => button.props.className === 'interrupt');
  assert.equal(interrupt.props.type, 'button');
  interrupt.props.onClick();
  assert.equal(interrupted, 1); assert.equal(stopped, 0);
});


test('a transient effect keeps Stop after model completion without turning the next message into a queue', () => {
  let stopped = 0;
  const options = { effectsActive: true, text: 'Next task', stop() { stopped++; } };
  assert.ok(stopButton(options));
  stopButton(options).props.onClick();
  assert.equal(stopped, 1);
  assert.equal(sendButton(options).props['aria-label'], 'send');
  assert.equal(buttons(options).some(button => button.props.className === 'interrupt'), false);
});
