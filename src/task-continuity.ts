/** Account-wide task state. Polling and acknowledgements never execute a task. */
export type ActivityItem = {
  run_id: string; session_id: string; title: string; status: string; phase: string;
  attention_key: string; created_at: string; updated_at: string; needs_user: boolean;
  help_id?: string | null; error_code?: string | null;
};
export type ActivityState = { owner_id: string; items: ActivityItem[]; has_more: boolean; error: boolean; limit: number };
type Transport = (path: string, signal: AbortSignal, body?: unknown) => Promise<unknown>;
type Timer = ReturnType<typeof setTimeout>;
type Clock = { later: (callback: () => void, delay: number) => Timer; cancel: (timer: Timer) => void };
const clock: Clock = { later: (callback, delay) => setTimeout(callback, delay), cancel: timer => clearTimeout(timer) };
export const activeActivity = (item: ActivityItem) => ['queued', 'running', 'waiting_user', 'waiting_agents'].includes(item.status);
export const activityPriority = (item: ActivityItem) => item.needs_user ? 0 : ['failed', 'interrupted'].includes(item.status) ? 1 : !activeActivity(item) ? 2 : 3;
export function activityGroups(items: ActivityItem[], currentSession: string | null, includeCurrentTerminal = false) {
  const groups = new Map<string, ActivityItem[]>();
  for (const item of items) {
    if (item.session_id === currentSession && (!includeCurrentTerminal || activeActivity(item))) continue;
    groups.set(item.session_id, [...(groups.get(item.session_id) || []), item]);
  }
  return [...groups.values()].map(group => group.sort((a, b) => activityPriority(a) - activityPriority(b) || b.updated_at.localeCompare(a.updated_at)))
    .sort((a, b) => activityPriority(a[0]) - activityPriority(b[0]) || b[0].updated_at.localeCompare(a[0].updated_at));
}
/** The compact companion only replaces its status for an actionable
 * update elsewhere. Terminal items in this feed have not been acknowledged. */
export function companionAttention(items: ActivityItem[], currentSession: string | null): {
  count: number; label: 'continuity.needsYou' | 'continuity.failed' | 'continuity.ready';
} | null {
  const groups = activityGroups(items.filter(item => (item.needs_user && activeActivity(item)) || ['complete', 'failed'].includes(item.status)), currentSession);
  if (!groups.length) return null;
  return { count: groups.length, label: groups.some(group => group.some(item => item.needs_user)) ? 'continuity.needsYou'
    : groups.some(group => group.some(item => item.status === 'failed')) ? 'continuity.failed' : 'continuity.ready' };
}
export function readableActivity(item: ActivityItem, session: string | null, runs: { id: string; status: string }[], visible: boolean) {
  return visible && item.session_id === session && !activeActivity(item) && runs.some(run => run.id === item.run_id && run.status === item.status);
}
function validItem(value: unknown): value is ActivityItem {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return ['run_id', 'session_id', 'title', 'status', 'attention_key', 'created_at', 'updated_at'].every(key => typeof item[key] === 'string')
    && typeof item.needs_user === 'boolean' && ['queued', 'running', 'waiting_user', 'waiting_agents', 'complete', 'failed', 'cancelled', 'interrupted'].includes(item.status as string);
}

/** An old 401 may arrive after another tab renews the shared cookie. Recheck
 * identity without replaying the original request, which may be a mutation. */
export async function requestActivity(owner: string, path: string, signal: AbortSignal, identityLost: () => void,
  body?: unknown, fetcher: typeof fetch = fetch): Promise<unknown> {
  const response = await fetcher('/api' + path, { signal, ...(body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }) });
  if (signal.aborted) throw new Error('aborted');
  if (response.status === 401) {
    const auth = await fetcher('/api/auth/me', { signal });
    if (signal.aborted) throw new Error('aborted');
    if (!auth.ok) throw new Error('identity_unavailable');
    const identity = await auth.json();
    if (signal.aborted) throw new Error('aborted');
    if (!identity || typeof identity !== 'object' || !('user' in identity)
      || (identity.user !== null && typeof identity.user?.id !== 'string')) throw new Error('identity_unavailable');
    if (identity.user?.id !== owner) identityLost();
    throw new Error(identity.user?.id === owner ? 'activity_reauthenticated' : 'login_required');
  }
  if (!response.ok) throw new Error('activity_unavailable');
  return response.json();
}

export class ActivityFeed {
  state: ActivityState;
  private transport: Transport;
  private changed: (state: ActivityState) => void;
  private identityLost: () => void;
  private clock: Clock;
  private disposed = false;
  private visible = false;
  private timer: Timer | undefined;
  private polling: AbortController | null = null;
  private again = false;
  private mutation = 0;
  private requests = new Set<AbortController>();
  private acknowledging = new Set<string>();
  constructor(owner: string, transport: Transport, changed: (state: ActivityState) => void, identityLost: () => void, scheduler = clock) {
    this.state = { owner_id: owner, items: [], has_more: false, error: false, limit: 30 };
    this.transport = transport; this.changed = changed; this.identityLost = identityLost; this.clock = scheduler;
  }
  private publish(change: Partial<ActivityState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...change }; this.changed(this.state);
  }
  private checkOwner(value: unknown): value is Record<string, unknown> {
    if (this.disposed) return false;
    if (!value || typeof value !== 'object' || typeof (value as Record<string, unknown>).owner_id !== 'string') throw new Error('invalid_activity');
    if ((value as Record<string, unknown>).owner_id !== this.state.owner_id) {
      this.publish({ items: [], has_more: false, error: true }); this.stop(); this.identityLost(); return false;
    }
    return true;
  }
  private cancelTimer() { if (this.timer !== undefined) this.clock.cancel(this.timer); this.timer = undefined; }
  resume() { if (this.disposed) return; this.visible = true; this.refresh(); }
  pause() {
    this.visible = false; this.again = false; this.cancelTimer();
    this.polling?.abort(); this.polling = null;
  }
  stop() { this.disposed = true; this.pause(); for (const request of this.requests) request.abort(); this.requests.clear(); }
  refresh() {
    if (this.disposed || !this.visible) return;
    this.cancelTimer();
    if (this.polling) { this.again = true; return; }
    const controller = new AbortController(), revision = this.mutation;
    this.polling = controller; this.requests.add(controller);
    const deadline = this.clock.later(() => controller.abort(), 15000);
    void this.transport(`/activity?limit=${this.state.limit}`, controller.signal).then(value => {
      if (controller.signal.aborted || this.polling !== controller || !this.checkOwner(value)) return;
      if (!Array.isArray(value.items) || value.items.length > 100 || !value.items.every(validItem) || typeof value.has_more !== 'boolean') throw new Error('invalid_activity');
      // A GET started before an acknowledgement must not resurrect the item.
      if (revision !== this.mutation) { this.again = true; return; }
      this.publish({ items: value.items, has_more: value.has_more, error: false });
    }).catch(() => { if (!this.disposed && this.polling === controller && this.visible) this.publish({ error: true }); })
      .finally(() => {
        this.clock.cancel(deadline); this.requests.delete(controller);
        if (this.polling !== controller) return;
        this.polling = null;
        if (this.disposed || !this.visible) return;
        if (this.again) { this.again = false; this.refresh(); }
        else this.timer = this.clock.later(() => { this.timer = undefined; this.refresh(); }, 5000);
      });
  }
  loadMore() { this.publish({ limit: Math.min(100, this.state.limit + 30) }); this.refresh(); }
  async acknowledge(item: ActivityItem) {
    const key = `${item.run_id}:${item.attention_key}`;
    if (this.disposed || this.acknowledging.has(key)) return false;
    this.acknowledging.add(key); this.mutation++;
    const controller = new AbortController(); this.requests.add(controller);
    const deadline = this.clock.later(() => controller.abort(), 15000);
    try {
      const value = await this.transport('/activity/ack', controller.signal, { run_id: item.run_id, attention_key: item.attention_key });
      if (controller.signal.aborted || !this.checkOwner(value)) return false;
      if (typeof value.acknowledged !== 'boolean' || (value.item !== null && !validItem(value.item))) throw new Error('invalid_activity');
      const current = this.state.items.find(row => row.run_id === item.run_id);
      // An old answer cannot erase a newer help request/completion from this run.
      if (current?.attention_key === item.attention_key) {
        const items = this.state.items.filter(row => row.run_id !== item.run_id);
        if (value.item) items.push(value.item as ActivityItem);
        this.publish({ items, error: false });
      }
      return value.acknowledged;
    } catch { if (!controller.signal.aborted) this.publish({ error: true }); return false; }
    finally {
      // Also invalidate GETs launched while the ACK was in flight: their DB read
      // may precede its commit even when their response arrives after this one.
      this.mutation++;
      this.clock.cancel(deadline); this.requests.delete(controller); this.acknowledging.delete(key); this.refresh();
    }
  }
  async stopSession(session: string) {
    if (this.disposed) return false;
    const controller = new AbortController(); this.requests.add(controller);
    const deadline = this.clock.later(() => controller.abort(), 15000);
    try {
      const value = await this.transport(`/sessions/${encodeURIComponent(session)}/stop`, controller.signal, {});
      if (!value || typeof value !== 'object' || (value as { ok?: unknown }).ok !== true) throw new Error('invalid_stop_receipt');
      if (this.disposed || controller.signal.aborted) return false;
      this.publish({ error: false }); return true;
    } catch { if (!controller.signal.aborted) this.publish({ error: true }); return false; }
    finally { this.clock.cancel(deadline); this.requests.delete(controller); this.refresh(); }
  }
}
