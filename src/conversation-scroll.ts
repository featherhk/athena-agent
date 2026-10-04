export type ScrollPosition = { scrollTop: number; scrollHeight: number; clientHeight: number };
const bottom = (position: ScrollPosition) => Math.max(0, position.scrollHeight - position.clientHeight);
const atBottom = (position: ScrollPosition) => bottom(position) - Math.max(0, position.scrollTop) <= 2;

/** Follow new output until the reader takes control. Scroll position alone is
 * not user intent: layout changes and activity navigation also emit scroll. */
export class ConversationScroll {
  following = true;
  private previous: number | null = null;
  private previousBottom: number | null = null;
  private direction = 0;
  private intentAt = -Infinity;
  private dragging = false;

  resume() {
    this.following = true; this.previous = null; this.previousBottom = null;
    this.direction = 0; this.intentAt = -Infinity; this.dragging = false;
  }
  pause() {
    this.following = false;
    this.direction = 0; this.intentAt = -Infinity; this.dragging = false;
  }
  intent(direction: number, position: ScrollPosition, time = Date.now()) {
    if (!direction) return;
    this.direction = Math.sign(direction); this.intentAt = time;
    this.previous = Math.max(0, position.scrollTop);
    this.previousBottom = bottom(position);
    if (direction < 0) this.following = false;
    else if (atBottom(position)) this.following = true;
  }
  startDrag(position: ScrollPosition, time = Date.now()) {
    this.pause(); this.dragging = true; this.intentAt = time;
    this.previous = Math.max(0, position.scrollTop);
    this.previousBottom = bottom(position);
  }
  endDrag() { this.dragging = false; }
  recentlyInteracted(time = Date.now()) { return time - this.intentAt < 1500; }
  observe(position: ScrollPosition, time = Date.now()) {
    const top = Math.max(0, position.scrollTop);
    const end = bottom(position);
    const delta = this.previous === null ? 0 : top - this.previous;
    const clampedToShorterEnd = this.following && atBottom(position) && this.previousBottom !== null && end < this.previousBottom - .5;
    if (this.dragging && Math.abs(delta) > .5) { this.direction = Math.sign(delta); this.intentAt = time; }
    if (delta < -.5 && !clampedToShorterEnd) {
      this.following = false;
      if (!this.dragging && this.direction > 0) this.direction = 0;
    }
    // Keep a continuous touch/trackpad momentum sequence alive, without
    // treating an unrelated later position change as a fresh user gesture.
    if (Math.abs(delta) > .5 && Math.sign(delta) === this.direction && this.recentlyInteracted(time)) this.intentAt = time;
    // Upward touch rubber-banding or programmatic positioning must never
    // restore following. Only a downward user gesture reaching the end can.
    if (this.direction > 0 && this.recentlyInteracted(time) && atBottom(position)) this.following = true;
    this.previous = top; this.previousBottom = end;
  }
  follow(position: ScrollPosition) {
    if (!this.following) return;
    this.previous = bottom(position); this.previousBottom = this.previous;
    position.scrollTop = this.previous;
  }
}

export function scrollKeyDirection(event: { key: string; shiftKey: boolean; defaultPrevented: boolean; isComposing?: boolean }, target: EventTarget | null): number {
  if (event.defaultPrevented || event.isComposing || !(target instanceof Element)
    || target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return 0;
  if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) return -1;
  if (['ArrowDown', 'PageDown', 'End'].includes(event.key)) return 1;
  if (event.key === ' ' && !target.closest('button,a,[role="button"]')) return event.shiftKey ? -1 : 1;
  return 0;
}

/** A nested scrolling code block owns its own gestures until it reaches an edge. */
export function nestedScrollConsumes(target: EventTarget | null, container: HTMLElement, direction: number): boolean {
  let node = target instanceof Element ? target : null;
  while (node && node !== container) {
    if (node instanceof HTMLElement && node.scrollHeight > node.clientHeight + 2
      && /^(auto|scroll|overlay)$/.test(getComputedStyle(node).overflowY)
      && (direction < 0 ? node.scrollTop > 0 : node.scrollTop + node.clientHeight < node.scrollHeight - 2)) return true;
    node = node.parentElement;
  }
  return false;
}
