import type { Component, Message } from './api';

export type SurfaceState = { provider: 'default'; state: 'mounted' } | {
  provider: 'component'; id: string; version: number; view_revision: number; state: 'loading' | 'mounted';
};
export type SurfaceHost = { revision: string; call: (method: string, args: Record<string, unknown>) => Promise<unknown> };
export type SurfaceHealth = { state: 'mounted' | 'failed'; view_revision: number; activation: number };
export function surfaceFailed(health: SurfaceHealth | undefined, component: Component | undefined): boolean {
  return Boolean(component && health?.state === 'failed' && health.view_revision >= (component.view_revision || 0));
}
export class SurfaceError extends Error {}
export type SurfaceDismissal = { owner: string | null; session: string | null;
  components: { id: string; version: number; view_revision: number }[] };
export function dismissSurfaces(owner: string | null, session: string | null, components: Component[]): SurfaceDismissal {
  return { owner, session, components: components.filter(item => (item.view?.placement || item.placement) === 'surface')
    .map(item => ({ id: item.id, version: item.version, view_revision: item.view_revision || 0 })) };
}
export function surfacesDismissed(dismissal: SurfaceDismissal | null, owner: string | null, session: string | null, components: Component[]): boolean {
  if (!dismissal || dismissal.owner !== owner || dismissal.session !== session) return false;
  return !components.some(item => {
    if (item.view?.hidden || (item.view?.placement || item.placement) !== 'surface') return false;
    const previous = dismissal.components.find(saved => saved.id === item.id);
    // Hiding receipts cannot reopen a standby renderer. A new source version or
    // a later explicit layout activation can, including an agent's layout call.
    return !previous || item.version > previous.version || (item.version === previous.version && (item.view_revision || 0) > previous.view_revision);
  });
}
export const surfaceKey = (component: Component) => `${component.id}:${component.version}:${component.view_revision || 0}`;
export function selectedSurface(components: Component[]): Component | undefined {
  return components.filter(item => !item.view?.hidden && (item.view?.placement || item.placement) === 'surface')
    .sort((a, b) => {
      const date = (item: Component) => [item.created_at || '', item.view_updated_at || ''].sort().at(-1)!;
      const compare = (left: string, right: string) => left < right ? -1 : left > right ? 1 : 0;
      return compare(date(a), date(b)) || compare(a.id, b.id);
    }).at(-1);
}
export function messagePage(messages: Message[], args: Record<string, unknown>) {
  messages = messages.filter(item => item.role === 'user' || item.role === 'assistant');
  const limit = args.limit === undefined ? 30 : args.limit;
  if (!Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 50) throw new SurfaceError('invalid_request');
  const end = args.before === undefined ? messages.length : messages.findIndex(item => item.id === args.before);
  if (end < 0) throw new SurfaceError('message_not_found');
  const start = Math.max(0, end - Number(limit));
  // Explicit fields: provider request snapshots and internal tool transcripts never cross this bridge.
  const items = messages.slice(start, end).map(({ id, role, content, reasoning, status, run_id, attachments, help_reply, origin }) =>
    ({ id, role, content, reasoning, status, run_id, attachments, help_reply, origin }));
  return { items, before: start ? messages[start].id : null, has_more: start > 0 };
}
export function surfaceRecovery(search: string) { return new URLSearchParams(search).get('recovery') === '1'; }

/** Per-frame receipts. Repeated or expired mutation IDs are never executed again. */
export class SurfaceDispatcher {
  private highWater = 0;
  private pending = 0;
  private disposed = false;
  private receipts = new Map<number, Promise<unknown>>();
  private fileReceipts = new Map<number, Promise<unknown>>();
  async dispatch(id: unknown, method: unknown, args: unknown, call: SurfaceHost['call']): Promise<unknown> {
    if (this.disposed) throw new SurfaceError('surface_closed');
    if (!Number.isSafeInteger(id) || Number(id) <= 0 || typeof method !== 'string' || !args || typeof args !== 'object' || Array.isArray(args)) throw new SurfaceError('invalid_request');
    const key = Number(id), existing = this.receipts.get(key) || this.fileReceipts.get(key);
    if (existing) return existing;
    if (key <= this.highWater) throw new SurfaceError('receipt_expired');
    this.highWater = key;
    const file = method === 'file';
    if (this.pending >= 32 || (file && this.fileReceipts.size >= 2)) throw new SurfaceError('surface_busy');
    this.pending++;
    const receipt = Promise.resolve().then(() => {
      if (this.disposed) throw new SurfaceError('surface_closed');
      return call(method, args as Record<string, unknown>);
    }).finally(() => {
      this.pending--;
      // File responses may contain MiBs of image data. Deduplicate only while
      // reading; the high-water mark still rejects a replay after completion.
      if (file) this.fileReceipts.delete(key);
    });
    if (file) this.fileReceipts.set(key, receipt);
    else {
      this.receipts.set(key, receipt);
      if (this.receipts.size > 64) this.receipts.delete(this.receipts.keys().next().value!);
    }
    return receipt;
  }
  dispose() { this.disposed = true; this.receipts.clear(); this.fileReceipts.clear(); }
}
