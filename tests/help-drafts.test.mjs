import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { HelpDraftStore } from '../src/help-drafts.ts';

const one = { owner: 'alice', session: 'travel', request: 'trip-questions' };
const receipt = { id: one.request, status: 'answered' };
function storage() {
  const items = new Map();
  return { getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, value), removeItem: key => items.delete(key) };
}

test('partly answered help survives switching conversations and reloading only in its own scope', () => {
  const disk = storage(), first = new HelpDraftStore(disk);
  first.write(one, { date: 'Next Friday', city: '' });
  const another = { ...one, session: 'work' };
  first.write(another, { date: 'Monday' });
  const reloaded = new HelpDraftStore(disk);
  assert.deepEqual(reloaded.read(one).answers, { date: 'Next Friday', city: '' });
  assert.deepEqual(reloaded.read(another).answers, { date: 'Monday' });
  assert.deepEqual(reloaded.read({ ...one, owner: 'bob' }).answers, {});
  assert.deepEqual(reloaded.read({ ...one, request: 'later-questions' }).answers, {});
  assert.deepEqual(new HelpDraftStore(storage()).read(one).answers, {});
});

test('failed or malformed answer receipts preserve words without resubmitting', async () => {
  const store = new HelpDraftStore(storage()), sent = store.write(one, { date: 'Friday', city: 'Kyoto' });
  let attempts = 0;
  try { attempts++; await Promise.reject(new Error('response lost')); } catch { /* No confirmation. */ }
  for (const value of [null, {}, { id: 'another-request', status: 'answered' }, { id: one.request, status: 'pending' }]) {
    assert.equal(store.acknowledge(one, sent, value), false);
    assert.deepEqual(store.read(one).answers, sent.answers);
  }
  assert.equal(attempts, 1);
});

test('confirmed answers clear only the original request and remain cleared after reload', () => {
  const disk = storage(), store = new HelpDraftStore(disk);
  const sent = store.write(one, { date: 'Friday', city: 'Kyoto' });
  const another = { ...one, request: 'new-request' };
  store.write(another, { hotel: 'Near the station' });
  assert.equal(store.acknowledge(one, sent, receipt), true);
  // A later snapshot failure does not revoke the receipt or restore sent input.
  const reloaded = new HelpDraftStore(disk);
  assert.deepEqual(reloaded.read(one).answers, {});
  assert.deepEqual(reloaded.read(another).answers, { hotel: 'Near the station' });
});

test('a late or duplicated answer receipt cannot erase a newer edit after navigating back', () => {
  const store = new HelpDraftStore(storage());
  const sent = store.write(one, { date: 'Friday', city: 'Kyoto' });
  store.write(one, { date: 'Saturday', city: 'Osaka' });
  assert.equal(store.acknowledge(one, sent, receipt), true);
  assert.deepEqual(store.read(one).answers, { date: 'Saturday', city: 'Osaka' });
  const latest = store.read(one);
  store.acknowledge(one, latest, receipt);
  store.write(one, { date: 'Sunday' });
  store.acknowledge(one, latest, receipt);
  assert.deepEqual(store.read(one).answers, { date: 'Sunday' });
});

test('restricted storage keeps navigation drafts, and callers cannot mutate retained answers', () => {
  const store = new HelpDraftStore({ getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); } });
  const original = { date: 'Friday' }, saved = store.write(one, original);
  original.date = 'Changed outside'; saved.answers.date = 'Changed returned copy';
  const read = store.read(one); read.answers.date = 'Changed reader';
  assert.equal(store.read(one).answers.date, 'Friday');
  store.acknowledge(one, store.read(one), receipt);
  assert.deepEqual(store.read(one).answers, {});
});

test('invalid browser storage does not break the form or import non-answer objects', () => {
  for (const value of ['{', 'null', '[]', '{"answers":[],"revision":1}', '{"answers":{"a":"x"},"revision":-1}']) {
    const store = new HelpDraftStore({ getItem: () => value, setItem() {} });
    assert.deepEqual(store.read(one).answers, {});
  }
  const store = new HelpDraftStore({ getItem: () => JSON.stringify({ answers: { date: 'x'.repeat(13000), city: {}, 'not an id': 'skip' }, revision: 2 }), setItem() {} });
  assert.equal(store.read(one).answers.date.length, 12000);
  assert.deepEqual(Object.keys(store.read(one).answers), ['date']);
});

// Exercise the real form's value and event bindings. Focus and mobile layout are
// checked separately in the browser; this does not simulate React committing.
const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/UserHelp.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const component = { exports: {} };
vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`, {})(
  name => name === './i18n' ? { useI18n: () => ({ t: key => key }) } : name.endsWith('.css') ? {} : require(name), component, component.exports,
);
const UserHelp = component.exports.default;
function nodes(node, type) {
  if (Array.isArray(node)) return node.flatMap(child => nodes(child, type));
  if (!node || typeof node !== 'object') return [];
  return [...(node.type === type ? [node] : []), ...nodes(node.props?.children, type)];
}

test('the actual multi-question form restores typed text and choice selection after remount', () => {
  const disk = storage(); let store = new HelpDraftStore(disk), submitted;
  const request = { id: one.request, questions: [{ id: 'date', question: 'When?' }, { id: 'city', question: 'Where?', options: [{ label: 'Kyoto' }] }] };
  const render = (scope = one, busy = false) => UserHelp({ request, busy, answers: store.read(scope).answers,
    change: answers => store.write(scope, answers), select() { throw Error('Must not replace the normal composer'); },
    answer: async answers => { submitted = { ...answers }; } });
  const first = render();
  nodes(first, 'textarea')[0].props.onChange({ target: { value: 'Next Friday' } });
  nodes(render(), 'button').find(button => button.props.type === 'button').props.onClick();
  assert.equal(nodes(render({ ...one, session: 'elsewhere' }), 'textarea')[0].props.value, '');
  store = new HelpDraftStore(disk);
  const restored = render();
  assert.deepEqual(nodes(restored, 'textarea').map(input => input.props.value), ['Next Friday', 'Kyoto']);
  assert.equal(nodes(restored, 'button').find(button => button.props.type === 'button').props['aria-pressed'], true);
  nodes(restored, 'form')[0].props.onSubmit({ preventDefault() {} });
  assert.deepEqual(submitted, { date: 'Next Friday', city: 'Kyoto' });
  submitted = undefined;
  nodes(render(one, true), 'form')[0].props.onSubmit({ preventDefault() {} });
  assert.equal(submitted, undefined);
});

test('a saved single spoken answer is reviewable without replacing the normal message composer', () => {
  const store = new HelpDraftStore(storage()); store.write(one, { city: 'Kyoto' });
  const render = () => UserHelp({ request: { id: one.request, questions: [{ id: 'city', question: 'Where?', options: [{ label: 'Osaka' }] }] },
    answers: store.read(one).answers, busy: false, change: answers => store.write(one, answers),
    answer: async () => {}, select() { throw Error('Must not overwrite message'); } });
  assert.equal(nodes(render(), 'textarea')[0].props.value, 'Kyoto');
  assert.equal(nodes(render(), 'button').at(-1).props.disabled, false);
  nodes(render(), 'button')[0].props.onClick();
  assert.equal(store.read(one).answers.city, 'Osaka');
  nodes(render(), 'textarea')[0].props.onChange({ target: { value: '' } });
  assert.equal(nodes(render(), 'textarea')[0].props.value, '', 'Clearing a saved answer must not make the review form disappear');
  assert.equal(nodes(render(), 'button').at(-1).props.disabled, true);
});
