import type { Rect } from './component-layout';

/** A canvas contributes its own hit regions, never access to the host DOM. */
export type SurfaceRegion = { id: string; label: string; rect: Rect; pressed?: boolean };
export type SurfaceAction = {
  id: string; kind: 'activate' | 'press' | 'move' | 'release' | 'cancel';
  point: { x: number; y: number }; pointer: 'mouse' | 'touch' | 'pen' | 'keyboard';
};

export function parseSurfaceRegions(value: unknown): SurfaceRegion[] | undefined {
  if (value === null) return [];
  if (!Array.isArray(value) || value.length > 64) return undefined;
  const ids = new Set<string>(), result: SurfaceRegion[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item)
      || Object.keys(item).some(key => !['id', 'label', 'rect', 'pressed'].includes(key))
      || typeof item.id !== 'string' || !/^[\w-]{1,64}$/.test(item.id) || ids.has(item.id)
      || typeof item.label !== 'string' || !item.label.trim() || item.label.length > 160
      || ('pressed' in item && typeof item.pressed !== 'boolean')) return undefined;
    const rect = item.rect;
    if (!rect || typeof rect !== 'object' || Array.isArray(rect)
      || Object.keys(rect).length !== 4 || !['x', 'y', 'width', 'height'].every(key =>
        typeof rect[key] === 'number' && Number.isFinite(rect[key]) && Math.abs(rect[key]) <= 16384)
      || rect.width <= 0 || rect.height <= 0) return undefined;
    ids.add(item.id);
    result.push({ id: item.id, label: item.label.trim(), rect: { ...rect }, ...('pressed' in item ? { pressed: item.pressed } : {}) });
  }
  return result;
}

/** Clip focus/hit boxes as well as pixels: offscreen controls must not trap focus. */
export function visibleSurfaceRegions(regions: SurfaceRegion[], width: number, height: number): SurfaceRegion[] {
  return regions.flatMap(region => {
    const x = Math.max(0, region.rect.x), y = Math.max(0, region.rect.y);
    const right = Math.min(width, region.rect.x + region.rect.width), bottom = Math.min(height, region.rect.y + region.rect.height);
    return right > x && bottom > y ? [{ ...region, rect: { x, y, width: right - x, height: bottom - y } }] : [];
  });
}
