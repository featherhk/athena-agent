import test, { after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import React, { act, createElement as h } from 'react';

// In-memory DOM and fake HTTP only; no account, checkout, wallet, or network access.
const window = new Window({ url: 'https://athena.invalid', settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true } });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'localStorage']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/Recharge.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const module = { exports: {} };
vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: 'Recharge.tsx' })(name => {
  if (name.endsWith('.css')) return {};
  if (name === './i18n') return { useI18n: () => ({ locale: 'en', t: (key, args) => `${key}${args ? ':' + JSON.stringify(args) : ''}` }), errorMessage: code => code };
  return require(name);
}, module, module.exports);
const { Recharge } = module.exports;
const order = (patch = {}) => ({ id: 'order_one', amount_micros: 10_000_000, currency: 'USD', status: 'pending', checkout_url: 'https://checkout.invalid/invoice/one', created_at: '2026-10-04T12:00:00Z', updated_at: '2026-10-04T12:00:00Z', ...patch });
const catalog = (orders = [], enabled = true) => ({ enabled, amounts_micros: [10_000_000, 20_000_000], currency: 'USD', orders });
const response = value => ({ ok: true, json: async () => structuredClone(value) });
let intervals = new Map(), intervalId = 0;
window.setInterval = (callback, delay) => { intervals.set(++intervalId, { callback, delay }); return intervalId; };
window.clearInterval = id => intervals.delete(id);
const tick = async () => { await act(async () => { for (const { callback } of [...intervals.values()]) callback(); await new Promise(resolve => setTimeout(resolve, 0)); }); };
const button = (host, key) => [...host.querySelectorAll('button')].find(item => item.textContent === key);
async function click(element) { assert.ok(element, 'expected control is present'); await act(async () => { element.click(); await new Promise(resolve => setTimeout(resolve, 0)); }); }
async function mount(userId = 'user_a', props = {}) {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => { root.render(h(React.StrictMode, null, h(Recharge, { userId, onCredited() {}, ...props }))); await new Promise(resolve => setTimeout(resolve, 0)); });
  return { host, close: async () => { await act(async () => root.unmount()); host.remove(); } };
}
beforeEach(() => { localStorage.clear(); intervals.clear(); Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); });
after(() => window.happyDOM.abort());

test('disabled recharge has no payment CTA or address, but old orders remain readable', async () => {
  globalThis.fetch = async () => response(catalog([order()], false));
  const fixture = await mount();
  try {
    assert.match(fixture.host.textContent, /account.noRecharge/);
    assert.equal(button(fixture.host, 'recharge.title'), undefined);
    await click(button(fixture.host, 'recharge.history'));
    assert.match(fixture.host.textContent, /recharge.pending/);
    assert.equal(fixture.host.querySelector('a'), null);
    assert.equal(button(fixture.host, 'recharge.continue'), undefined);
    assert.equal(intervals.size, 0);
  } finally { await fixture.close(); }
});

test('lost create response survives reload and retries the exact same amount and idempotency key', async () => {
  const requests = []; let lost = true;
  globalThis.fetch = async (_url, init = {}) => {
    if (init.method !== 'POST') return response(catalog());
    requests.push(JSON.parse(init.body));
    if (lost) throw new Error('connection lost after provider accepted');
    return response(order());
  };
  let fixture = await mount();
  try {
    await click(button(fixture.host, 'recharge.title'));
    await click(button(fixture.host, 'recharge.continue'));
    assert.match(fixture.host.textContent, /recharge.createError/);
    assert.equal(requests.length, 1);
    assert.deepEqual(JSON.parse(localStorage.getItem('athena-recharge-intent:user_a')), requests[0]);
    assert.equal(fixture.host.querySelector('[role="group"]'), null, 'unresolved amount cannot be changed');
    await fixture.close(); fixture = await mount(); lost = false;
    await click(button(fixture.host, 'recharge.title'));
    await click(button(fixture.host, 'recharge.retry'));
    assert.deepEqual(requests[1], requests[0]);
    assert.equal(localStorage.getItem('athena-recharge-intent:user_a'), null);
    assert.equal(fixture.host.querySelector('a').href, 'https://checkout.invalid/invoice/one');
    assert.equal(fixture.host.querySelector('a').rel, 'noopener noreferrer');
    assert.equal(button(fixture.host, 'recharge.continue'), undefined, 'pending invoice must not produce another purchase');
  } finally { await fixture.close(); }
});

test('account identities never reuse another user’s pending payment request', async () => {
  localStorage.setItem('athena-recharge-intent:user_a', JSON.stringify({ amount_micros: 20_000_000, idempotency_key: 'existing_request_12345' }));
  const sent = [];
  globalThis.fetch = async (_url, init = {}) => {
    if (init.method !== 'POST') return response(catalog());
    sent.push(JSON.parse(init.body)); return response(order());
  };
  const fixture = await mount('user_b');
  try {
    await click(button(fixture.host, 'recharge.title'));
    assert.equal(button(fixture.host, 'recharge.retry'), undefined);
    await click(button(fixture.host, 'recharge.continue'));
    assert.equal(sent[0].amount_micros, 10_000_000);
    assert.notEqual(sent[0].idempotency_key, 'existing_request_12345');
    assert.ok(localStorage.getItem('athena-recharge-intent:user_a'));
  } finally { await fixture.close(); }
});

test('only a credited server receipt refreshes balance; payment links and client return events do not', async () => {
  let current = order(), credited = 0;
  globalThis.fetch = async url => response(url.endsWith('/order_one') ? current : catalog([current]));
  const fixture = await mount('user_a', { onCredited: () => credited++ });
  try {
    await click(button(fixture.host, 'recharge.title'));
    assert.equal(credited, 0);
    window.dispatchEvent(new window.Event('focus'));
    window.dispatchEvent(new window.MessageEvent('message', { data: { status: 'paid' }, origin: 'https://checkout.invalid' }));
    assert.equal(credited, 0);
    current = order({ status: 'confirming' }); await tick();
    assert.equal(credited, 0); assert.match(fixture.host.textContent, /recharge.confirmingHelp/);
    current = order({ status: 'credited' }); await tick();
    assert.equal(credited, 1); assert.match(fixture.host.textContent, /recharge.credited/);
    assert.equal(intervals.size, 0);
    await click(button(fixture.host, 'recharge.refresh'));
    assert.equal(credited, 1, 'repeated credited receipts do not repeatedly refresh the balance');
  } finally { await fixture.close(); }
});

test('polling runs only for a visible open panel, is bounded, and ends on unmount', async () => {
  let reads = 0;
  globalThis.fetch = async url => { reads++; return response(url.endsWith('/order_one') ? order() : catalog([order()])); };
  const fixture = await mount();
  try {
    const loaded = reads;
    assert.equal(intervals.size, 0);
    await click(button(fixture.host, 'recharge.title'));
    assert.equal(intervals.size, 1); assert.equal([...intervals.values()][0].delay, 15_000);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await tick(); assert.equal(reads, loaded);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await tick(); assert.equal(reads, loaded + 2);
    await click(button(fixture.host, 'recharge.title')); assert.equal(intervals.size, 0);
    await click(button(fixture.host, 'recharge.title'));
    for (let i = 0; i < 41; i++) await tick();
    assert.equal(intervals.size, 0); assert.match(fixture.host.textContent, /recharge.pollingPaused/);
  } finally { await fixture.close(); }
  assert.equal(intervals.size, 0);
});

test('unsafe checkout URLs cannot become navigable links', async () => {
  for (const url of ['javascript:alert(1)', 'http://checkout.invalid', 'https://name:secret@checkout.invalid', 'file:///tmp/payment']) {
    globalThis.fetch = async () => response(catalog([order({ checkout_url: url })]));
    const fixture = await mount();
    try { await click(button(fixture.host, 'recharge.title')); assert.equal(fixture.host.querySelector('a'), null, url); }
    finally { await fixture.close(); }
  }
});

test('an unmounted create keeps its recovery key and cannot publish a late balance callback', async () => {
  let complete, credited = 0;
  globalThis.fetch = async (_url, init = {}) => {
    if (init.method !== 'POST') return response(catalog());
    return new Promise(resolve => { complete = () => resolve(response(order({ status: 'credited' }))); });
  };
  const fixture = await mount('user_a', { onCredited: () => credited++ });
  await click(button(fixture.host, 'recharge.title'));
  await click(button(fixture.host, 'recharge.continue'));
  await fixture.close();
  await act(async () => { complete(); await new Promise(resolve => setTimeout(resolve, 0)); });
  assert.equal(credited, 0);
  assert.ok(localStorage.getItem('athena-recharge-intent:user_a'));
});

test('unavailable persistence fails closed before sending a payment request', async () => {
  let posts = 0;
  globalThis.fetch = async (_url, init = {}) => { if (init.method === 'POST') posts++; return response(catalog()); };
  const fixture = await mount();
  const storage = localStorage;
  try {
    await click(button(fixture.host, 'recharge.title'));
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: key => storage.getItem(key), setItem() { throw new Error('storage disabled'); } } });
    await click(button(fixture.host, 'recharge.continue'));
    assert.equal(posts, 0); assert.match(fixture.host.textContent, /recharge.storageError/);
  } finally { Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage }); await fixture.close(); }
});

test('only the explicit never-created amount rejection clears a recovery intent', async () => {
  for (const code of ['recharge_unavailable', 'recharge_conflict', 'recharge_review_required', 'recharge_rate_limited', 'invalid_input']) {
    localStorage.clear();
    globalThis.fetch = async (_url, init = {}) => init.method === 'POST'
      ? { ok: false, json: async () => ({ code }) } : response(catalog());
    const fixture = await mount();
    try {
      await click(button(fixture.host, 'recharge.title'));
      await click(button(fixture.host, 'recharge.continue'));
      assert.ok(localStorage.getItem('athena-recharge-intent:user_a'), code);
      assert.ok(button(fixture.host, 'recharge.retry'), code);
    } finally { await fixture.close(); }
  }
  localStorage.clear(); let changed = false;
  globalThis.fetch = async (_url, init = {}) => {
    if (init.method === 'POST') { changed = true; return { ok: false, json: async () => ({ code: 'recharge_amount_unavailable' }) }; }
    return response({ ...catalog(), amounts_micros: changed ? [20_000_000] : [10_000_000] });
  };
  const fixture = await mount();
  try {
    await click(button(fixture.host, 'recharge.title'));
    await click(button(fixture.host, 'recharge.continue'));
    assert.equal(localStorage.getItem('athena-recharge-intent:user_a'), null);
    assert.match(fixture.host.textContent, /recharge.amountChanged/);
    assert.equal(fixture.host.querySelector('[role="group"]').textContent, '$20.00');
    assert.ok(button(fixture.host, 'recharge.continue'));
  } finally { await fixture.close(); }
});

test('repeated submit clicks send one invoice request and an unknown receipt retains its recovery key', async () => {
  let posts = 0, complete;
  globalThis.fetch = async (_url, init = {}) => {
    if (init.method !== 'POST') return response(catalog());
    posts++;
    return new Promise(resolve => { complete = () => resolve(response(order({ status: 'unknown', checkout_url: null }))); });
  };
  const fixture = await mount();
  try {
    await click(button(fixture.host, 'recharge.title'));
    const submit = button(fixture.host, 'recharge.continue');
    await act(async () => { submit.click(); submit.click(); submit.click(); });
    assert.equal(posts, 1);
    await act(async () => { complete(); await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.ok(localStorage.getItem('athena-recharge-intent:user_a'));
    assert.ok(button(fixture.host, 'recharge.retry'));
    assert.equal(fixture.host.querySelector('a'), null);
  } finally { await fixture.close(); }
});

test('switching account never exposes the previous account balance while the new balance loads', async () => {
  let calls = 0, nextBalance;
  const api = async () => {
    if (++calls === 1) return { managed: true, available_micros: 10_000_000 };
    return new Promise(resolve => { nextBalance = resolve; });
  };
  const source = ts.transpileModule(readFileSync(new URL('../src/AccountProfile.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const accountModule = { exports: {} };
  vm.runInThisContext(`(function(require,module,exports){${source}\n})`, { filename: 'AccountProfile.tsx' })(name => {
    if (name === './api') return { api };
    if (name === './Recharge') return { Recharge: () => null };
    if (name === './i18n') return { useI18n: () => ({ t: key => key }), errorMessage: code => code };
    return require(name);
  }, accountModule, accountModule.exports);
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  const view = id => h(accountModule.exports.AccountProfile, { user: { id, username: id }, onChange() {} });
  try {
    await act(async () => root.render(view('user_a')));
    assert.match(host.querySelector('.account-credit').textContent, /10/);
    await act(async () => root.render(view('user_b')));
    assert.equal(host.querySelector('.account-credit'), null);
    await act(async () => nextBalance({ managed: true, available_micros: 20_000_000 }));
    assert.match(host.querySelector('.account-credit').textContent, /20/);
  } finally { await act(async () => root.unmount()); host.remove(); }
});
