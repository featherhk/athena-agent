import type { Component } from './api';

export type DesktopRect = { x: number; y: number; width: number; height: number };
export type DesktopState = {
  platform: 'macos'; supported: true; presented: boolean;
  screens: { id: string; frame: DesktopRect; visible_frame: DesktopRect; scale: number }[];
  components: { id: string; version: number; view_revision: number; state: 'loading' | 'mounted' | 'hidden' | 'failed'; visible: boolean; rect: DesktopRect; screen_id: string; error_code?: string }[];
};
declare global {
  interface Window { __ATHENA_DESKTOP__?: { readonly version: number }; webkit?: { messageHandlers?: { athenaDesktop?: { postMessage: (value: unknown) => void } } } }
}

/** The URL chooses presentation only inside a native, main-frame host. */
export function desktopRole(win: Window = window): 'companion' | 'widget' | null {
  if (win.__ATHENA_DESKTOP__?.version !== 1 || !win.webkit?.messageHandlers?.athenaDesktop) return null;
  const role = new URLSearchParams(win.location.search).get('desktop');
  return role === 'companion' || role === 'widget' ? role : null;
}
export function desktopPost(value: Record<string, unknown>, win: Window = window): boolean {
  if (!desktopRole(win)) return false;
  try { win.webkit!.messageHandlers!.athenaDesktop!.postMessage(value); return true; }
  catch { return false; }
}
export function interactionVisible(doc: Document = document): boolean {
  return !doc.hidden && doc.documentElement?.dataset?.desktopPresented !== 'false';
}
export function setDesktopPresented(presented: boolean, doc: Document = document) {
  if (doc.documentElement.dataset.desktopPresented === String(presented)) return;
  doc.documentElement.dataset.desktopPresented = String(presented);
  doc.dispatchEvent(new doc.defaultView!.Event('visibilitychange'));
}
const number = (value: unknown, lower: number, upper: number): value is number => typeof value === 'number' && Number.isFinite(value) && value >= lower && value <= upper;
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value);
const record = (value: unknown, keys: readonly string[]): value is Record<string, unknown> => Boolean(value && typeof value === 'object'
  && !Array.isArray(value) && Object.keys(value).every(key => keys.includes(key)));
const rect = (value: unknown): value is DesktopRect => record(value, ['x', 'y', 'width', 'height'])
  && number(value.x, -131072, 131072) && number(value.y, -131072, 131072)
  && number(value.width, 0, 32768) && number(value.height, 0, 32768);
const desktopErrors = new Set(['native_unavailable', 'panel_failed', 'navigation_failed', 'script_error',
  'unhandled_rejection', 'blocked_resource', 'empty_document', 'load_timeout']);

/** Reject malformed native observations before they can disrupt the UI or its authenticated reporter. */
export function readDesktopState(value: unknown): DesktopState | null {
  if (!record(value, ['platform', 'supported', 'presented', 'screens', 'components'])
    || value.platform !== 'macos' || value.supported !== true || typeof value.presented !== 'boolean'
    || !Array.isArray(value.screens) || value.screens.length > 16 || !Array.isArray(value.components) || value.components.length > 32) return null;
  const screenIds = new Set<string>(), componentIds = new Set<string>();
  const screens: DesktopState['screens'] = [], components: DesktopState['components'] = [];
  for (const screen of value.screens) {
    if (!record(screen, ['id', 'frame', 'visible_frame', 'scale']) || !identifier(screen.id) || screenIds.has(screen.id)
      || !rect(screen.frame) || !rect(screen.visible_frame) || !number(screen.scale, .5, 8)) return null;
    screenIds.add(screen.id);
    screens.push({ id: screen.id, frame: { ...screen.frame }, visible_frame: { ...screen.visible_frame }, scale: screen.scale });
  }
  for (const component of value.components) {
    if (!record(component, ['id', 'version', 'view_revision', 'state', 'visible', 'rect', 'screen_id', 'error_code'])
      || !identifier(component.id) || componentIds.has(component.id)
      || typeof component.version !== 'number' || !Number.isSafeInteger(component.version) || component.version < 1
      || typeof component.view_revision !== 'number' || !Number.isSafeInteger(component.view_revision) || component.view_revision < 0
      || typeof component.state !== 'string' || !['loading', 'mounted', 'hidden', 'failed'].includes(component.state)
      || typeof component.visible !== 'boolean' || !rect(component.rect)
      || !identifier(component.screen_id) || !screenIds.has(component.screen_id)
      || ((!value.presented || component.state === 'hidden') && component.visible)) return null;
    const error = component.error_code;
    if (component.state === 'failed' ? typeof error !== 'string' || !desktopErrors.has(error) : error !== undefined && error !== null) return null;
    componentIds.add(component.id);
    components.push({ id: component.id, version: component.version, view_revision: component.view_revision,
      state: component.state as DesktopState['components'][number]['state'], visible: component.visible,
      rect: { ...component.rect }, screen_id: component.screen_id, ...(typeof error === 'string' ? { error_code: error } : {}) });
  }
  return { platform: 'macos', supported: true, presented: value.presented, screens, components };
}
export function desktopScene(owner: string | null, session: string | null, components: Component[], disabled = false) {
  return { action: 'scene', scope: owner && session ? `${owner}:${session}` : '', session_id: owner && session ? session : '',
    items: disabled || !owner || !session ? [] : components.filter(item => (item.view?.placement || item.placement) === 'desktop').slice(0, 32)
      .map(item => ({ id: item.id, version: item.version, view_revision: item.view_revision || 0, title: item.title,
        ...Object.fromEntries(Object.entries(item.view || {}).filter(([key]) => ['hidden', 'x', 'y', 'width', 'height', 'screen_id'].includes(key))) })) };
}

/** Geometry only; no generated HTML, drafts, text or screenshots cross this path. */
export function companionHitRegions(doc: Document = document): DesktopRect[] {
  return [...doc.querySelectorAll<HTMLElement>('[data-desktop-hit],.scene-overlay .canvas-action,.overlay,.developer-panel,.voice-call,.voice-problem,.voice-recovery,.toast,dialog[open]' )].filter(node => !node.hidden && node.getClientRects().length && doc.defaultView?.getComputedStyle(node).visibility !== 'hidden')
    .slice(0, 64).map(node => { const box = node.getBoundingClientRect(); return { x: box.x, y: box.y, width: box.width, height: box.height }; });
}
