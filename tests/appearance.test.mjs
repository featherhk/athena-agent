import test from 'node:test';
import assert from 'node:assert/strict';
import { applyPalette, customPalette } from '../src/appearance.ts';

function luminance(hex) {
  const rgb = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const [r, g, b] = rgb.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return .2126 * r + .7152 * g + .0722 * b;
}
function contrast(a, b) {
  const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (high + .05) / (low + .05);
}

test('arbitrary color themes retain the requested canvas and readable controls', () => {
  // Include near-threshold greys and saturated colors, not only named presets.
  for (const color of ['#000000', '#ffffff', '#101b35', '#eec0dd', '#ff0000', '#00ff00', '#0000ff', '#757575', '#767676', '#777777', '#ee33aa', '#887122']) {
    const palette = customPalette(color);
    assert.equal(palette.tokens['--canvas'], color);
    for (const surface of ['--canvas', '--surface', '--wash']) {
      for (const text of ['--ink', '--muted', '--accent']) {
        assert.ok(contrast(palette.tokens[text], palette.tokens[surface]) >= 4.5, `${color}: ${text} on ${surface}`);
      }
    }
    assert.ok(contrast(palette.tokens['--on-accent'], palette.tokens['--accent']) >= 4.5);
  }
  assert.equal(customPalette('#000').scheme, 'dark');
  assert.equal(customPalette('#fff').scheme, 'light');
  assert.equal(customPalette('#AbC').tokens['--canvas'], '#aabbcc');
  for (const value of ['dream', '#ffff', '#ffffffff', '#fff\n', '#fff;display:none', 'url(https://invalid.example)']) assert.equal(customPalette(value), null);
});

test('live palette replacement and reset remove all custom styles', () => {
  const styles = new Map();
  const root = { dataset: {}, style: { setProperty: (name, value) => styles.set(name, value), removeProperty: name => styles.delete(name) } };
  applyPalette(root, '#000');
  assert.equal(root.dataset.palette, 'custom');
  assert.equal(styles.get('--canvas'), '#000000');
  assert.equal(styles.get('color-scheme'), 'dark');
  applyPalette(root, '#fff');
  assert.equal(styles.get('--canvas'), '#ffffff');
  assert.equal(styles.get('color-scheme'), 'light');
  applyPalette(root, 'dream');
  assert.equal(root.dataset.palette, 'dream');
  assert.equal(styles.size, 0);
});
