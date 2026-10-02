import assert from 'node:assert/strict';
import test from 'node:test';
import { arrangePanels, fitPanel, movePanel, resizePanel, panelView, overlaps } from '../src/component-layout.ts';

const bounds = { x: 12, y: 76, width: 1200, height: 640 };
const component = id => ({ id, title: id, version: 1, html: '', placement: 'top-end' });
const inside = rect => rect.x >= bounds.x && rect.y >= bounds.y && rect.x + rect.width <= bounds.x + bounds.width && rect.y + rect.height <= bounds.y + bounds.height;

test('same-corner panels never overlap; overflow returns to the reading flow', () => {
  const layout = arrangePanels(Array.from({ length: 12 }, (_, index) => component(`panel${index}`)), bounds);
  const panels = Object.values(layout).filter(Boolean);
  assert.ok(panels.length > 1 && panels.length < 12);
  for (const [i, rect] of panels.entries()) {
    assert.ok(inside(rect));
    for (const other of panels.slice(i + 1)) assert.ok(!overlaps(rect, other));
  }
});

test('drag/resize preserve independent top bar and composer even at extreme coordinates', () => {
  const rect = fitPanel({}, 'bottom-end', bounds);
  for (const dx of [-10000, -5, 0, 50, 10000]) for (const dy of [-10000, 0, 10000]) {
    const moved = movePanel(rect, dx, dy, bounds);
    assert.ok(inside(moved));
    const resized = resizePanel(moved, dx, dy, bounds);
    assert.ok(inside(resized));
    assert.deepEqual(fitPanel(panelView(moved, bounds), 'top-start', bounds), moved);
  }
});

test('narrow screens and large clarification forms cannot acquire floating blockers', () => {
  for (const constrained of [{ ...bounds, width: 366 }, { ...bounds, height: 100 }]) {
    assert.deepEqual(arrangePanels([component('weather')], constrained), { weather: null });
  }
});

test('saved positions adapt to viewport changes and respect RTL logical corners', () => {
  const small = { x: 12, y: 76, width: 900, height: 330 };
  const fitted = fitPanel({ width: 1500, height: 1500, x: 1, y: 1 }, 'top-start', small);
  assert.deepEqual(fitted, small);
  assert.equal(fitPanel({}, 'top-start', bounds, true).x, bounds.x + bounds.width - 320);
  assert.equal(fitPanel({}, 'top-end', bounds, true).x, bounds.x);
});

test('last user action keeps its requested position while other panels make room', () => {
  const older = component('older'), current = { ...component('current'), view_updated_at: '2026-10-03T03:00:00Z', view: { x: 0.4, y: 0.3 } };
  const layout = arrangePanels([older, current], bounds);
  assert.deepEqual(layout.current, fitPanel(current.view, current.placement, bounds));
  assert.ok(!layout.older || !overlaps(layout.older, layout.current));
});

test('automatic panels leave the entire reading column clear; deliberate positioning remains possible', () => {
  const reading = { x: 270, y: bounds.y, width: 700, height: bounds.height };
  const layout = arrangePanels([component('one'), component('two'), component('three')], { ...bounds, reading });
  for (const rect of Object.values(layout).filter(Boolean)) assert.ok(!overlaps(rect, reading));
  const manual = { ...component('manual'), view: { x: 0.5, y: 0.5 } };
  const positioned = arrangePanels([manual], { ...bounds, reading });
  assert.ok(positioned.manual && overlaps(positioned.manual, reading));
});
