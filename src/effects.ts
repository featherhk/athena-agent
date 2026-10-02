/** Host-owned styling contract. Components never receive parent DOM access. */
export type TextStyle = {
  color?: string; fontFamily?: string; fontSize?: number; fontWeight?: number | 'normal' | 'bold';
  fontStyle?: 'normal' | 'italic' | 'oblique'; textDecoration?: 'none' | 'underline' | 'line-through';
  opacity?: number; transform?: string; letterSpacing?: number;
};
export type TextStyleEffect = { message_id: string; unit: 'character' | 'word'; styles: TextStyle[] };
export type SurfaceTheme = { tokens: Record<string, string>; font_family: string; scheme: 'light' | 'dark' };
export type SurfaceLayout = {
  viewport: { width: number; height: number };
  messages: { id: string; role: 'user' | 'assistant'; rect: { x: number; y: number; width: number; height: number } }[];
  reduced_motion: boolean; locale: string; theme?: SurfaceTheme;
};
const numberIn = (value: unknown, min: number, max: number): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;

function boundedTransform(value: unknown): value is string {
  if (value === 'none') return true;
  if (typeof value !== 'string' || value.length > 160) return false;
  const operations = [...value.matchAll(/(translateX|translateY|translate|rotate|scaleX|scaleY|scale)\(([^)]+)\)/g)];
  if (!operations.length || operations.length > 4 || operations.map(item => item[0]).join('') !== value.replace(/\)\s+(?=[a-z])/g, ')')) return false;
  return operations.every(([, name, args]) => {
    const parts = args.split(',').map(part => part.trim());
    if (parts.length > (name === 'translate' || name === 'scale' ? 2 : 1)) return false;
    return parts.every(part => {
      if (name.startsWith('translate')) return /^-?(?:\d+(?:\.\d+)?|\.\d+)(?:px|%)$/.test(part) && Math.abs(parseFloat(part)) <= 512;
      if (name === 'rotate') return /^-?(?:\d+(?:\.\d+)?|\.\d+)deg$/.test(part) && Math.abs(parseFloat(part)) <= 3600;
      return /^(?:\d+(?:\.\d+)?|\.\d+)$/.test(part) && numberIn(Number(part), .05, 4);
    });
  });
}

export function parseTextStyleEffect(value: unknown): TextStyleEffect | null | undefined {
  if (value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const effect = value as Record<string, unknown>;
  if (Object.keys(effect).some(key => !['message_id', 'unit', 'styles'].includes(key))
      || typeof effect.message_id !== 'string' || !/^[\w-]{1,64}$/.test(effect.message_id)
      || !['character', 'word'].includes(String(effect.unit))
      || !Array.isArray(effect.styles) || effect.styles.length < 1 || effect.styles.length > 256) return undefined;
  const styles: TextStyle[] = [];
  for (const candidate of effect.styles) {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate) || !Object.keys(candidate).length) return undefined;
    const style: TextStyle = {};
    for (const [key, item] of Object.entries(candidate)) {
      if (key === 'color' && typeof item === 'string' && /^(?:#[\da-f]{3,4}|#[\da-f]{6}|#[\da-f]{8}|[a-z]{1,24})$/i.test(item)) style.color = item;
      else if (key === 'fontFamily' && typeof item === 'string' && /^[\p{L}\p{M}\p{N}\s'",_-]{1,100}$/u.test(item) && !/[\r\n]/.test(item)) style.fontFamily = item;
      else if (key === 'fontSize' && numberIn(item, 8, 72)) style.fontSize = item;
      else if (key === 'fontWeight' && (numberIn(item, 100, 900) || item === 'normal' || item === 'bold')) style.fontWeight = item;
      else if (key === 'fontStyle' && (item === 'normal' || item === 'italic' || item === 'oblique')) style.fontStyle = item;
      else if (key === 'textDecoration' && (item === 'none' || item === 'underline' || item === 'line-through')) style.textDecoration = item;
      else if (key === 'opacity' && numberIn(item, 0, 1)) style.opacity = item;
      else if (key === 'letterSpacing' && numberIn(item, -2, 8)) style.letterSpacing = item;
      else if (key === 'transform' && boundedTransform(item)) style.transform = item;
      else return undefined;
    }
    styles.push(style);
  }
  return { message_id: effect.message_id, unit: effect.unit as TextStyleEffect['unit'], styles };
}

export function readSurfaceTheme(app: Element): SurfaceTheme {
  const style = getComputedStyle(app.ownerDocument.documentElement);
  const defaults: Record<string, string> = { '--canvas': '#fcf9fc', '--surface': '#fdfbfe', '--wash': '#f5edf9', '--tint': '#eadef3', '--ink': '#453b50', '--muted': '#82738f', '--accent': '#9570bd', '--on-accent': '#ffffff' };
  const tokens = Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => [key, style.getPropertyValue(key).trim() || fallback]));
  return { tokens, font_family: style.fontFamily, scheme: style.colorScheme === 'dark' ? 'dark' : 'light' };
}

export function measureSurface(app: Element, frame: HTMLIFrameElement, locale: string, reduced: boolean, theme?: SurfaceTheme): SurfaceLayout {
  const bounds = frame.getBoundingClientRect();
  const width = frame.clientWidth, height = frame.clientHeight;
  const scaleX = bounds.width ? width / bounds.width : 1, scaleY = bounds.height ? height / bounds.height : 1;
  const round = (value: number) => Math.round(value * 100) / 100;
  const messages: SurfaceLayout['messages'] = [];
  for (const element of app.querySelectorAll<HTMLElement>('[data-message-id][data-message-role]')) {
    const id = element.dataset.messageId, role = element.dataset.messageRole;
    if (!id || (role !== 'user' && role !== 'assistant')) continue;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= bounds.top || rect.top >= bounds.bottom || rect.right <= bounds.left || rect.left >= bounds.right) continue;
    messages.push({ id, role, rect: { x: round((rect.left - bounds.left) * scaleX), y: round((rect.top - bounds.top) * scaleY), width: round(rect.width * scaleX), height: round(rect.height * scaleY) } });
    if (messages.length === 128) break;
  }
  return { viewport: { width, height }, messages, reduced_motion: reduced, locale, ...(theme ? { theme } : {}) };
}
