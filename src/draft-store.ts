import type { FileEntry } from './FilePreview';

export type Draft = { text: string; attached: FileEntry[]; revision: number };
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const blank = (): Draft => ({ text: '', attached: [], revision: 0 });
const key = (owner: string | null, session: string | null) => `athena-draft:${JSON.stringify([owner, session])}`;

/** Tab-local drafts are always scoped to both account and conversation. */
export class DraftStore {
  private memory = new Map<string, Draft>();
  private storage: StorageLike | null;
  constructor(storage: StorageLike | null = null) { this.storage = storage; }
  read(owner: string | null, session: string | null): Draft {
    const id = key(owner, session), cached = this.memory.get(id);
    if (cached) return { ...cached, attached: [...cached.attached] };
    try {
      const value = JSON.parse(this.storage?.getItem(id) || 'null');
      if (value && typeof value.text === 'string' && Array.isArray(value.attached)) {
        const draft = { text: value.text.slice(0, 30000), revision: Number.isSafeInteger(value.revision) ? value.revision : 0,
          attached: value.attached.filter((file: FileEntry) => file && typeof file.id === 'string' && typeof file.name === 'string' && typeof file.content_url === 'string').slice(0, 10) };
        this.memory.set(id, draft); return { ...draft, attached: [...draft.attached] };
      }
    } catch { /* Private storage may be unavailable; memory still preserves navigation. */ }
    return blank();
  }
  write(owner: string | null, session: string | null, value: Pick<Draft, 'text' | 'attached'>): Draft {
    const draft = { text: value.text, attached: [...value.attached], revision: this.read(owner, session).revision + 1 };
    const id = key(owner, session); this.memory.set(id, draft);
    try { this.storage?.setItem(id, JSON.stringify(draft)); } catch { /* Keep the in-memory draft. */ }
    return { ...draft, attached: [...draft.attached] };
  }
  persist(owner: string | null, session: string | null): boolean {
    if (!this.storage) return false;
    try { this.storage.setItem(key(owner, session), JSON.stringify(this.read(owner, session))); return true; }
    catch { return false; }
  }
  move(owner: string | null, from: string | null, to: string | null): Draft {
    if (from === to) return this.read(owner, to);
    const draft = this.read(owner, from);
    this.write(owner, to, draft); this.clear(owner, from);
    return this.read(owner, to);
  }
  clear(owner: string | null, session: string | null) {
    this.memory.delete(key(owner, session));
    try { this.storage?.removeItem(key(owner, session)); } catch { /* Memory still clears. */ }
  }
  submitted(owner: string, session: string, sent: Draft): Draft {
    const current = this.read(owner, session), fileIds = new Set(sent.attached.map(file => file.id));
    return this.write(owner, session, {
      text: current.revision === sent.revision ? '' : current.text,
      attached: current.attached.filter(file => !fileIds.has(file.id)),
    });
  }
}
