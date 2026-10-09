/** Native companion UI state lives in SQLite, independently of its random port.
 * This storage adapter never executes restored intentions. Existing receipt
 * recovery remains responsible for read-only send/stop reconciliation. */
type Transport = (path: string, body: unknown) => Promise<unknown>;
type Receipt = { writer: string; sequence: number };
export type ResumeClaim = Receipt & { entries: Record<string, string> };
type Context = Receipt & { owner: string; dirty: Map<string, string | null>; pending?: { sequence: number; changes: Record<string, string | null> }; running?: Promise<void>; timer?: ReturnType<typeof setTimeout> };
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const owns = (owner: string, key: string) => key === `athena-session:${owner}` ||
  ['athena-draft:', 'athena-submissions:', 'stops:athena-submissions:', 'athena-help-draft:'].some(prefix => {
    try { return key.startsWith(prefix) && JSON.parse(key.slice(prefix.length))[0] === owner; } catch { return false; }
  });

export function readResumeClaim(value: unknown, writer: string): ResumeClaim {
  const claim = value as ResumeClaim;
  if (!claim || claim.writer !== writer || !Number.isSafeInteger(claim.sequence) || claim.sequence < 0 || !claim.entries
    || typeof claim.entries !== 'object' || Array.isArray(claim.entries) || Object.keys(claim.entries).length > 256
    || Object.values(claim.entries).some(value => typeof value !== 'string')) throw new Error('invalid_response');
  return claim;
}

export class DesktopResumeStore {
  private values = new Map<string, string>();
  private context?: Context;
  private transport: Transport;
  private failure: (error: unknown) => void;
  constructor(transport: Transport, failure: (error: unknown) => void = () => {}) { this.transport = transport; this.failure = failure; }
  activate(owner: string, claim: ResumeClaim) {
    if (!identifier(owner) || Object.keys(claim.entries).some(key => !owns(owner, key))) throw new Error('invalid_response');
    clearTimeout(this.context?.timer);
    for (const key of this.values.keys()) if (owns(owner, key)) this.values.delete(key);
    for (const [key, value] of Object.entries(claim.entries)) this.values.set(key, value);
    this.context = { owner, writer: claim.writer, sequence: claim.sequence, dirty: new Map() };
  }
  deactivate() { clearTimeout(this.context?.timer); this.context = undefined; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) {
    if (this.values.get(key) === value) return;
    this.values.set(key, value); this.changed(key, value);
  }
  removeItem(key: string) {
    if (!this.values.delete(key)) return;
    this.changed(key, null);
  }
  private changed(key: string, value: string | null) {
    const current = this.context;
    if (!current || !owns(current.owner, key)) return;
    current.dirty.set(key, value);
    // Bounded coalescing: continuous typing is saved at most every 150 ms,
    // without postponing durability indefinitely. Send/stop/quit flush now.
    if (!current.timer) current.timer = setTimeout(() => {
      current.timer = undefined;
      if (this.context === current) void this.flush(current.owner).catch(error => { if (this.context === current) this.failure(error); });
    }, 150);
  }
  async flush(owner?: string): Promise<void> {
    const context = this.context;
    if (!context) { if (owner) throw new Error('desktop_resume_unavailable'); return; }
    if (owner && owner !== context.owner) throw new Error('desktop_resume_conflict');
    clearTimeout(context.timer); context.timer = undefined;
    if (context.running) { await context.running; return this.flush(owner); }
    if (!context.pending && !context.dirty.size) return;
    const operation = async () => {
      while (context.pending || context.dirty.size) {
        if (this.context !== context) throw new Error('desktop_resume_conflict');
        if (!context.pending) {
          context.pending = { sequence: context.sequence + 1, changes: Object.fromEntries(context.dirty) };
          context.dirty.clear();
        }
        const request = context.pending;
        // Keep the exact sequence and body until acknowledged: an HTTP timeout
        // can follow a successful DB commit, so a retry must be identical.
        const receipt = await this.transport('/desktop/resume/save', { writer: context.writer, ...request }) as Receipt;
        if (receipt?.writer !== context.writer || receipt.sequence !== request.sequence) throw new Error('invalid_response');
        context.sequence = request.sequence; context.pending = undefined;
      }
    };
    context.running = operation();
    try { await context.running; } finally { context.running = undefined; }
  }
}
