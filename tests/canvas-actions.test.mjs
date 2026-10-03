import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

// Exercise the actual event handlers and their shared refs without a DOM or a
// second React renderer. Visible focus, hit testing and layout still need a browser.
const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL('../src/CanvasActions.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const module = { exports: {} };
vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`, {})(
  name => name === 'react' ? { useRef: value => ({ current: value }) } : require(name), module, module.exports,
);
const CanvasActions = module.exports.default;
const region = { id: 'note', label: 'Mark note', rect: { x: 20, y: 30, width: 80, height: 44 }, pressed: false };

function fixture() {
  const actions = [], captures = new Set();
  const button = CanvasActions({ regions: [region], dispatch: action => actions.push(JSON.parse(JSON.stringify(action))) }).props.children[0];
  const target = {
    parentElement: { getBoundingClientRect: () => ({ left: 100, top: 64 }) },
    setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id), releasePointerCapture: id => captures.delete(id),
  };
  const event = changes => ({ currentTarget: target, nativeEvent: {}, isPrimary: true, button: 0,
    pointerId: 1, pointerType: 'mouse', clientX: 124, clientY: 100, detail: 1, ...changes });
  return { button: button.props, actions, captures, event };
}

test('dragging a canvas control releases capture without an accidental click', () => {
  const { button, actions, captures, event } = fixture();
  button.onPointerDown(event());
  button.onPointerMove(event({ clientX: 140 }));
  button.onPointerUp(event({ clientX: 140 }));
  button.onClick(event({ clientX: 140 }));
  assert.deepEqual(actions.map(action => action.kind), ['press', 'move', 'release']);
  assert.deepEqual(actions.at(-1).point, { x: 40, y: 36 });
  assert.equal(captures.size, 0);
  button.onClick(event({ detail: 0 }));
  assert.equal(actions.at(-1).pointer, 'keyboard');
  assert.deepEqual(actions.at(-1).point, { x: 60, y: 52 });
});

test('touch and pen clicks retain their native modality in React mouse wrappers', () => {
  for (const pointerType of ['touch', 'pen']) {
    for (const nativeEvent of [{ pointerType }, {}]) {
      const { button, actions, event } = fixture();
      button.onPointerDown(event({ pointerType }));
      button.onPointerUp(event({ pointerType }));
      const click = event({ nativeEvent });
      delete click.pointerType;
      button.onClick(click);
      assert.equal(actions.at(-1).kind, 'activate');
      assert.equal(actions.at(-1).pointer, pointerType);
      assert.deepEqual(actions.at(-1).point, { x: 24, y: 36 });
    }
  }
});

test('a cancelled drag ends once and does not poison the next pointer gesture', () => {
  const { button, actions, event } = fixture();
  button.onPointerDown(event());
  button.onPointerCancel(event());
  button.onLostPointerCapture(event());
  button.onClick(event());
  assert.deepEqual(actions.map(action => action.kind), ['press', 'cancel']);
  button.onPointerDown(event());
  button.onPointerUp(event());
  button.onClick(event());
  assert.deepEqual(actions.map(action => action.kind), ['press', 'cancel', 'press', 'release', 'activate']);
});

test('a second touch cannot cancel the primary pointer gesture', () => {
  const { button, actions, event } = fixture();
  button.onPointerDown(event({ pointerType: 'touch' }));
  const secondTouch = event({ pointerId: 2, pointerType: 'touch', isPrimary: false });
  button.onPointerDown(secondTouch);
  button.onPointerCancel(secondTouch);
  button.onLostPointerCapture(secondTouch);
  button.onPointerUp(event({ pointerType: 'touch' }));
  button.onClick(event({ pointerType: 'touch' }));
  assert.deepEqual(actions.map(action => action.kind), ['press', 'release', 'activate']);
});
