import type { Component, ComponentView, Placement } from './api';

export type Rect = { x: number; y: number; width: number; height: number };
export type SurfaceBounds = Rect & { reading?: Rect };
export const floatingPlacements = new Set<Placement>(['top-start', 'top-end', 'bottom-start', 'bottom-end']);
export const placementOf = (component: Component): Placement => component.view?.placement || component.placement || 'inline';
export const clamp = (value: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(maximum, value));

export function fitPanel(view: ComponentView, placement: Placement, bounds: Rect, rtl = false, measured = 240): Rect {
  const width = Math.min(bounds.width, view.width || 320), height = Math.min(bounds.height, view.height || Math.max(160, measured + 56));
  const end = placement.endsWith('end') !== rtl;
  const x = view.x ?? (end ? 1 : 0), y = view.y ?? (placement.startsWith('bottom') ? 1 : 0);
  return { x: bounds.x + clamp(x, 0, 1) * (bounds.width - width), y: bounds.y + clamp(y, 0, 1) * (bounds.height - height), width, height };
}

export function movePanel(rect: Rect, dx: number, dy: number, bounds: Rect): Rect {
  return { ...rect, x: clamp(rect.x + dx, bounds.x, bounds.x + bounds.width - rect.width), y: clamp(rect.y + dy, bounds.y, bounds.y + bounds.height - rect.height) };
}

export function resizePanel(rect: Rect, dx: number, dy: number, bounds: Rect): Rect {
  return { ...rect, width: clamp(rect.width + dx, Math.min(240, bounds.width), Math.min(1600, bounds.x + bounds.width - rect.x)),
    height: clamp(rect.height + dy, Math.min(160, bounds.height), Math.min(1600, bounds.y + bounds.height - rect.y)) };
}

export function panelView(rect: Rect, bounds: Rect): ComponentView {
  return { x: bounds.width > rect.width ? clamp((rect.x - bounds.x) / (bounds.width - rect.width), 0, 1) : 0,
    y: bounds.height > rect.height ? clamp((rect.y - bounds.y) / (bounds.height - rect.height), 0, 1) : 0,
    width: Math.max(240, Math.round(rect.width)), height: Math.max(160, Math.round(rect.height)) };
}

export function overlaps(a: Rect, b: Rect, gap = 12) {
  return a.x < b.x + b.width + gap && a.x + a.width + gap > b.x && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
}

/** Pack around requested positions. Overflow joins the conversation, never piles over controls. */
export function arrangePanels(components: Component[], bounds: SurfaceBounds, rtl = false, heights: Record<string, number> = {}): Record<string, Rect | null> {
  const result: Record<string, Rect | null> = {}, occupied: Rect[] = [];
  // The most recently positioned surface keeps the requested place first.
  const ordered = [...components].sort((a, b) => (b.view_updated_at || '').localeCompare(a.view_updated_at || ''));
  for (const component of ordered) {
    if (bounds.width < 876 || bounds.height < 220) { result[component.id] = null; continue; }
    const manualPosition = component.view?.x !== undefined || component.view?.y !== undefined;
    const reading = !manualPosition ? bounds.reading : undefined;
    const freeWidth = reading ? Math.max(reading.x - bounds.x - 12, bounds.x + bounds.width - reading.x - reading.width - 12) : 320;
    if (reading && freeWidth < 240) { result[component.id] = null; continue; }
    const view = { width: Math.min(320, freeWidth), ...component.view };
    const desired = fitPanel(view, placementOf(component), bounds, rtl, heights[component.id]);
    const xs = [desired.x, bounds.x, bounds.x + bounds.width - desired.width, ...occupied.flatMap(rect => [rect.x + rect.width + 12, rect.x - desired.width - 12])];
    const ys = [desired.y, bounds.y, bounds.y + bounds.height - desired.height, ...occupied.flatMap(rect => [rect.y + rect.height + 12, rect.y - desired.height - 12])];
    const candidates = xs.flatMap(x => ys.map(y => ({ ...desired, x, y }))).filter(rect => rect.x >= bounds.x && rect.y >= bounds.y && rect.x + rect.width <= bounds.x + bounds.width && rect.y + rect.height <= bounds.y + bounds.height);
    candidates.sort((a, b) => Math.hypot(a.x - desired.x, a.y - desired.y) - Math.hypot(b.x - desired.x, b.y - desired.y));
    const fit = candidates.find(rect => (!reading || !overlaps(rect, reading)) && occupied.every(other => !overlaps(rect, other))) || null;
    result[component.id] = fit;
    if (fit) occupied.push(fit);
  }
  return result;
}
