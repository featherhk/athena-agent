import test from 'node:test';
import assert from 'node:assert/strict';
import { Window } from 'happy-dom';
import { manageModalFocus } from '../src/modal-focus.ts';

function fixture() {
  const window = new Window();
  const document = window.document;
  // DOM-only lifecycle checks. Real visibility and iframe focus are checked in WKWebView.
  window.HTMLElement.prototype.getClientRects = function () { return [{}]; };
  document.body.innerHTML = '<div id="app"><header><button id="menu">Menu</button></header><main><iframe></iframe><textarea>Unsaved</textarea></main><aside inert>Already inactive</aside></div>';
  const root = document.querySelector('#app'), origin = document.querySelector('#menu');
  origin.focus();
  const stop = manageModalFocus(root);
  const flush = () => new Promise(resolve => setTimeout(resolve, 0));
  const dialog = (parent = root) => {
    const overlay = document.createElement('div');
    overlay.innerHTML = '<section role="dialog" aria-modal="true"><button>Close</button><input><button>Finish</button></section>';
    parent.append(overlay);
    return overlay.firstElementChild;
  };
  const tab = (shiftKey = false) => {
    const event = new window.KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true });
    document.activeElement.dispatchEvent(event); return event.defaultPrevented;
  };
  return { window, document, root, origin, stop, flush, dialog, tab,
    close: async () => { stop(); await window.happyDOM.abort(); } };
}

test('a host dialog isolates the renderer and composer without replacing their state', async () => {
  const f = fixture();
  try {
    const frame = f.root.querySelector('iframe'), input = f.root.querySelector('textarea');
    const dialog = f.dialog(); await f.flush();
    assert.equal(f.document.activeElement, dialog);
    assert.equal(f.root.querySelector('main').inert, true);
    assert.equal(f.root.querySelector('header').inert, true);
    dialog.parentElement.remove(); await f.flush();
    assert.equal(f.document.activeElement, f.origin);
    assert.equal(f.root.querySelector('main').inert, false);
    assert.equal(f.root.querySelector('iframe'), frame);
    assert.equal(f.root.querySelector('textarea'), input);
    assert.equal(input.value, 'Unsaved');
    assert.equal(f.root.querySelector('aside').inert, true);
  } finally { await f.close(); }
});

test('Tab stays within visible enabled controls and redirects background focus', async () => {
  const f = fixture();
  try {
    const d = f.dialog();
    d.insertAdjacentHTML('beforeend', '<button disabled>Disabled</button><button hidden>Hidden</button><button tabindex="-1">Skipped</button>');
    await f.flush();
    assert.equal(f.tab(), true); assert.equal(f.document.activeElement, d.children[0]);
    assert.equal(f.tab(true), true); assert.equal(f.document.activeElement, d.children[2]);
    assert.equal(f.tab(), true); assert.equal(f.document.activeElement, d.children[0]);
    f.root.querySelector('textarea').focus();
    assert.ok(d.contains(f.document.activeElement));
  } finally { await f.close(); }
});

test('nested dialogs restore their invoker, then the original shell control', async () => {
  const f = fixture();
  try {
    const outer = f.dialog(); await f.flush();
    outer.children[2].focus();
    const inner = f.dialog(outer); await f.flush();
    assert.equal(f.document.activeElement, inner);
    assert.equal(outer.children[2].inert, true);
    inner.parentElement.remove(); await f.flush();
    assert.equal(f.document.activeElement, outer.children[2]);
    outer.parentElement.remove(); await f.flush();
    assert.equal(f.document.activeElement, f.origin);
  } finally { await f.close(); }
});

test('closing an entire dialog stack resolves the original return focus', async () => {
  const f = fixture();
  try {
    const outer = f.dialog(); await f.flush(); outer.children[2].focus();
    f.dialog(outer); await f.flush(); outer.parentElement.remove(); await f.flush();
    assert.equal(f.document.activeElement, f.origin);
  } finally { await f.close(); }
});

test('new background branches are isolated and disposing keeps pre-existing inert', async () => {
  const f = fixture();
  try {
    f.dialog(); await f.flush();
    const branch = f.document.createElement('button'); f.root.prepend(branch); await f.flush();
    assert.equal(branch.inert, true);
    f.stop();
    assert.equal(branch.inert, false);
    assert.equal(f.root.querySelector('aside').inert, true);
    assert.equal(f.root.querySelector('[role="dialog"]').hasAttribute('tabindex'), false);
  } finally { await f.close(); }
});

test('autofocus before the observer retains its destination and return invoker', async () => {
  const f = fixture();
  try {
    const d = f.dialog(); d.querySelector('input').focus(); await f.flush();
    assert.equal(f.document.activeElement, d.querySelector('input'));
    d.parentElement.remove(); await f.flush();
    assert.equal(f.document.activeElement, f.origin);
  } finally { await f.close(); }
});

test('an empty dialog and a hidden modal cannot leak focus into the renderer', async () => {
  const f = fixture();
  try {
    const d = f.dialog(); d.replaceChildren(); await f.flush();
    assert.equal(f.tab(), true); assert.equal(f.document.activeElement, d);
    d.hidden = true; await f.flush();
    assert.equal(f.document.activeElement, f.origin);
    assert.equal(f.root.querySelector('main').inert, false);
  } finally { await f.close(); }
});

test('replacing the menu with another dialog inherits the original return focus', async () => {
  const f = fixture();
  try {
    const menu = f.dialog(); await f.flush(); menu.children[2].focus();
    menu.parentElement.remove();
    const appearance = f.dialog(); await f.flush();
    assert.equal(menu.hasAttribute('tabindex'), false, 'closed dialog is released immediately');
    appearance.parentElement.remove(); await f.flush();
    assert.equal(f.document.activeElement, f.origin);
  } finally { await f.close(); }
});

test('pointer-induced blur to body retains the last shell focus as return destination', async () => {
  const f = fixture();
  try {
    f.document.body.tabIndex = -1; f.document.body.focus();
    const d = f.dialog(); await f.flush(); d.parentElement.remove(); await f.flush();
    assert.ok(f.document.activeElement === f.origin);
  } finally { await f.close(); }
});

test('a native top-layer portal owns its own focus and Tab behavior', async () => {
  const f = fixture();
  const query = f.document.querySelector.bind(f.document);
  try {
    const outer = f.dialog(); await f.flush();
    const native = f.document.createElement('dialog'); native.innerHTML = '<button>Zoom close</button>';
    // HappyDOM exposes showModal but does not model :modal. Only that browser
    // signal is stubbed here; the actual top layer is accepted in WebKit.
    f.document.querySelector = selector => selector === 'dialog:modal' ? (native.open ? native : null) : query(selector);
    f.document.body.append(native); native.showModal();
    native.firstElementChild.focus();
    assert.ok(f.document.activeElement === native.firstElementChild);
    assert.equal(f.tab(), false, 'native modal receives its normal Tab handling');
    native.close(); native.remove(); outer.querySelector('input').focus();
    assert.equal(f.document.activeElement, outer.querySelector('input'));
  } finally { f.document.querySelector = query; await f.close(); }
});
