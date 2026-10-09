import { interactionVisible } from './desktop-bridge';
import { useEffect, useId, useRef, useState } from 'react';
import { useI18n } from './i18n';
import { ActivityFeed, activeActivity, activityGroups, readableActivity, requestActivity } from './task-continuity';
import type { ActivityItem, ActivityState } from './task-continuity';
import './task-continuity.css';

const emptyState: ActivityState = { owner_id: '', items: [], has_more: false, error: false, limit: 30 };
export function useTaskContinuity(owner: string | null, identityLost: () => void) {
  const [state, setState] = useState(emptyState);
  const feed = useRef<ActivityFeed | null>(null), lost = useRef(identityLost); lost.current = identityLost;
  useEffect(() => {
    if (!owner) { feed.current = null; setState(emptyState); return; }
    const source = new ActivityFeed(owner, (path, signal, body) => requestActivity(owner, path, signal,
      () => { source.stop(); lost.current(); }, body), setState, () => lost.current());
    feed.current = source; setState(source.state);
    let departing = false;
    const visible = () => { if (!interactionVisible() || departing) source.pause(); else source.resume(); };
    const leave = () => { departing = true; source.pause(); };
    const enter = () => { departing = false; visible(); };
    document.addEventListener('visibilitychange', visible); window.addEventListener('online', visible);
    window.addEventListener('pagehide', leave); window.addEventListener('pageshow', enter); visible();
    return () => {
      source.stop(); if (feed.current === source) feed.current = null;
      document.removeEventListener('visibilitychange', visible); window.removeEventListener('online', visible);
      window.removeEventListener('pagehide', leave); window.removeEventListener('pageshow', enter);
    };
  }, [owner]);
  return { state: state.owner_id === owner ? state : emptyState, source: feed };
}
export type Continuity = ReturnType<typeof useTaskContinuity>;

/** A received snapshot is not a read receipt: require the matching result on screen. */
export function useVisibleActivity(continuity: Continuity, session: string | null, runs: { id: string; status: string }[], blocked: boolean, cursor: number) {
  const { state, source } = continuity;
  useEffect(() => {
    if (blocked || !interactionVisible() || !session) return;
    const eligible = state.items.filter(item => readableActivity(item, session, runs, true));
    if (!eligible.length) return;
    const timers = new Map<Element, ReturnType<typeof setTimeout>>();
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const timer = timers.get(entry.target);
        if (!entry.isIntersecting || entry.intersectionRect.height < Math.min(24, entry.boundingClientRect.height)) {
          if (timer) clearTimeout(timer); timers.delete(entry.target); continue;
        }
        if (timer) continue;
        const item = eligible.find(item => item.run_id === (entry.target as HTMLElement).dataset.activityResult);
        if (item) timers.set(entry.target, setTimeout(() => { if (interactionVisible()) void source.current?.acknowledge(item); }, 700));
      }
    }, { root: document.querySelector('.scroll-area'), threshold: [0, 0.1, 0.5, 1] });
    for (const element of document.querySelectorAll('[data-activity-result]')) observer.observe(element);
    return () => { observer.disconnect(); for (const timer of timers.values()) clearTimeout(timer); };
  }, [state, session, runs, blocked, cursor, source]);
}

export default function TaskContinuity({ continuity, currentSession, openConversation, openHistory, open, setOpen, includeCurrentTerminal = false, embedded = false }: {
  continuity: Continuity; currentSession: string | null; openConversation: (item: ActivityItem) => Promise<boolean>; openHistory: () => void;
  open: boolean; setOpen: (open: boolean) => void; includeCurrentTerminal?: boolean; embedded?: boolean;
}) {
  const { t } = useI18n(), id = useId();
  const [working, setWorking] = useState(''), [failed, setFailed] = useState(false);
  const root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const groups = activityGroups(continuity.state.items, open ? null : currentSession, includeCurrentTerminal);
  const first = groups[0]?.[0], problem = continuity.state.error || failed;
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside); root.current?.addEventListener('keydown', escape);
    const element = root.current;
    return () => { document.removeEventListener('pointerdown', outside); element?.removeEventListener('keydown', escape); };
  }, [open]);
  const status = (item: ActivityItem) => item.needs_user ? 'continuity.needsYou' : item.status === 'complete' ? 'continuity.ready'
    : item.status === 'failed' ? 'continuity.failed' : ['cancelled', 'interrupted'].includes(item.status) ? 'continuity.stopped' : 'continuity.working';
  async function view(item: ActivityItem) {
    setWorking(item.session_id); setFailed(false);
    try { if (await openConversation(item)) setOpen(false); else setFailed(true); }
    catch { setFailed(true); }
    finally { setWorking(''); }
  }
  if (!first && !problem && !open && !continuity.state.has_more) return null;
  return <div className={`task-continuity${embedded ? ' embedded' : ''}`} ref={root}>
    <button className="continuity-trigger" ref={trigger} type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <span className="continuity-dot" data-state={first ? status(first) : 'error'} aria-hidden="true" />
      <span>{first ? groups.length === 1 ? t(status(first)) : t('continuity.count', { count: groups.length }) : t(problem ? 'continuity.unavailable' : 'continuity.title')}</span>
    </button>
    {open && <section className="continuity-panel" data-app-view="activity" id={id} aria-label={t('continuity.title')}>
      <div className="continuity-heading"><h2>{t('continuity.title')}</h2><button className="icon-button" type="button" aria-label={t('close')} onClick={() => { setOpen(false); trigger.current?.focus(); }}>×</button></div>
      {problem && <p className="continuity-error" role="status">{t('continuity.unavailable')}<button type="button" onClick={() => { setFailed(false); continuity.source.current?.refresh(); }}>{t('retry')}</button></p>}
      <div className="continuity-list">{groups.map(group => {
        const item = group[0], active = group.some(activeActivity), terminal = group.filter(item => !activeActivity(item));
        return <div className="continuity-item" key={item.session_id}>
          <button className="continuity-view" type="button" disabled={working === item.session_id} onClick={() => void view(item)}>
            <span className="continuity-title" dir="auto">{item.title === '新的事情' ? t('newChat') : item.title}</span>
            <span className="continuity-detail">{t(status(item))}{group.length > 1 && <span>{t('continuity.tasks', { count: group.length })}</span>}</span>
          </button>
          <div className="continuity-actions">{active && <button type="button" disabled={Boolean(working)} onClick={async () => {
            setWorking(item.session_id); setFailed(false);
            const stopped = await continuity.source.current?.stopSession(item.session_id);
            if (!stopped) setFailed(true); setWorking('');
          }}>{t('continuity.stopChat')}</button>}
          {terminal.length > 0 && <button type="button" disabled={Boolean(working)} onClick={async () => {
            setWorking(item.session_id); setFailed(false);
            await Promise.all(terminal.map(item => continuity.source.current?.acknowledge(item))); setWorking('');
          }}>{t('continuity.dismiss')}</button>}</div>
        </div>;
      })}</div>
      {continuity.state.has_more && <button className="continuity-more continuity-more-button" type="button" onClick={() => continuity.state.limit < 100 ? continuity.source.current?.loadMore() : openHistory()}>{t(continuity.state.limit < 100 ? 'continuity.more' : 'continuity.history')}</button>}
      {!first && !problem && !continuity.state.has_more && <p className="continuity-more">{t('continuity.clear')}</p>}
    </section>}
  </div>;
}
