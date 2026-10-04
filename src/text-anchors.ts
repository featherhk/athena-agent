import type { Rect } from './component-layout';

export type TextAnchorTarget = { id: string; message_id: string; quote: string; occurrence?: number };
export type TextAnchor = { id: string; message_id: string; status: 'visible' | 'offscreen' | 'missing' | 'ambiguous' | 'limited'; rects: Rect[] };
type Piece = { node: Text; start: number; end: number };
const identifier = /^[\w-]{1,64}$/;

export function parseTextTargets(value: unknown): TextAnchorTarget[] | undefined {
  if (value === null) return [];
  if (!Array.isArray(value) || value.length > 32) return undefined;
  const ids = new Set<string>(), result: TextAnchorTarget[] = [];
  let size = 0;
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item)
      || Object.keys(item).some(key => !['id', 'message_id', 'quote', 'occurrence'].includes(key))
      || typeof item.id !== 'string' || !identifier.test(item.id) || ids.has(item.id)
      || typeof item.message_id !== 'string' || !identifier.test(item.message_id)
      || typeof item.quote !== 'string' || !item.quote.trim() || item.quote.length > 512
      || (item.occurrence !== undefined && (!Number.isInteger(item.occurrence) || item.occurrence < 0 || item.occurrence > 1023))) return undefined;
    size += item.quote.length;
    if (size > 8192) return undefined;
    ids.add(item.id);
    result.push({ id: item.id, message_id: item.message_id, quote: item.quote, ...(item.occurrence !== undefined ? { occurrence: item.occurrence } : {}) });
  }
  return result;
}

/** Build once per body/layout, avoiding repeated growing-string flattening. */
function normalizeText(text: string) {
  const characters: string[] = [];
  const starts: number[] = [], ends: number[] = [];
  for (let i = 0; i < text.length; i++) {
    if (/\s/u.test(text[i])) {
      if (characters.at(-1) === ' ') { ends[ends.length - 1] = i + 1; continue; }
      characters.push(' ');
    } else characters.push(text[i]);
    starts.push(i); ends.push(i + 1);
  }
  return { normalized: characters.join(''), starts, ends };
}

/** Preserve UTF-16 offsets across Markdown inline nodes; normalize whitespace only. */
export function locateQuote(text: string, quote: string, occurrence?: number): { start: number; end: number } | 'missing' | 'ambiguous' {
  return matchQuote(normalizeText(text), quote, occurrence);
}

function matchQuote({ normalized, starts, ends }: ReturnType<typeof normalizeText>, quote: string, occurrence?: number): ReturnType<typeof locateQuote> {
  const needle = quote.replace(/\s+/gu, ' ').trim();
  if (!needle) return 'missing';
  let at = -1, found = -1, count = 0;
  while ((at = normalized.indexOf(needle, at + 1)) !== -1) {
    if (occurrence === undefined && count > 0) return 'ambiguous';
    if (occurrence === count || occurrence === undefined) found = at;
    if (occurrence === count) break;
    count++;
  }
  return found < 0 ? 'missing' : { start: starts[found], end: ends[found + needle.length - 1] };
}

function intersect(a: Rect, b: Rect): Rect | null {
  const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.width, b.x + b.width), bottom = Math.min(a.y + a.height, b.y + b.height);
  return right > x && bottom > y ? { x, y, width: right - x, height: bottom - y } : null;
}

function clipAncestors(start: Element, stop: Element, initial: Rect): Rect | null {
  let clip: Rect | null = initial;
  for (let ancestor: Element | null = start; ancestor && ancestor !== stop && clip; ancestor = ancestor.parentElement) {
    const style = getComputedStyle(ancestor);
    if (style.visibility === 'hidden' || style.display === 'none') return null;
    const clipX = /(auto|scroll|hidden|clip)/.test(style.overflowX), clipY = /(auto|scroll|hidden|clip)/.test(style.overflowY);
    if (clipX || clipY) {
      const box = ancestor.getBoundingClientRect();
      clip = intersect(clip, { x: clipX ? box.left : clip.x, y: clipY ? box.top : clip.y,
        width: clipX ? box.width : clip.width, height: clipY ? box.height : clip.height });
    }
  }
  return clip;
}

/** Only message-body text participates. Never scan the composer, files or account forms. */
export function measureTextTargets(app: Element, frame: HTMLIFrameElement, targets: TextAnchorTarget[]): TextAnchor[] {
  const frameBox = frame.getBoundingClientRect();
  const baseClip: Rect = { x: frameBox.left, y: frameBox.top, width: frameBox.width, height: frameBox.height };
  const scaleX = frameBox.width ? frame.clientWidth / frameBox.width : 1;
  const scaleY = frameBox.height ? frame.clientHeight / frameBox.height : 1;
  const round = (n: number) => Math.round(n * 100) / 100;
  const roots = new Map<string, Element>();
  for (const element of app.querySelectorAll<HTMLElement>('[data-message-id][data-message-role]')) {
    if (element.dataset.messageRole !== 'user' && element.dataset.messageRole !== 'assistant') continue;
    const root = element.querySelector('[data-message-text]');
    if (root && element.dataset.messageId) roots.set(element.dataset.messageId, root);
  }
  const indexes = new Map<Element, { normalized: ReturnType<typeof normalizeText>; pieces: Piece[]; limited: boolean }>();
  return targets.map(target => {
    const empty = (status: TextAnchor['status']): TextAnchor => ({ id: target.id, message_id: target.message_id, status, rects: [] });
    const root = roots.get(target.message_id);
    if (!root) return empty('missing');
    if (!root.getClientRects().length || root.closest('[hidden],[inert]')) return empty('offscreen');
    let index = indexes.get(root);
    if (!index) {
      const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
      let node: Node | null, text = '', previousBlock: Element | null = null, limited = false, visited = 0;
      const pieces: Piece[] = [];
      while ((node = walker.nextNode())) {
        if (++visited > 8192) { limited = true; break; }
        if (node.nodeType === Node.ELEMENT_NODE) {
          if ((node as Element).tagName === 'BR') text += '\n';
          continue;
        }
        const parent = node.parentElement;
        if (!parent || parent.closest('script,style,[hidden],[aria-hidden="true"],button,input,textarea,select')) continue;
        const block = parent.closest('p,li,pre,td,th,blockquote,h1,h2,h3,h4,h5,h6');
        if (pieces.length && block !== previousBlock) text += '\n';
        previousBlock = block;
        const value = node.textContent || '';
        if (pieces.length >= 4096 || text.length + value.length > 64000) { limited = true; break; }
        const start = text.length; text += value;
        if (value) pieces.push({ node: node as Text, start, end: text.length });
      }
      index = { normalized: normalizeText(text), pieces, limited }; indexes.set(root, index);
    }
    // Unknown remainder could contain a duplicate. Never guess a partial match.
    if (index.limited) return empty('limited');
    const match = matchQuote(index.normalized, target.quote, target.occurrence);
    if (typeof match === 'string') return empty(match);
    const clip = clipAncestors(root, app, baseClip);
    if (!clip) return empty('offscreen');
    const rects: Rect[] = [];
    const pieceClips = new Map<Element, Rect | null>();
    for (const piece of index.pieces) {
      if (piece.end <= match.start || piece.start >= match.end) continue;
      const parent = piece.node.parentElement!;
      if (!pieceClips.has(parent)) pieceClips.set(parent, clipAncestors(parent, root, clip));
      const pieceClip = pieceClips.get(parent);
      if (!pieceClip) continue;
      const range = root.ownerDocument.createRange();
      range.setStart(piece.node, Math.max(0, match.start - piece.start));
      range.setEnd(piece.node, Math.min(piece.end - piece.start, match.end - piece.start));
      for (const box of range.getClientRects()) {
        const visible = intersect({ x: box.left, y: box.top, width: box.width, height: box.height }, pieceClip);
        if (!visible) continue;
        rects.push({ x: round((visible.x - frameBox.left) * scaleX), y: round((visible.y - frameBox.top) * scaleY),
          width: round(visible.width * scaleX), height: round(visible.height * scaleY) });
        if (rects.length > 128) return empty('limited');
      }
    }
    return { id: target.id, message_id: target.message_id, status: rects.length ? 'visible' : 'offscreen', rects };
  });
}
