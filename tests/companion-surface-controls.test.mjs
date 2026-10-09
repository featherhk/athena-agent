import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { Window } from 'happy-dom';
import { act, createElement as h } from 'react';
import { companionHitRegions } from '../src/desktop-bridge.ts';

const window = new Window({ url: 'https://athena.invalid' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node']) {
  Object.defineProperty(globalThis, name, { configurable: true, value: name === 'window' ? window : window[name] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url), module = { exports: {} };
const compiled = ts.transpileModule(readFileSync(new URL('../src/CompanionSurfaceControls.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
vm.runInThisContext(`(function(require,module,exports){${compiled}\n})`, { filename: 'CompanionSurfaceControls.tsx' })(name => {
  if (name.endsWith('.css')) return {};
  if (name === './i18n') return { useI18n: () => ({ t: key => key }) };
  return require(name);
}, module, module.exports);
const Controls = module.exports.default;
after(() => window.happyDOM.abort());

test('generated surfaces retain trusted Back, Stop and Reset with native pointer regions', async () => {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host), calls = [];
  const props = { back: () => calls.push('back'), stop: () => calls.push('stop'), reset: () => calls.push('reset'), resetting: false };
  try {
    await act(async () => root.render(h(Controls, props)));
    const navigation = host.querySelector('nav'), buttons = [...navigation.querySelectorAll('button')];
    assert.equal(navigation.getAttribute('aria-label'), 'chatControls');
    assert.deepEqual(buttons.map(button => button.textContent), ['backToChat', 'stop', 'reset']);
    for (const button of buttons) {
      assert.equal(button.type, 'button', 'recovery must not submit a surrounding draft');
      await act(async () => button.click());
    }
    assert.deepEqual(calls, ['back', 'stop', 'reset']);
    navigation.getClientRects = () => [{}];
    navigation.getBoundingClientRect = () => ({ x: 18, y: 8, width: 524, height: 44 });
    assert.deepEqual(companionHitRegions(document), [{ x: 18, y: 8, width: 524, height: 44 }]);
    await act(async () => root.render(h(Controls, { ...props, resetting: true })));
    assert.equal(host.querySelectorAll('button')[2].disabled, true);
    assert.equal(host.querySelectorAll('button')[1].disabled, false, 'Stop stays available during reset');
    await act(async () => host.querySelectorAll('button')[2].click());
    assert.deepEqual(calls, ['back', 'stop', 'reset']);
  } finally { await act(async () => root.unmount()); host.remove(); }
});
