export type HelpDraft = { answers: Record<string, string>; revision: number };
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type Scope = { owner: string; session: string; request: string };
const key = ({ owner, session, request }: Scope) => `athena-help-draft:${JSON.stringify([owner, session, request])}`;
const blank = (): HelpDraft => ({ answers: {}, revision: 0 });
const copy = (draft: HelpDraft): HelpDraft => ({ ...draft, answers: { ...draft.answers } });

/** A clarification is a draft too, scoped to its immutable request in this tab. */
export class HelpDraftStore {
  private memory = new Map<string, HelpDraft>();
  private storage: StorageLike | null;
  constructor(storage: StorageLike | null = null) { this.storage = storage; }
  read(scope: Scope): HelpDraft {
    const id = key(scope), cached = this.memory.get(id);
    if (cached) return copy(cached);
    try {
      const value = JSON.parse(this.storage?.getItem(id) || 'null');
      if (value && value.answers && typeof value.answers === 'object' && !Array.isArray(value.answers)
        && Number.isSafeInteger(value.revision) && value.revision >= 0) {
        const answers = Object.fromEntries(Object.entries(value.answers)
          .filter(([id, text]) => /^[a-zA-Z0-9_-]{1,64}$/.test(id) && typeof text === 'string')
          .slice(0, 3).map(([id, text]) => [id, (text as string).slice(0, 12000)]));
        const draft = { answers, revision: value.revision };
        this.memory.set(id, draft); return copy(draft);
      }
    } catch { /* Storage restrictions never prevent answering Athena. */ }
    return blank();
  }
  write(scope: Scope, answers: Record<string, string>): HelpDraft {
    const draft = { answers: { ...answers }, revision: this.read(scope).revision + 1 };
    const id = key(scope); this.memory.set(id, draft);
    try { this.storage?.setItem(id, JSON.stringify(draft)); } catch { /* Preserve navigation in memory. */ }
    return copy(draft);
  }
  acknowledge(scope: Scope, sent: HelpDraft, receipt: unknown): boolean {
    if (!receipt || typeof receipt !== 'object' || !('id' in receipt) || receipt.id !== scope.request
      || !('status' in receipt) || receipt.status !== 'answered') return false;
    // A response for a previous edit must not erase words typed after switching back.
    if (this.read(scope).revision === sent.revision) {
      // Keep the revision monotonic so a duplicated late receipt cannot clear a newer draft.
      const cleared = { answers: {}, revision: sent.revision + 1 };
      this.memory.set(key(scope), cleared);
      try { this.storage?.setItem(key(scope), JSON.stringify(cleared)); } catch { /* Memory stays correct. */ }
    }
    return true;
  }
}
