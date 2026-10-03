const aborted = () => new DOMException('Frontend request was aborted.', 'AbortError');

/** One deadline covers the request, response body and any identity lookup.
 * Abort the transport itself so the reporter can drain after a stalled request. */
export async function frontendRequest<T>(parent: AbortSignal, work: (signal: AbortSignal) => Promise<T>, timeoutMs = 5000): Promise<T> {
  const controller = new AbortController();
  let cancel!: (error: Error) => void;
  const cancelled = new Promise<never>((_resolve, reject) => { cancel = reject; });
  const abort = () => { controller.abort(); cancel(aborted()); };
  parent.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, timeoutMs);
  if (parent.aborted) abort();
  try {
    return await Promise.race([Promise.resolve().then(() => {
      if (controller.signal.aborted) throw aborted();
      return work(controller.signal);
    }), cancelled]);
  } finally {
    clearTimeout(timer);
    parent.removeEventListener('abort', abort);
  }
}
