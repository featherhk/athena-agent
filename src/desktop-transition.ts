/** Freeze new user mutations across the final persistence ACK and native/auth
 * transition. A timeout from an older transition cannot thaw a newer one. */
export class DesktopTransitionFence {
  private serial = 0;
  private active?: { id: number; kind: 'quit' | 'logout' };
  get blocked() { return Boolean(this.active); }
  begin(kind: 'quit' | 'logout'): number {
    if (this.active) throw new Error('desktop_transition_pending');
    this.active = { id: ++this.serial, kind }; return this.active.id;
  }
  owns(id: number) { return this.active?.id === id; }
  release(id: number) {
    if (!this.owns(id)) return false;
    this.active = undefined; return true;
  }
  cancelQuit() {
    if (this.active?.kind !== 'quit') return false;
    this.active = undefined; return true;
  }
}
