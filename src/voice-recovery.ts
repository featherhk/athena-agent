export type VoiceDelivery = 'not_sent' | 'unknown';

export class VoiceSubmissionError extends Error {
  readonly delivery: VoiceDelivery;
  constructor(delivery: VoiceDelivery, cause?: unknown) {
    super(cause instanceof Error ? cause.message : 'voice_interrupted');
    this.delivery = delivery;
  }
}

/** A lost POST response is not evidence that the server rejected the message. */
export class VoiceSubmissionReceipt {
  private dispatched = false;
  private accepted = false;
  dispatch() { this.dispatched = true; }
  confirm() { this.accepted = true; }
  failure(error: unknown): VoiceSubmissionError | undefined {
    if (this.accepted) return undefined;
    return new VoiceSubmissionError(this.dispatched ? 'unknown' : 'not_sent', error);
  }
}

export type VoiceRecovery = { id: number; text: string; delivery: VoiceDelivery };
export type VoiceRecoveryScope = { owner: string; session: string; generation: number };

/** Failed speech stays in this mounted client's memory, separate from typed drafts. */
export class VoiceRecoveryStore {
  private owner: string | null = null;
  private generation = 0;
  private nextId = 0;
  private records = new Map<string, VoiceRecovery>();
  selectOwner(owner: string | null) {
    if (owner === this.owner) return;
    this.owner = owner; this.generation++; this.records.clear();
  }
  scope(owner: string, session: string): VoiceRecoveryScope | null {
    return owner === this.owner ? { owner, session, generation: this.generation } : null;
  }
  retain(scope: VoiceRecoveryScope | null, text: string, error: unknown): boolean {
    if (!scope || scope.owner !== this.owner || scope.generation !== this.generation || !text.trim()) return false;
    // Starting another call is disabled until this failure has been reviewed.
    // A late failure must still never overwrite the earlier retained words.
    if (this.records.has(scope.session)) return false;
    this.records.set(scope.session, { id: ++this.nextId, text,
      delivery: error instanceof VoiceSubmissionError ? error.delivery : 'unknown' });
    return true;
  }
  read(owner: string | null, session: string | null): VoiceRecovery | null {
    if (!owner || owner !== this.owner || !session) return null;
    const value = this.records.get(session);
    return value ? { ...value } : null;
  }
  dismiss(owner: string | null, session: string | null, id: number): boolean {
    if (!owner || owner !== this.owner || !session || this.records.get(session)?.id !== id) return false;
    return this.records.delete(session);
  }
}
