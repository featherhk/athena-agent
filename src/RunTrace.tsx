import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useI18n, errorMessage } from './i18n';
import { MarkdownContent } from './MessageBody';
import { taskOrigin, agentOrigin } from './message-origin';
import type { MessageOrigin } from './message-origin';
import './run-trace.css';

type Entry = {
  id: string; seq: number; role: string; status: string; created_at: string;
  content: string; content_chars: number; reasoning: string; reasoning_chars: number;
  tool_calls: string; tool_calls_chars: number; tool_call_id?: string; artifact_id?: string;
  origin?: MessageOrigin;
};
type Trace = {
  run: { status: string; phase?: string; error?: string; error_code?: string };
  entries: Entry[]; total: number; next_after: number; has_more: boolean;
};
type Page = { content: string; offset: number; total_chars: number };
const charCount = (value: string) => Array.from(value).length;

function TraceField({ base, entry, field, plain = false }: { base: string; entry: Entry; field: 'content' | 'reasoning' | 'tool_calls'; plain?: boolean }) {
  const { t } = useI18n();
  const [extra, setExtra] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Streaming previews are cumulative. If their prefix grows, discard a prior
  // extension rather than duplicating text at the old preview boundary.
  useEffect(() => { setExtra(''); }, [entry[field], base, field]);
  const content = entry[field] + extra;
  const total = entry[`${field}_chars`];
  async function more() {
    setLoading(true); setError('');
    try {
      const page = await api<Page>(`${base}/${entry.id}?field=${field}&offset=${charCount(content)}&limit=12000`);
      setExtra(old => old + page.content);
    } catch (error) { setError((error as Error).message); }
    finally { setLoading(false); }
  }
  return <div className="trace-field">
    {plain ? <pre>{content}</pre> : <MarkdownContent content={content} />}
    {charCount(content) < total && <button className="trace-link" disabled={loading} onClick={() => void more()}>{t(loading ? 'wait' : 'trace.more')}</button>}
    {error && <p className="trace-error" role="alert">{error}</p>}
  </div>;
}

function OriginalOutput({ base, entry }: { base: string; entry: Entry }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  async function load(offset = 0) {
    setLoading(true); setError('');
    try {
      const next = await api<Page>(`${base}/${entry.id}/artifact?offset=${offset}&limit=12000`);
      setPage(old => ({ ...next, offset: 0, content: (offset ? old?.content || '' : '') + next.content }));
    } catch (error) { setError((error as Error).message); }
    finally { setLoading(false); }
  }
  return <details className="trace-original" open={open} onToggle={event => {
    const expanded = event.currentTarget.open;
    setOpen(expanded);
    if (expanded && !page && !loading) void load();
  }}>
    <summary>{t('trace.original')}</summary>
    {open && <>
      {page && <pre>{page.content}</pre>}
      {(!page && loading) && <p>{t('wait')}</p>}
      {page && charCount(page.content) < page.total_chars && <button className="trace-link" disabled={loading} onClick={() => void load(charCount(page.content))}>{t(loading ? 'wait' : 'trace.more')}</button>}
      {error && <p className="trace-error" role="alert">{error} <button className="trace-link" onClick={() => void load(charCount(page?.content || ''))}>{t('retry')}</button></p>}
    </>}
  </details>;
}

export function RunTrace({ sessionId, runId, revision = 0, phase, status, origin, label }: { sessionId: string; runId: string; revision?: number; phase?: string; status?: string; origin?: MessageOrigin; label?: string }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const refresh = useRef<((next?: boolean) => void) | null>(null);
  const base = `/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/trace`;
  const live = phase === 'thinking' || phase === 'executing' || phase === 'responding';
  const state = status || trace?.run.status;
  const stateLabel = state === 'complete' ? 'trace.done' : state === 'failed' ? 'trace.failed'
    : state === 'queued' ? 'queued' : state === 'waiting_user' ? 'status.waiting' : state === 'waiting_agents' ? 'agents.waiting'
    : state === 'cancelled' ? 'cancelled' : state === 'interrupted' ? 'stopped' : undefined;

  useEffect(() => {
    if (!open) { refresh.current = null; return; }
    let alive = true, busy = false, dirty = false, failed = false;
    let current: Trace | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastFetched = 0;
    setTrace(null); setError('');
    async function load(next = false) {
      if (!alive) return;
      if (busy) { dirty = true; return; }
      busy = true; setLoading(true); lastFetched = Date.now();
      const previous = current;
      const after = next && previous ? previous.next_after
        : previous?.entries.length ? Math.max(0, previous.entries[Math.max(0, previous.entries.length - 50)].seq - 1) : 0;
      try {
        const page = await api<Trace>(`${base}?after=${after}&limit=50`);
        if (!alive) return;
        const retained = previous?.entries.filter(item => item.seq <= after) || [];
        current = { ...page, entries: [...retained, ...page.entries] };
        failed = false; setTrace(current); setError('');
      } catch (error) { if (alive) { failed = true; setError((error as Error).message); } }
      finally {
        busy = false;
        if (alive) {
          setLoading(false);
          if (dirty) { dirty = false; schedule(); }
        }
      }
    }
    function schedule(next = false) {
      if (!alive) return;
      if (busy) { dirty = true; return; }
      if (timer !== undefined) return;
      timer = setTimeout(() => { timer = undefined; void load(next); }, next ? 0 : Math.max(0, 600 - (Date.now() - lastFetched)));
    }
    // A local backend can restart while an expanded activity is on screen.
    // Recover when the user returns or connectivity resumes, without polling
    // failed requests or collapsing the record they were reading.
    function recover() { if (failed && !document.hidden) schedule(); }
    window.addEventListener('online', recover);
    document.addEventListener('visibilitychange', recover);
    refresh.current = schedule;
    void load();
    return () => {
      alive = false; if (timer !== undefined) clearTimeout(timer); refresh.current = null;
      window.removeEventListener('online', recover);
      document.removeEventListener('visibilitychange', recover);
    };
  }, [open, base]);

  useEffect(() => { if (open) refresh.current?.(); }, [revision, phase, open]);

  return <details className={`run-trace${origin || label ? ' task-trace' : ''}`} open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><span className={`trace-indicator${live ? ' active' : ''}`} aria-hidden="true" />
      {origin?.kind === 'task' ? <><span className="task-trace-kind">{t(origin.trigger === 'time' ? 'trace.scheduled' : 'trace.condition')}</span><span className="task-trace-title" dir="auto">{origin.title}</span></>
        : label || origin?.kind === 'agent_task' ? <span className="task-trace-title" dir="auto">{label || (origin?.kind === 'agent_task' ? origin.title : '')}</span> : t('trace.title')}
      {live && <span className="trace-live">{t('trace.live')}</span>}
      {(origin || label) && !live && stateLabel && <span className="trace-state">{t(stateLabel)}</span>}
    </summary>
    {open && <div className="trace-timeline">
      {origin?.kind === 'task' && <p className="trace-source">{t('trace.occurrence', { number: origin.occurrence })}</p>}
      {trace?.entries.map((entry, index) => <article className={`trace-entry trace-${entry.role}`} key={entry.id}>
        <header><span>{t('trace.step', { number: index + 1 })}</span><span>{t(taskOrigin(entry) ? (taskOrigin(entry)!.trigger === 'time' ? 'trace.scheduled' : 'trace.condition') : agentOrigin(entry) ? 'agents.reference' : entry.role === 'user' ? 'trace.user' : entry.role === 'tool' ? 'trace.output' : entry.reasoning ? 'trace.thinking' : 'trace.reply')}</span>{entry.status !== 'complete' && <span>{t(entry.status === 'streaming' ? 'trace.live' : entry.status === 'queued' ? 'queued' : entry.status === 'cancelled' ? 'cancelled' : 'stopped')}</span>}</header>
        {!!entry.reasoning && <section><h4>{t('trace.thinking')}</h4><TraceField base={base} entry={entry} field="reasoning" /></section>}
        {!!entry.content && <section>{entry.role === 'assistant' && !!entry.reasoning && <h4>{t('trace.reply')}</h4>}<TraceField base={base} entry={entry} field="content" plain={entry.role === 'tool'} /></section>}
        {!!entry.tool_calls && <section><h4>{t('trace.tools')}</h4><TraceField base={base} entry={entry} field="tool_calls" plain /></section>}
        {entry.artifact_id && <OriginalOutput base={base} entry={entry} />}
      </article>)}
      {trace?.run.error_code && <p className="trace-error" role="status">{trace.run.error || errorMessage(trace.run.error_code)} <code>{trace.run.error_code}</code></p>}
      {trace && !trace.entries.length && <p className="trace-empty">{t('trace.empty')}</p>}
      {loading && !trace && <p className="trace-empty">{t('wait')}</p>}
      {trace?.has_more && <button className="trace-link" disabled={loading} onClick={() => refresh.current?.(true)}>{t(loading ? 'wait' : 'trace.next')}</button>}
      {error && <p className="trace-error" role="alert">{error} <button className="trace-link" onClick={() => refresh.current?.()}>{t('retry')}</button></p>}
    </div>}
  </details>;
}

export default RunTrace;
