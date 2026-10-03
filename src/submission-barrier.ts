export type SubmissionTicket = { readonly id: number };

/** A local submit owns the surface until its final draft changes have committed. */
export class SubmissionBarrier {
  private serial = 0;
  private revision = 0;
  private active: { ticket: SubmissionTicket; scope: string; finishRevision: number | null } | null = null;

  begin(scope: string): SubmissionTicket | null {
    if (this.active?.scope === scope) return null;
    const ticket = { id: ++this.serial };
    this.active = { ticket, scope, finishRevision: null };
    this.revision++;
    return ticket;
  }

  move(ticket: SubmissionTicket, scope: string) {
    if (this.active?.ticket !== ticket) return;
    this.active.scope = scope;
    this.revision++;
  }

  finish(ticket: SubmissionTicket): number | null {
    if (this.active?.ticket !== ticket) return null;
    this.active.finishRevision = ++this.revision;
    return this.revision;
  }

  commit(revision: number) {
    if (this.active?.finishRevision == null || this.active.finishRevision > revision) return;
    this.active = null;
    this.revision++;
  }

  stamp() { return this.revision; }
  ready(scope: string, revision = this.revision) { return this.active?.scope !== scope && revision === this.revision; }
}

/** A timeout leaves the action unclaimed. Nothing here executes or retries it. */
export async function prepareAppClaim(options: {
  barrier: SubmissionBarrier; scope: string; current: () => boolean;
  report: () => Promise<unknown>; timeoutMs?: number;
}): Promise<boolean> {
  let ended = false, deadline: ReturnType<typeof setTimeout> | undefined;
  const current = () => !ended && options.current();
  const prepare = async () => {
    while (current()) {
      if (!options.barrier.ready(options.scope)) {
        await new Promise(resolve => setTimeout(resolve, 25));
        continue;
      }
      const revision = options.barrier.stamp();
      const observation = await options.report();
      if (!current() || !observation) return false;
      if (options.barrier.ready(options.scope, revision)) return true;
    }
    return false;
  };
  try {
    return await Promise.race([prepare(), new Promise<boolean>(resolve => {
      deadline = setTimeout(() => resolve(false), options.timeoutMs ?? 5000);
    })]);
  } catch { return false; }
  finally { ended = true; if (deadline !== undefined) clearTimeout(deadline); }
}
