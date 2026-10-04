import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useI18n, errorMessage } from './i18n';
import { MarkdownContent } from './MessageBody';
import { taskOrigin, agentOrigin } from './message-origin';
import type { MessageOrigin } from './message-origin';
import { appendTracePage, charCount, traceContent, toolResult, toolRequests, type TracePage } from './trace-format';
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
type Page = TracePage;

function TraceField({ base, entry, field, plain = false }: { base: string; entry: Entry; field: 'content' | 'reasoning' | 'tool_calls'; plain?: boolean }) {
  const { t } = useI18n();
  const [loaded, setLoaded] = useState({ scope: '', text: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const scope = `${base}/${entry.id}/${field}`;
  const pending = useRef<symbol | null>(null);
  const latest = useRef({ scope, preview: entry[field] });
  latest.current = { scope, preview: entry[field] };
  const content = traceContent(entry[field], loaded.scope === scope ? loaded.text : '');
  const total = entry[`${field}_chars`];
  useEffect(() => {
    pending.current = null; setLoading(false); setError('');
    return () => { pending.current = null; };
  }, [scope]);
  async function more() {
    if (pending.current) return;
    const ticket = Symbol(); pending.current = ticket;
    setLoading(true); setError('');
    try {
      const page = await api<Page>(`${base}/${entry.id}?field=${field}&offset=${charCount(content)}&limit=12000`);
      if (pending.current !== ticket || latest.current.scope !== scope) return;
      const merged = appendTracePage(traceContent(latest.current.preview, content), page);
      if (merged === undefined) setError(t('trace.contentChanged'));
      else setLoaded({ scope, text: merged });
    } catch (error) { if (pending.current === ticket) setError((error as Error).message); }
    finally { if (pending.current === ticket) { pending.current = null; setLoading(false); } }
  }
  return <div className="trace-field">
    {plain ? <pre>{content}</pre> : <MarkdownContent content={content} />}
    {charCount(content) < total && <button className="trace-link" disabled={loading} onClick={() => void more()}>{t(loading ? 'wait' : 'trace.more')}</button>}
    {error && <p className="trace-error" role="alert">{error}</p>}
  </div>;
}

function ToolOutput({ base, entry }: { base: string; entry: Entry }) {
  const { t } = useI18n();
  const result = toolResult(entry.content, entry.status);
  const label = result.outcome === 'failed' ? 'trace.operationFailed' : result.outcome === 'uncertain' ? 'trace.outcomeUnknown'
    : result.outcome === 'cancelled' ? 'cancelled' : 'trace.resultReceived';
  return <div className="trace-result" data-outcome={result.outcome}>
    <p className="trace-result-summary"><span>{t(label)}</span>{result.exitCode !== undefined && <span>{t('trace.exitCode', { code: result.exitCode })}</span>}</p>
    {result.message && <p className="trace-result-message" dir="auto">{result.message}</p>}
    {result.output && <pre className="trace-result-preview" dir="auto">{result.output}</pre>}
    <details className="trace-original"><summary>{t('trace.details')}</summary><TraceField base={base} entry={entry} field="content" plain /></details>
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
  const state = status || trace?.run.status;
  // A completed run's trace is immutable. Other messages still advance the
  // session cursor; they must not refetch every expanded historical activity.
  const revisionToObserve = ['complete', 'failed', 'cancelled', 'interrupted'].includes(state || '') ? 0 : revision;
  const live = (phase === 'thinking' || phase === 'executing' || phase === 'responding')
    && !['complete', 'failed', 'cancelled', 'interrupted', 'waiting_user', 'waiting_agents'].includes(state || '');
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

  // The final status transition still flushes the final reasoning/tool results.
  // Explicit pagination and online recovery remain available for settled runs.
  useEffect(() => { if (open) refresh.current?.(); }, [revisionToObserve, phase, status, open]);

  return <details className={`run-trace${origin || label ? ' task-trace' : ''}`} open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><span className={`trace-indicator${live ? ' active' : ''}`} aria-hidden="true" />
      {origin?.kind === 'task' ? <><span className="task-trace-kind">{t(origin.trigger === 'time' ? 'trace.scheduled' : 'trace.condition')}</span><span className="task-trace-title" dir="auto">{origin.title}</span></>
        : label || origin?.kind === 'agent_task' ? <span className="task-trace-title" dir="auto">{label || (origin?.kind === 'agent_task' ? origin.title : '')}</span> : t('trace.title')}
      {live && <span className="trace-live">{t('trace.live')}</span>}
      {!live && stateLabel && (origin || label || ['failed', 'cancelled', 'interrupted'].includes(state || '')) && <span className="trace-state">{t(stateLabel)}</span>}
    </summary>
    {open && <div className="trace-timeline">
      {origin?.kind === 'task' && <p className="trace-source">{t('trace.occurrence', { number: origin.occurrence })}</p>}
      {trace?.entries.map((entry, index) => <article className={`trace-entry trace-${entry.role}`} key={entry.id}>
        <header><span>{t('trace.step', { number: index + 1 })}</span><span>{t(taskOrigin(entry) ? (taskOrigin(entry)!.trigger === 'time' ? 'trace.scheduled' : 'trace.condition') : agentOrigin(entry) ? 'agents.reference' : entry.role === 'user' ? 'trace.user' : entry.role === 'tool' ? 'trace.output' : entry.reasoning ? 'trace.thinking' : 'trace.reply')}</span>{entry.status !== 'complete' && <span>{t(entry.status === 'streaming' ? 'trace.live' : entry.status === 'queued' ? 'queued' : entry.status === 'failed' ? 'trace.failed' : entry.status === 'cancelled' ? 'cancelled' : 'stopped')}</span>}</header>
        {!!entry.reasoning && <section><h4>{t('trace.thinking')}</h4><TraceField base={base} entry={entry} field="reasoning" /></section>}
        {!!entry.content && <section>{entry.role === 'assistant' && !!entry.reasoning && <h4>{t('trace.reply')}</h4>}{entry.role === 'tool' ? <ToolOutput base={base} entry={entry} /> : <TraceField base={base} entry={entry} field="content" />}</section>}
        {!!entry.tool_calls && <section><details className="trace-original trace-requests"><summary>{t('trace.tools')}{toolRequests(entry.tool_calls).map((name, index) => <span className="trace-tool-name" key={index} dir="auto">{name}</span>)}</summary><TraceField base={base} entry={entry} field="tool_calls" plain /></details></section>}
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
