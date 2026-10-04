import type { Draft } from './draft-store';

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
export type SubmissionIntent = {
  id: string; owner: string; session: string; text: string; mode: 'queue' | 'interrupt';
  file_ids: string[]; revision: number; draft: Draft | null; preserveDraft?: boolean;
};
export type SubmissionReceipt = {
  accepted: true; run_id: string; status: string; client_submission_id: string;
  submission_revision: number; replayed: boolean;
};
export type StopIntent = { id: string; owner: string; session: string };
export type SubmissionLookup = ({ found: true } & SubmissionReceipt) | {
  found: false; client_submission_id: string; submission_revision: number;
};
export const validRevision = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const key = (owner: string, session: string) => `athena-submissions:${JSON.stringify([owner, session])}`;
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value));

/** An intent is immutable after dispatch. Reload recovery only looks up receipts;
 * it must never turn an uncertain delivery into a new instruction. */
export class SubmissionIntents {
  private storage: StorageLike | null;
  private memory = new Map<string, SubmissionIntent[]>();
  private floors = new Map<string, number>();
  private uncertain = new Set<string>();
  private stopping = new Map<string, number>();
  private lostStop = new Set<string>();
  private epochs = new Map<string, number>();
  private stops = new Map<string, StopIntent[]>();
  constructor(storage: StorageLike | null = null) { this.storage = storage; }
  list(owner: string, session: string): SubmissionIntent[] {
    const scope = key(owner, session);
    if (!this.memory.has(scope)) {
      let records: SubmissionIntent[] = [];
      try {
        const raw = JSON.parse(this.storage?.getItem(scope) || '[]');
        if (Array.isArray(raw)) records = raw.filter(value => value && value.owner === owner && value.session === session
          && typeof value.id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.id) && typeof value.text === 'string' && value.text.length <= 30000
          && ['queue', 'interrupt'].includes(value.mode) && validRevision(value.revision)
          && Array.isArray(value.file_ids) && value.file_ids.length <= 10 && value.file_ids.every((id: unknown) => typeof id === 'string')
          && (value.draft === null || (value.draft && typeof value.draft.text === 'string' && validRevision(value.draft.revision)
            && Array.isArray(value.draft.attached) && value.draft.attached.every((file: { id?: unknown }) => file && typeof file.id === 'string')))).slice(0, 64);
      } catch { /* Storage is optional; the mounted client retains its intents. */ }
      this.memory.set(scope, records);
    }
    return copy(this.memory.get(scope)!);
  }
  private save(owner: string, session: string, records: SubmissionIntent[]) {
    const scope = key(owner, session); this.memory.set(scope, copy(records));
    try { if (records.length) this.storage?.setItem(scope, JSON.stringify(records)); else this.storage?.removeItem(scope); }
    catch { /* No reload guarantee when browser storage is unavailable. */ }
  }
  add(intent: SubmissionIntent) {
    const records = this.list(intent.owner, intent.session);
    if (records.some(item => item.id === intent.id)) throw new Error('submission_conflict');
    if (records.length >= 64) throw new Error('submission_pending_limit');
    this.save(intent.owner, intent.session, [...records, intent]);
  }
  remove(intent: SubmissionIntent) {
    this.save(intent.owner, intent.session, this.list(intent.owner, intent.session).filter(item => item.id !== intent.id));
  }
  preserveDrafts(owner: string, session: string) {
    this.save(owner, session, this.list(owner, session).map(intent => ({ ...intent, preserveDraft: true })));
  }
  forDraft(owner: string, session: string, draft: Draft): SubmissionIntent | undefined {
    return this.list(owner, session).find(item => item.draft && item.draft.revision === draft.revision
      && item.draft.text === draft.text && JSON.stringify(item.draft.attached.map(file => file.id)) === JSON.stringify(draft.attached.map(file => file.id)));
  }
  observe(owner: string, session: string, revision: unknown) {
    if (!validRevision(revision)) return;
    const scope = key(owner, session);
    this.floors.set(scope, Math.max(this.floors.get(scope) ?? 0, revision));
  }
  revision(owner: string, session: string): number | undefined {
    const scope = key(owner, session);
    return this.uncertain.has(scope) || this.stopping.has(scope) || this.pendingStops(owner, session).length ? undefined : this.floors.get(scope);
  }
  pendingStops(owner: string, session: string): StopIntent[] {
    const scope = `stops:${key(owner, session)}`;
    if (!this.stops.has(scope)) {
      let records: StopIntent[] = [];
      try {
        const value = JSON.parse(this.storage?.getItem(scope) || '[]');
        if (Array.isArray(value)) records = value.filter(item => item && item.owner === owner && item.session === session && typeof item.id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(item.id)).slice(0, 64);
      } catch { /* In-page recovery remains available. */ }
      this.stops.set(scope, records);
    }
    return copy(this.stops.get(scope)!);
  }
  private saveStops(owner: string, session: string, records: StopIntent[]) {
    const scope = `stops:${key(owner, session)}`; this.stops.set(scope, copy(records));
    try { if (records.length) this.storage?.setItem(scope, JSON.stringify(records)); else this.storage?.removeItem(scope); }
    catch { /* Do not claim persistence if the browser disallows it. */ }
  }
  addStop(intent: StopIntent) {
    const records = this.pendingStops(intent.owner, intent.session);
    if (records.some(item => item.id === intent.id)) return;
    if (records.length >= 64) throw new Error('submission_pending_limit');
    this.saveStops(intent.owner, intent.session, [...records, intent]);
    this.invalidate(intent.owner, intent.session);
  }
  confirmStop(intent: StopIntent, revision: number) {
    this.observe(intent.owner, intent.session, revision);
    this.saveStops(intent.owner, intent.session, this.pendingStops(intent.owner, intent.session).filter(item => item.id !== intent.id));
  }
  needsStopReceipt(owner: string, session: string) { return this.stopping.has(key(owner, session)); }
  stopStarted(owner: string, session: string) {
    const scope = key(owner, session); this.invalidate(owner, session);
    if (!this.stopping.has(scope)) this.lostStop.delete(scope);
    this.stopping.set(scope, (this.stopping.get(scope) || 0) + 1);
  }
  stopFinished(owner: string, session: string, revision?: unknown) {
    const scope = key(owner, session), remaining = (this.stopping.get(scope) || 1) - 1;
    if (remaining) this.stopping.set(scope, remaining); else this.stopping.delete(scope);
    if (!validRevision(revision)) this.lostStop.add(scope);
    this.observe(owner, session, revision);
    // Even successful overlapping Stops may contain one lost response. A fresh
    // read after all settle is cheap and does not mistake an old SSE for proof.
    if (!remaining && !this.lostStop.has(scope) && validRevision(revision)) this.uncertain.delete(scope);
  }
  invalidate(owner: string, session: string) {
    const scope = key(owner, session); this.uncertain.add(scope); this.epochs.set(scope, this.epoch(owner, session) + 1);
  }
  epoch(owner: string, session: string) { return this.epochs.get(key(owner, session)) || 0; }
  refreshed(owner: string, session: string, revision: unknown, epoch: number) {
    if (!validRevision(revision) || this.needsStopReceipt(owner, session) || this.pendingStops(owner, session).length || epoch !== this.epoch(owner, session)) throw new Error('invalid_response');
    this.observe(owner, session, revision); this.uncertain.delete(key(owner, session));
    return this.revision(owner, session)!;
  }
}

export function readStopReceipt(value: unknown, id: string, lookup = false): number | null {
  const receipt = value as { found?: boolean; ok?: boolean; client_stop_id?: string; submission_revision?: number };
  if (!receipt || receipt.client_stop_id !== id) throw new Error('invalid_response');
  if (lookup && receipt.found === false) return null;
  if ((lookup ? receipt.found !== true : receipt.ok !== true) || !validRevision(receipt.submission_revision)) throw new Error('invalid_response');
  return receipt.submission_revision;
}

export function submissionBody(intent: SubmissionIntent, frontendClientId: string) {
  return { text: intent.text, mode: intent.mode, file_ids: [...intent.file_ids], frontend_client_id: frontendClientId,
    client_submission_id: intent.id, expected_submission_revision: intent.revision };
}
export function readSubmissionReceipt(value: unknown, id: string): SubmissionReceipt {
  const receipt = value as SubmissionReceipt;
  if (receipt?.accepted !== true || receipt.client_submission_id !== id || typeof receipt.run_id !== 'string' || !receipt.run_id
    || typeof receipt.status !== 'string' || !validRevision(receipt.submission_revision) || typeof receipt.replayed !== 'boolean') throw new Error('invalid_response');
  return receipt;
}
export function readSubmissionLookup(value: unknown, id: string): SubmissionLookup {
  const result = value as SubmissionLookup;
  if (!result || result.client_submission_id !== id || !validRevision(result.submission_revision)) throw new Error('invalid_response');
  if (result.found === false) return result;
  if (result.found !== true) throw new Error('invalid_response');
  return { ...readSubmissionReceipt(result, id), found: true };
}
/** Only an explicit application rejection proves this attempt wasn't admitted.
 * Timeouts, invalid JSON, a proxy's 5xx and missing receipts remain uncertain. */
export function submissionRejected(error: unknown): boolean {
  const failure = error as { status?: number; code?: string };
  return typeof failure?.status === 'number' && failure.status >= 400 && failure.status < 500 && typeof failure.code === 'string';
}
