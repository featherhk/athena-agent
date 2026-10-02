/** Presets are shortcuts; every opaque sRGB color can be a personal theme. */
const tokenNames = ['--canvas', '--surface', '--wash', '--tint', '--ink', '--muted', '--accent', '--on-accent'] as const;
type Color = readonly [number, number, number];
type Tokens = Record<typeof tokenNames[number], string>;

function channels(hex: string): Color {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}
function mix(first: Color, second: Color, amount: number): Color {
  return first.map((value, index) => Math.round(value * (1 - amount) + second[index] * amount)) as unknown as Color;
}
function hex(color: Color) { return '#' + color.map(value => value.toString(16).padStart(2, '0')).join(''); }
function luminance(color: Color) {
  const linear = color.map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
}
function contrast(first: Color, second: Color) {
  const [low, high] = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (high + .05) / (low + .05);
}
function readableInk(background: Color): Color { return luminance(background) > .179 ? [0, 0, 0] : [255, 255, 255]; }

export function customPalette(value: string): { tokens: Tokens; scheme: 'light' | 'dark' } | null {
  if (![4, 7].includes(value.length) || !/^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(value)) return null;
  const normalized = value.length === 4 ? '#' + [...value.slice(1)].map(char => char + char).join('') : value;
  const base = channels(normalized);
  const ink = readableInk(base);
  const opposite = ink[0] === 0 ? [255, 255, 255] as const : [0, 0, 0] as const;
  // Near the contrast threshold, raise a surface away from its text rather than
  // making labels unreadable. Pure black still gets distinct charcoal surfaces.
  const raised = (amount: number) => {
    const candidate = mix(base, ink, amount);
    return contrast(ink, candidate) >= 4.5 ? candidate : mix(base, opposite, amount);
  };
  const surface = raised(.055), wash = raised(.10);
  const text = (start: number) => {
    for (let amount = start; amount <= 1; amount += .025) {
      const candidate = mix(base, ink, amount);
      if ([base, surface, wash].every(background => contrast(candidate, background) >= 4.5)) return candidate;
    }
    return ink;
  };
  const accent = text(.8);
  return {
    scheme: ink[0] === 255 ? 'dark' : 'light',
    tokens: {
      '--canvas': hex(base), '--surface': hex(surface), '--wash': hex(wash), '--tint': hex(mix(base, ink, .23)),
      '--ink': hex(ink), '--muted': hex(text(.65)), '--accent': hex(accent), '--on-accent': hex(readableInk(accent)),
    },
  };
}

export function applyPalette(root: Pick<HTMLElement, 'dataset' | 'style'>, value: string) {
  const custom = customPalette(value);
  root.dataset.palette = custom ? 'custom' : value;
  // Removing every override is essential when reset or another account restores
  // a preset; theme state must never leak into the next session.
  for (const name of tokenNames) {
    if (custom) root.style.setProperty(name, custom.tokens[name]);
    else root.style.removeProperty(name);
  }
  if (custom) root.style.setProperty('color-scheme', custom.scheme);
  else root.style.removeProperty('color-scheme');
}
