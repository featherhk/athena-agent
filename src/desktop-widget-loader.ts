import type { Component, Preferences, Snapshot, User, ViewChange } from './api';

export type DesktopWidgetRequest = { owner: string; session: string; component: string; version: number };
type Request = <T>(path: string, body?: unknown, signal?: AbortSignal) => Promise<T>;
const identifier = (value: string | null): value is string => value !== null && /^[a-zA-Z0-9_-]{1,64}$/.test(value);

/** A URL alone is never authority to enter a native widget host. */
export function desktopWidgetRequest(search: string, nativeWidget: boolean): DesktopWidgetRequest | null {
  if (!nativeWidget) return null;
  const query = new URLSearchParams(search);
  if (['desktop', 'session', 'component', 'version', 'scope'].some(key => query.getAll(key).length !== 1)
      || query.get('desktop') !== 'widget') return null;
  const session = query.get('session'), component = query.get('component'), versionText = query.get('version');
  const scope = (query.get('scope') || '').split(':');
  if (!identifier(session) || !identifier(component) || scope.length !== 2 || !identifier(scope[0])
      || scope[1] !== session || !versionText || !/^[1-9][0-9]{0,9}$/.test(versionText)) return null;
  const version = Number(versionText);
  if (!Number.isSafeInteger(version) || version > 2147483647) return null;
  return { owner: scope[0], session, component, version };
}

export class DesktopWidgetUnavailable extends Error {
  constructor() { super('desktop_widget_unavailable'); }
}

/** The parent native host owns scene reconciliation; child pages own no stream. */
export async function loadDesktopWidget(target: DesktopWidgetRequest, request: Request, signal?: AbortSignal) {
  const identity = await request<{ user: User | null; preferences?: Preferences }>('/auth/me', undefined, signal);
  if (signal?.aborted || identity.user?.id !== target.owner) throw new DesktopWidgetUnavailable();
  const snapshot = await request<Snapshot>(`/sessions/${target.session}`, undefined, signal);
  const component = snapshot.components?.find(item => item.id === target.component && item.version === target.version);
  if (signal?.aborted || !component || typeof component.html !== 'string'
      || (component.view?.placement || component.placement) !== 'desktop') throw new DesktopWidgetUnavailable();
  return { component, preferences: snapshot.preferences || identity.preferences };
}

export async function changeDesktopWidgetView(target: DesktopWidgetRequest, change: ViewChange, request: Request, signal?: AbortSignal) {
  // Other native panels can change layout without reloading this document. Read
  // the current revision but never adopt a different source version or owner.
  const { component } = await loadDesktopWidget(target, request, signal);
  const result = await request<Pick<Component, 'version' | 'view' | 'view_revision' | 'view_updated_at'>>(
    `/sessions/${target.session}/components/${target.component}/view`,
    { version: component.version, view_revision: component.view_revision || 0, view: change }, signal);
  if (signal?.aborted || result.version !== target.version || typeof result.view_revision !== 'number'
      || result.view_revision <= (component.view_revision || 0)) throw new DesktopWidgetUnavailable();
  return { ...component, ...result };
}
