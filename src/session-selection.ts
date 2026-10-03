type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

const key = (owner: string) => `athena-session:${owner}`;
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(value);

/** The shared selection seeds a new tab; an existing tab always keeps its own place. */
export class SessionSelection {
  private selections = new Map<string, string | null>();
  private tab: StorageLike | null;
  private shared: StorageLike | null;

  constructor(tab: StorageLike | null = null, shared: StorageLike | null = null) {
    this.tab = tab; this.shared = shared;
  }

  read(owner: string | null): string | null {
    if (!identifier(owner)) return null;
    if (this.selections.has(owner)) return this.selections.get(owner)!;
    let stored: string | null = null;
    try { stored = this.tab?.getItem(key(owner)) ?? null; } catch { /* Fall back once, then retain this tab's in-memory selection. */ }
    if (stored === null) {
      try { stored = this.shared?.getItem(key(owner)) ?? null; } catch { /* Start a new conversation when storage is unavailable. */ }
    }
    const selected = identifier(stored) ? stored : null;
    this.selections.set(owner, selected);
    // Empty is an explicit new conversation, distinct from a tab never initialized.
    // Malformed tab values also reset locally rather than reviving a shared selection.
    try { this.tab?.setItem(key(owner), selected ?? ''); } catch { /* Navigation remains available in memory. */ }
    return selected;
  }

  write(owner: string | null, session: string | null): string | null {
    if (!identifier(owner)) return null;
    const selected = identifier(session) ? session : null;
    this.selections.set(owner, selected);
    try { this.tab?.setItem(key(owner), selected ?? ''); } catch { /* Keep the current tab usable without persistent storage. */ }
    try {
      if (selected) this.shared?.setItem(key(owner), selected);
      else this.shared?.removeItem(key(owner));
    } catch { /* A shared-storage failure must not discard the tab-local selection. */ }
    return selected;
  }
}
