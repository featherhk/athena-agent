import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Window } from 'happy-dom';

const require = createRequire(import.meta.url);
const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const handler = app.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'quitDesktop');
let button;
function find(node) {
  if (ts.isJsxElement(node) && node.openingElement.attributes.properties.some(attr => attr.name?.text === 'data-desktop-quit')) button = node;
  ts.forEachChild(node, find);
}
find(app);
assert.ok(handler && button && ts.isBinaryExpression(button.parent), 'Test must exercise the actual fixed-shell quit control');
const body = ts.transpileModule(`function QuitControl(){${handler.getText(source)};return (${button.parent.getText(source)});}`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;

async function fixture(extra = {}) {
  const window = new Window(), originals = new Map(), calls = [];
  for (const [key, value] of Object.entries({ window, document: window.document, navigator: window.navigator, IS_REACT_ACT_ENVIRONMENT: true })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  const context = vm.createContext({ exports: {}, require, desktop: true, desktopFrozen: false, t: value => value,
    desktopPost: value => { calls.push(['native', JSON.parse(JSON.stringify(value))]); return true; },
    setMenu: value => calls.push(['menu', value]), setError: value => calls.push(['error', value]), ...extra });
  vm.runInContext(body, context);
  const root = createRoot(window.document.body);
  await act(async () => { root.render(React.createElement(context.QuitControl)); });
  return { calls, document: window.document, async close() {
    await act(async () => root.unmount()); window.happyDOM.abort();
    for (const [key, descriptor] of originals) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  } };
}

test('the actual desktop menu quits through the native bridge without clearing drafts or hiding the app', async () => {
  const f = await fixture();
  try {
    const button = f.document.querySelector('[data-desktop-quit]');
    assert.equal(button.textContent, 'desktop.quit');
    await act(async () => button.click());
    assert.deepEqual(f.calls, [['native', { action: 'quit' }], ['menu', false]]);
  } finally { await f.close(); }
});

test('web omits quit, an active final save disables it, and an unavailable host retains the menu', async () => {
  for (const [extra, expected] of [[{ desktop: false }, []], [{ desktopFrozen: true }, []],
    [{ desktopPost: () => false }, [['error', 'error.connection']]]]) {
    const f = await fixture(extra);
    try {
      const button = f.document.querySelector('[data-desktop-quit]');
      if (extra.desktop === false) assert.equal(button, null);
      if (extra.desktopFrozen) assert.equal(button.disabled, true);
      await act(async () => button?.click());
      assert.deepEqual(f.calls, expected);
    } finally { await f.close(); }
  }
});
