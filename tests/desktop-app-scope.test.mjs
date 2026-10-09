import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { readDesktopState, setDesktopPresented, interactionVisible } from '../src/desktop-bridge.ts';
import { observeFrontend } from '../src/frontend-observation.ts';

// Execute the real App handler and observation spread with mutable scope refs.
// A second implementation would miss the pre-React-commit closure boundary.
const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
const nativeAssignment = app.body.statements.find(node => ts.isExpressionStatement(node)
  && ts.isBinaryExpression(node.expression) && node.expression.left.getText(source) === 'nativeActions.current');
const observation = app.body.statements.filter(ts.isVariableStatement)
  .flatMap(node => [...node.declarationList.declarations]).find(node => node.name.getText(source) === 'frontendObservation');
const desktopSpread = observation.initializer.arguments[2].properties.find(node => ts.isSpreadAssignment(node) && node.getText(source).includes('desktopState'));
assert.ok(nativeAssignment && desktopSpread, 'Keep tests bound to the actual App native action and facts paths');
const compiled = ts.transpileModule(`
  function bind({user,session,snapshot,busy,waiting,recoveryMode,resetting}) {
    ${nativeAssignment.getText(source)}
    return nativeActions.current;
  }
  function desktopFacts({desktopState,user,session}) { return { ${desktopSpread.getText(source)} }; }
`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const copy = value => structuredClone(value);
function native(presented = true, version = 2) {
  return { platform: 'macos', supported: true, presented,
    screens: [{ id: '1', frame: { x: 0, y: 0, width: 1440, height: 900 }, visible_frame: { x: 0, y: 24, width: 1440, height: 830 }, scale: 2 }],
    components: [{ id: 'collector', version, view_revision: 1, screen_id: '1', state: 'mounted', visible: presented, rect: { x: 100, y: 60, width: 200, height: 200 } }],
  };
}
function document() {
  return { hidden: false, documentElement: { dataset: {} }, defaultView: { innerWidth: 1200, innerHeight: 800, Event: class { constructor(type) { this.type = type; } } },
    dispatchEvent() {}, querySelector: () => null, querySelectorAll: () => [] };
}
function fixture() {
  const doc = document(), calls = [], presentations = [];
  const state = { user: { id: 'alice' }, session: 'trip', busy: false, waiting: false, recoveryMode: false, resetting: false,
    snapshot: { components: [{ id: 'collector', version: 2, placement: 'desktop', view_revision: 1 }] }, desktopState: undefined };
  const uiScope = { current: { owner: 'alice', session: 'trip', generation: 1, control: 0 } };
  const context = vm.createContext({ nativeActions: { current: () => {} }, uiScope, readDesktopState,
    setDesktopState: value => { state.desktopState = typeof value === 'function' ? value(state.desktopState) : value; },
    setDesktopPresented: value => { presentations.push(value); setDesktopPresented(value, doc); },
    interactionVisible: () => interactionVisible(doc),
    submit: text => { calls.push({ action: 'submit', text }); }, navigate: session => { calls.push({ action: 'navigate', session }); },
    placementOf: component => component.view?.placement || component.placement,
    setDesktopReplyOpen() {}, setDesktopLog() {}, setMenu() {}, setHistory() {}, stop() {}, reset() {},
    continuity: { state: { items: [] } }, input: { current: null }, document: doc,
    window: { dispatchEvent() {} }, Event: doc.defaultView.Event, requestAnimationFrame: callback => callback(),
  });
  vm.runInContext(compiled, context);
  return { doc, state, uiScope, calls, presentations,
    render: () => context.bind({ ...state }), facts: () => copy(context.desktopFacts(state)),
    move(owner, session, commit = false) {
      uiScope.current = { ...uiScope.current, owner, session, generation: uiScope.current.generation + 1 };
      if (commit) { state.user = owner ? { id: owner } : null; state.session = session; }
    },
  };
}
const action = (kind, extra = {}) => ({ action: kind, scope: 'alice:trip', component_id: 'collector', version: 2, text: 'Inspect the file', session_id: 'notes', ...extra });
const facts = (desktop) => ({ desktop, sessionId: 'trip', components: [], preview: null, helpIds: [], choosingFiles: false,
  uploading: false, reconnecting: false, hasError: false, hasUnsentChanges: false, attachmentIds: [] });

test('the current native scene is observed and its exact component relays submit/navigation once', () => {
  const f = fixture(), dispatch = f.render();
  dispatch({ action: 'state', scope: 'alice:trip', desktop: native() });
  assert.equal(f.facts().desktop.components[0].version, 2);
  const receipt = f.state.desktopState;
  dispatch({ action: 'state', scope: 'alice:trip', desktop: native() });
  assert.equal(f.state.desktopState, receipt, 'An identical native receipt must preserve state identity instead of rendering again');
  dispatch(action('submit')); dispatch(action('navigate'));
  assert.deepEqual(f.calls, [{ action: 'submit', text: 'Inspect the file' }, { action: 'navigate', session: 'notes' }]);
});

test('cross-account and old-session native events cannot use an old React action closure', () => {
  for (const next of [['alice', 'notes'], ['bob', 'trip'], [null, null]]) {
    const f = fixture(), old = f.render();
    f.move(...next); // navigate/acceptUser updates the ref synchronously, before React.
    old(action('submit')); old(action('navigate'));
    old({ action: 'state', scope: 'alice:trip', desktop: native() });
    assert.deepEqual(f.calls, []); assert.deepEqual(f.presentations, []);
    assert.equal(f.state.desktopState, undefined);
  }
});

test('new-scope events also cannot reuse the previous account or session render closure', () => {
  const f = fixture(), old = f.render();
  f.move('bob', 'trip');
  old(action('submit', { scope: 'bob:trip' })); old(action('navigate', { scope: 'bob:trip' }));
  assert.deepEqual(f.calls, []);
  old({ action: 'state', scope: 'bob:trip', desktop: native() });
  assert.equal(f.state.desktopState.scope, 'bob:trip', 'The current native scope may report state before React commits');
  assert.deepEqual(f.facts(), {}, 'New desktop facts must not join the old render owner/session');
  f.move('bob', 'trip', true);
  assert.equal(f.facts().desktop.components[0].id, 'collector');
});

test('stored desktop observations never transfer to a different session with coincident component IDs', () => {
  const f = fixture(); f.render()({ action: 'state', scope: 'alice:trip', desktop: native() });
  f.move('alice', 'notes'); assert.deepEqual(f.facts(), {});
  f.move('alice', 'notes', true); assert.deepEqual(f.facts(), {});
  f.render()(action('submit')); f.render()(action('navigate'));
  assert.deepEqual(f.calls, []);
});

test('removed, hidden, wrong-version and non-desktop components cannot emit native actions', () => {
  for (const component of [null, { id: 'collector', version: 1, placement: 'desktop' },
    { id: 'collector', version: 2, placement: 'inline' },
    { id: 'collector', version: 2, placement: 'desktop', view: { hidden: true } }]) {
    const f = fixture(); f.state.snapshot.components = component ? [component] : [];
    const dispatch = f.render(); dispatch(action('submit')); dispatch(action('navigate'));
    assert.deepEqual(f.calls, []);
  }
});

test('hidden or recovering native hosts do not accept background component interactions', () => {
  for (const mode of ['hidden', 'recovering', 'resetting']) {
    const f = fixture();
    if (mode === 'hidden') setDesktopPresented(false, f.doc);
    if (mode === 'recovering') f.state.recoveryMode = true;
    if (mode === 'resetting') f.state.resetting = true;
    const dispatch = f.render(); dispatch(action('submit')); dispatch(action('navigate'));
    assert.deepEqual(f.calls, [], mode);
  }
});

test('native hide before React commit keeps observation foreground and desktop visibility consistent', () => {
  const doc = document(), previous = native(), original = copy(previous);
  setDesktopPresented(false, doc);
  const hidden = observeFrontend(doc, facts(previous));
  assert.equal(hidden.foreground, false); assert.equal(hidden.desktop.presented, false);
  assert.ok(hidden.desktop.components.every(component => component.visible === false));
  assert.deepEqual(previous, original, 'Projection must not rewrite the stored native receipt');
  setDesktopPresented(true, doc);
  const shown = observeFrontend(doc, facts(native(false)));
  assert.equal(shown.foreground, true); assert.equal(shown.desktop.presented, true);
  assert.equal(shown.desktop.components[0].visible, false, 'Showing the companion does not invent a component-visible receipt');
});

test('without a presentation marker, a hidden native receipt remains a background observation', () => {
  const result = observeFrontend(document(), facts(native(false)));
  assert.equal(result.desktop.presented, false); assert.equal(result.foreground, false);
});
