import type { FrontendObservation } from './frontend-observation';

type RenderedSource = { clientId: string; sessionId: string | null };
export class SubmissionSourceError extends Error {
  readonly code: 'frontend_unavailable' | 'frontend_conflict';
  constructor(code: 'frontend_unavailable' | 'frontend_conflict') { super(code); this.code = code; }
}

/** Bind a message to a committed, acknowledged browser surface. A failed
 * observation must not silently send an unbound message to another window. */
export async function prepareMessageSource(options: {
  clientId: string; sessionId: string;
  rendered: () => RenderedSource | null;
  current: () => boolean;
  report: () => Promise<FrontendObservation | null>;
  nextFrame?: () => Promise<void>;
  timeoutMs?: number;
}): Promise<string> {
  let ended = false, timer: ReturnType<typeof setTimeout> | undefined;
  const current = () => !ended && options.current();
  const matches = () => {
    const source = options.rendered();
    return source?.clientId === options.clientId && source.sessionId === options.sessionId;
  };
  const nextFrame = options.nextFrame || (() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  const prepare = async () => {
    while (!matches()) {
      if (!current()) throw new SubmissionSourceError('frontend_conflict');
      await nextFrame();
    }
    if (!current()) throw new SubmissionSourceError('frontend_conflict');
    let observation: FrontendObservation | null;
    try { observation = await options.report(); }
    catch { throw new SubmissionSourceError('frontend_unavailable'); }
    if (!current() || !matches()) throw new SubmissionSourceError('frontend_conflict');
    if (!observation || observation.client_id !== options.clientId || observation.session_id !== options.sessionId || !observation.foreground)
      throw new SubmissionSourceError('frontend_unavailable');
    return observation.client_id;
  };
  try {
    return await Promise.race([prepare(), new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new SubmissionSourceError('frontend_unavailable')), options.timeoutMs ?? 5000);
    })]);
  } finally { ended = true; if (timer !== undefined) clearTimeout(timer); }
}
