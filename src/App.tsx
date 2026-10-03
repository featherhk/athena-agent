import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { api, empty, defaultPreferences } from './api';
import type { User, Snapshot, Session, Preferences, AppAction, Component, ViewChange } from './api';
import Orb, { AthenaMark } from './Orb';
import type { Phase } from './Orb';
import Widget from './Widget';
import ComponentControls from './ComponentControls';
import { arrangePanels, floatingPlacements, placementOf } from './component-layout';
import { useSurfaceBounds } from './use-surface-bounds';
import type { FormDraft } from './component-forms';
import { MessageBody } from './MessageBody';
import { FilePreview } from './FilePreview';
import type { FileEntry } from './FilePreview';
import DeveloperPanel from './DeveloperPanel';
import { AccountProfile } from './AccountProfile';
import UserHelp, { HelpReceipt } from './UserHelp';
import VoiceCall from './VoiceCall';
import { RunTrace } from './RunTrace';
import { taskOrigin, agentOrigin } from './message-origin';
import { AgentActivity, AgentResults } from './AgentActivity';
import { StyledText } from './StyledText';
import type { TextStyleEffect } from './effects';
import { mergeSnapshot } from './snapshot-sync';
import { checkClientVersion } from './client-version';
import { consumeAppAction, scopeGuard, interruptedReceipt } from './app-actions';
import type { AppScope, AppReceipt } from './app-actions';
import { DraftStore } from './draft-store';
import type { Draft } from './draft-store';
import ErrorBoundary from './ErrorBoundary';
import { useI18n, languages, errorMessage } from './i18n';

function Icon({ name }: { name: 'history' | 'reset' | 'user' | 'send' | 'files' | 'stop' }) {
  const paths = {
    history: <><path d="M4 5v5h5M4.5 9a8 8 0 1 1-.2 6" /><path d="M12 7v5l3 2" /></>,
    reset: <><path d="M19 4v5h-5M19 9a7 7 0 1 0 .2 6" /><path d="m11 9-2 4h4l-2 3" /></>,
    user: <><circle cx="12" cy="8" r="3" /><path d="M5 21v-2a7 7 0 0 1 14 0v2" /></>,
    send: <path d="M12 19V5m-6 6 6-6 6 6" />,
    files: <path d="M3 7h7l2-3h8a1 1 0 0 1 1 1v14H3V7Zm0 0V5h7" />,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

export default function App() {
  const { t, locale, choice, reduced, preferences, applyPreferences } = useI18n();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [hasUsers, setHasUsers] = useState(true);
  const [session, setSession] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot>(empty);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [serviceMessage, setServiceMessage] = useState('');
  const [toast, setToast] = useState('');
  const [history, setHistory] = useState(false);
  const [account, setAccount] = useState(false);
  const [passwordRequested, setPasswordRequested] = useState(0);
  const [menu, setMenu] = useState(false);
  const [appearance, setAppearance] = useState(false);
  const [auth, setAuth] = useState<'login' | 'register' | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [sending, setSending] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [reconnecting, setReconnecting] = useState(false);
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [surfaces, setSurfaces] = useState(false);
  const [surfaceHeights, setSurfaceHeights] = useState<Record<string, number>>({});
  const surfaceDrafts = useRef(new Map<string, FormDraft>());
  const surfaceBounds = useSurfaceBounds();
  const [preview, setPreview] = useState<FileEntry | null>(null);
  const [attached, setAttached] = useState<FileEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [reading, setReading] = useState(false);
  const [textEffects, setTextEffects] = useState<Record<string, Record<string, TextStyleEffect>>>({});
  const [chooseFiles, setChooseFiles] = useState(false);
  const [actionWake, setActionWake] = useState(0);
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceRecovery, setVoiceRecovery] = useState(0);
  const [voiceHelpAnswers, setVoiceHelpAnswers] = useState<{ id: string; answers: Record<string, string> }>({ id: '', answers: {} });
  const appClient = useRef(crypto.randomUUID());
  const appActionActive = useRef(0);
  const authEpoch = useRef(0);
  const uiScope = useRef<AppScope>({ owner: null, session: null, generation: 0, control: 0 });
  const drafts = useRef<DraftStore | null>(null);
  if (!drafts.current) {
    let storage: Storage | null = null; try { storage = sessionStorage; } catch { /* Use memory. */ }
    drafts.current = new DraftStore(storage);
  }
  const appActionSeen = useRef(new Set<string>());
  const appActionQueue = useRef(Promise.resolve());
  const messagesRef = useRef(snapshot.messages); messagesRef.current = snapshot.messages;
  const fileInput = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const actionSeen = useRef(0);
  const dismissedAction = useRef(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const composer = useRef<HTMLElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const scrollIntent = useRef(0);
  const upgradeState = useRef({ user, session, text, attached, safe: false });
  upgradeState.current = { user, session, text, attached, safe: !voiceActive && !sending && !uploading && !resetting && !chooseFiles && !snapshot.help_requests?.length && !auth && !account && !appearance && !preview && !files && !history && !menu && !surfaces };
  const busy = snapshot.runs.some(run => ['running', 'queued', 'waiting_agents'].includes(run.status));
  const waitingAgents = snapshot.runs.some(run => run.status === 'waiting_agents');
  const help = snapshot.help_requests?.[0];
  const waiting = Boolean(help);
  const voiceAnswers = help && voiceHelpAnswers.id === help.id ? voiceHelpAnswers.answers : {};
  const voiceQuestion = help?.questions.find(question => !(question.id in voiceAnswers));
  const voiceMessages = voiceQuestion && help ? [...snapshot.messages, { id: `voice-help:${help.id}:${voiceQuestion.id}`, role: 'assistant', content: voiceQuestion.question, status: 'complete' }] : snapshot.messages;
  const queued = snapshot.runs.filter(run => run.status === 'queued').length;
  const lastError = snapshot.runs[0]?.error ? errorMessage(snapshot.runs[0]?.error_code) : '';
  const activePhase = snapshot.runs.find(run => run.status === 'running')?.phase;
  const orbPhase: Phase = waiting ? 'waiting' : busy ? (activePhase === 'responding' || activePhase === 'executing' ? activePhase : phase === 'responding' || phase === 'executing' ? phase : 'thinking') : snapshot.runs[0]?.status === 'interrupted' ? 'stopped' : 'idle';

  const components = snapshot.components.filter(component => !component.view?.hidden);
  const workspaces = snapshot.components.filter(component => ['workspace', 'fullscreen'].includes(placementOf(component))).sort((a, b) => (a.view_updated_at || a.created_at || '').localeCompare(b.view_updated_at || b.created_at || ''));
  const workspace = workspaces.filter(component => !component.view?.hidden).at(-1);
  const presence = components.filter(component => placementOf(component) === 'presence').at(-1);
  const floating = components.filter(component => floatingPlacements.has(placementOf(component)));
  const panelLayout = arrangePanels(floating, surfaceBounds, locale === 'ar', surfaceHeights);
  const measureWidget = useCallback((id: string, height: number) => setSurfaceHeights(old => old[id] === height ? old : { ...old, [id]: height }), []);
  const styleText = useCallback((key: string, effect: TextStyleEffect | null) => {
    if (effect && !messagesRef.current.some(message => message.id === effect.message_id)) return;
    setTextEffects(old => {
      if (!effect) { if (!(key in old)) return old; const next = { ...old }; delete next[key]; return next; }
      const owned = old[key] || {};
      if (Object.keys(owned).length >= 64 && !(effect.message_id in owned)) return old;
      if (JSON.stringify(owned[effect.message_id]) === JSON.stringify(effect)) return old;
      return { ...old, [key]: { ...owned, [effect.message_id]: effect } };
    });
  }, []);
  function effectFor(id: string) { return Object.values(textEffects).map(effects => effects[id]).filter(Boolean).at(-1); }
  function currentGuard() { return scopeGuard(() => uiScope.current); }
  function showDraft(draft: Draft) { setText(draft.text); setAttached(draft.attached); }
  function editDraft(change: Partial<Pick<Draft, 'text' | 'attached'>>) {
    const { owner, session: sid } = uiScope.current;
    showDraft(drafts.current!.write(owner, sid, { ...drafts.current!.read(owner, sid), ...change }));
  }
  function changeAttachments(change: (old: FileEntry[]) => FileEntry[]) {
    const scope = uiScope.current;
    editDraft({ attached: change(drafts.current!.read(scope.owner, scope.session).attached) });
  }
  function navigate(next: string | null, migrate = false) {
    const scope = uiScope.current;
    const draft = migrate ? drafts.current!.move(scope.owner, scope.session, next) : drafts.current!.read(scope.owner, next);
    uiScope.current = { ...scope, session: next, generation: scope.generation + 1 };
    generation.current++; setSession(next); showDraft(draft);
    setSnapshot(empty()); setPhase('idle'); setError(''); setReading(false); setTextEffects({}); setChooseFiles(false); setSurfaceHeights({}); setSurfaces(false);
    setSending(false); setUploading(false);
  }
  async function navigateFromWidget(next: string) {
    const isCurrent = currentGuard();
    try { await api<Snapshot>(`/sessions/${encodeURIComponent(next)}`); if (isCurrent()) navigate(next); }
    catch (e) { if (isCurrent()) setError((e as Error).message); }
  }
  async function changeComponentView(component: Component, view: ViewChange) {
    if (!session) return;
    const sid = session, isCurrent = currentGuard();
    try {
      const result = await api<Pick<Component, 'version' | 'view' | 'view_revision' | 'view_updated_at'>>(`/sessions/${sid}/components/${component.id}/view`, { version: component.version, view_revision: component.view_revision || 0, view });
      if (isCurrent()) setSnapshot(old => ({ ...old, components: old.components.map(item => item.id === component.id && item.version === result.version && (item.view_revision || 0) <= (result.view_revision || 0) ? { ...item, ...result } : item) }));
    } catch (e) {
      if (isCurrent()) {
        setError((e as Error).message);
        // A competing window or a new registration wins over our stale drag.
        try { const next = await api<Snapshot>(`/sessions/${sid}`); if (isCurrent()) setSnapshot(old => next.cursor >= old.cursor ? next : old); } catch { /* Preserve the last known view. */ }
      }
      throw e;
    }
  }
  function adjustComponent(component: Component, view: ViewChange) { void changeComponentView(component, view).catch(() => {}); }
  async function applyAppAction(action: AppAction, isCurrent: () => boolean): Promise<AppReceipt> {
    if (!isCurrent()) return interruptedReceipt();
    const { target, file } = action.payload;
    if (action.action === 'navigate') {
      const next = action.payload.session_id!;
      await api<Snapshot>(`/sessions/${encodeURIComponent(next)}`);
      if (!isCurrent()) return interruptedReceipt();
      setMenu(false); setHistory(false); setFiles(null); setPreview(null); closeAccount(); setAppearance(false);
      navigate(next);
    } else if (action.action === 'open' || action.action === 'close') {
      const open = action.action === 'open';
      if (target === 'history') setHistory(open);
      else if (target === 'surfaces') setSurfaces(open);
      else if (target === 'menu') setMenu(open);
      else if (target === 'preview' && !open) setPreview(null);
      else if (target === 'files') {
        const next = open ? (await api<{ files: FileEntry[] }>('/files')).files : null;
        if (!isCurrent()) return interruptedReceipt();
        setFiles(next);
      }
      else if (target === 'account') { if (open) setAccount(true); else closeAccount(); }
      else if (target === 'password') { setAccount(open); setPasswordRequested(open ? Date.now() : 0); }
      else if (target === 'appearance') setAppearance(open);
      else if (target === 'developer') {
        const next = await api<Preferences>('/settings/preferences', { developer_mode: open });
        if (!isCurrent()) return interruptedReceipt();
        applyPreferences(next);
      } else if (target === 'workspace') {
        const latest = workspaces.at(-1);
        if (!latest) return { status: 'failed', error_code: 'unavailable' };
        if (open) await changeComponentView(latest, { hidden: false });
        else await Promise.all(workspaces.filter(item => !item.view?.hidden).map(item => changeComponentView(item, { hidden: true })));
      } else return { status: 'failed', error_code: 'unavailable' };
    } else if (action.action === 'preview' && file) setPreview(file);
    else if (action.action === 'download' && file) {
      const link = document.createElement('a'); link.href = `${file.content_url}?download=1`; link.download = file.name;
      document.body.append(link); link.click(); link.remove();
    } else if (action.action === 'attach') {
      if (!file) { setChooseFiles(true); return { status: 'failed', error_code: 'user_gesture_required' }; }
      const currentFiles = drafts.current!.read(uiScope.current.owner, uiScope.current.session).attached;
      if (currentFiles.length >= 10 && !currentFiles.some(item => item.id === file.id)) return { status: 'failed', error_code: 'unavailable' };
      changeAttachments(old => old.some(item => item.id === file.id) ? old : [...old, file]);
    } else if (action.action === 'detach' && file) changeAttachments(old => old.filter(item => item.id !== file.id));
    else if (action.action !== 'sign_out') return { status: 'failed', error_code: 'unavailable' };
    // Let React commit the requested view before reporting the browser effect.
    await new Promise<void>(resolve => { requestAnimationFrame(() => requestAnimationFrame(() => resolve())); window.setTimeout(resolve, 250); });
    return { status: 'applied' };
  }
  const appActionHandler = useRef(applyAppAction); appActionHandler.current = applyAppAction;
  useEffect(() => {
    const wake = () => { if (!document.hidden) setActionWake(old => old + 1); };
    document.addEventListener('visibilitychange', wake);
    return () => document.removeEventListener('visibilitychange', wake);
  }, []);
  useEffect(() => {
    if (!user || !session || document.hidden) return;
    const scopeStillCurrent = currentGuard(), authTicket = authEpoch.current, owner = user.id;
    const isSameAccount = () => authEpoch.current === authTicket && uiScope.current.owner === owner;
    for (const action of snapshot.app_actions || []) {
      if (appActionSeen.current.has(action.id)) continue;
      appActionSeen.current.add(action.id);
      appActionQueue.current = appActionQueue.current.then(async () => {
        const isCurrent = () => scopeStillCurrent() && !document.hidden;
        appActionActive.current++;
        try {
          const applied = await consumeAppAction(action, appClient.current, api, (request, guard) => appActionHandler.current(request, guard), isCurrent, isSameAccount);
          if (applied && action.action === 'sign_out' && isSameAccount()) acceptUser(null);
        } finally { appActionActive.current--; }
      }).catch(() => { /* A dispatched effect is never replayed; unclaimed requests can reconnect. */ })
        .finally(() => { appActionSeen.current.delete(action.id); });
    }
  }, [snapshot.app_actions, session, user?.id, actionWake]);
  function acceptUser(next: User | null, saved?: Preferences) {
    if (uiScope.current.owner !== (next?.id || null)) { surfaceDrafts.current.clear(); setSurfaces(false); }
    const previous = uiScope.current.owner;
    authEpoch.current++; generation.current++;
    let nextSession: string | null = null;
    try { if (next) nextSession = localStorage.getItem('athena-session:' + next.id); } catch { /* Private browser mode. */ }
    // A guest's unsent first message may accompany login. A signed-out account's draft never does.
    if (next && !previous) {
      const guest = drafts.current!.read(null, null), existing = drafts.current!.read(next.id, nextSession);
      if (guest.text && !existing.text && !existing.attached.length) drafts.current!.write(next.id, nextSession, { text: guest.text, attached: [] });
      drafts.current!.clear(null, null);
    } else if (!next && previous) drafts.current!.clear(null, null);
    if (next) {
      try {
        const cached = JSON.parse(sessionStorage.getItem('athena-upgrade-draft') || 'null');
        if (cached?.user_id === next.id && Date.now() - cached.saved_at < 600000) {
          nextSession = cached.session_id || null;
          if (typeof cached.text === 'string') drafts.current!.write(next.id, nextSession, { text: cached.text, attached: Array.isArray(cached.attached) ? cached.attached : [] });
          sessionStorage.removeItem('athena-upgrade-draft');
        }
      } catch { /* A failed recovery must not prevent login. */ }
    }
    uiScope.current = { owner: next?.id || null, session: nextSession, generation: uiScope.current.generation + 1, control: uiScope.current.control + 1 };
    applyPreferences(saved || defaultPreferences); actionSeen.current = 0; dismissedAction.current = 0; setPasswordRequested(0); setPreview(null); setFiles(null); setSessions([]);
    setUser(next); setAccount(false); setAuth(null); setPassword(''); setChooseFiles(false); setMenu(false); setHistory(false); setAppearance(false);
    setSending(false); setUploading(false); setResetting(false); setSession(nextSession); showDraft(drafts.current!.read(next?.id || null, nextSession));
    setSnapshot(empty()); setError(''); setTextEffects({});
  }
  async function loadAccount() {
    const ticket = authEpoch.current;
    try {
      const result = await api<{ user: User | null; preferences?: Preferences; configured: boolean; has_users: boolean }>('/auth/me');
      if (authEpoch.current !== ticket) return;
      acceptUser(result.user, result.preferences); setHasUsers(result.has_users); setReady(true);
    } catch { if (authEpoch.current === ticket) setError(t('error.connection')); }
  }
  useEffect(() => { void loadAccount(); }, []);
  useEffect(() => {
    if (!user) return;
    let stopped = false, checking = false;
    const readingActivity = () => Boolean(document.querySelector('.run-trace[open], .message-reasoning[open], .trace-original[open]'));
    const check = async () => {
      if (checking || stopped || document.hidden || !upgradeState.current.safe || appActionActive.current || document.activeElement?.tagName === 'IFRAME' || readingActivity()) return;
      checking = true; const authTicket = authEpoch.current;
      const version = await checkClientVersion();
      const current = upgradeState.current;
      if (!stopped && version && current.safe && !appActionActive.current && document.activeElement?.tagName !== 'IFRAME' && !readingActivity() && current.user?.id === user.id && uiScope.current.owner === user.id && authEpoch.current === authTicket) {
        try {
          if (!drafts.current!.persist(user.id, uiScope.current.session)) { checking = false; return; }
          sessionStorage.setItem('athena-upgrade-draft', JSON.stringify({ user_id: user.id, session_id: uiScope.current.session, saved_at: Date.now() }));
          window.location.reload();
        } catch { /* Keep the page and draft when private storage is unavailable. */ }
      }
      checking = false;
    };
    const timer = window.setInterval(() => void check(), 10000);
    document.addEventListener('visibilitychange', check);
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener('visibilitychange', check); };
  }, [user?.id]);
  useEffect(() => { setUser(old => old && old.developer_mode !== preferences.developer_mode ? { ...old, developer_mode: preferences.developer_mode } : old); }, [preferences.developer_mode]);
  useEffect(() => {
    if (!composer.current) return;
    const observer = new ResizeObserver(entries => {
      const height = entries[0]?.target.getBoundingClientRect().height;
      if (height) document.documentElement.style.setProperty('--composer-height', `${height}px`);
    });
    observer.observe(composer.current);
    return () => { observer.disconnect(); document.documentElement.style.removeProperty('--composer-height'); };
  }, []);
  useEffect(() => {
    setServiceMessage('');
    if (!user) return;
    let disposed = false;
    const load = async () => {
      try {
        const result = await api<{ configured: boolean; message: string }>(`/service${session ? '?session_id=' + session : ''}`);
        if (!disposed) setServiceMessage(result.configured ? '' : t('error.serviceNotice'));
      } catch { /* Request errors are surfaced by the active operation. */ }
    };
    void load(); const timer = window.setInterval(() => void load(), 10000);
    return () => { disposed = true; clearInterval(timer); };
  }, [user?.id, session, locale]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 4000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { closeAccount(); setMenu(false); setAppearance(false); setAuth(null); setHistory(false); setFiles(null); setPreview(null); setPassword(''); }
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, []);
  useEffect(() => {
    try {
      if (user) {
        if (session) localStorage.setItem('athena-session:' + user.id, session);
        else localStorage.removeItem('athena-session:' + user.id);
      }
    } catch { /* Session navigation also works without browser persistence. */ }
    const ticket = ++generation.current;
    setSnapshot(empty()); setPhase('idle'); setReconnecting(false);
    if (!session || !user) return;
    let source: EventSource | undefined;
    let disposed = false;
    let seen = 0, eventCursor = 0, structuralFloor = 0, loading = false, again = false;
    const messageVersions = new Map<string, number>();
    const reload = async () => {
      if (loading) { again = true; return; }
      loading = true;
      try {
        do {
          again = false;
          const next = await api<Snapshot>(`/sessions/${session}`);
          if (disposed || ticket !== generation.current) return;
          if (next.cursor >= structuralFloor) { seen = Math.max(seen, next.cursor); setSnapshot(old => mergeSnapshot(old, next, messageVersions, structuralFloor) || old); if (next.preferences) applyPreferences(next.preferences); if (next.account_action) receiveAction(next.account_action); const active = next.runs.find(run => run.status === 'running'); if (active?.phase) setPhase(active.phase as Phase); }
          else again = true;
        } while (again && !disposed);
      } catch (e) { if (!disposed) setError((e as Error).message); }
      finally { loading = false; }
    };
    void (async () => {
      await reload();
      if (disposed) return;
      eventCursor = seen;
      source = new EventSource(`/api/sessions/${session}/events?after=${eventCursor}`);
      source.onopen = () => { setReconnecting(false); void reload(); };
      source.onerror = () => { if (!disposed) setReconnecting(true); };
      source.onmessage = event => {
        if (disposed) return;
        const item = JSON.parse(event.data);
        if (item.seq <= eventCursor) return;
        eventCursor = item.seq;
        if (item.type === 'account_action') receiveAction({ seq: item.seq, ...item.payload });
        if (item.type === 'account_changed') setUser(item.payload.user);
        if (item.type === 'reset' || (item.type === 'app_action' && item.payload.status === 'dismissed' && ['reset', 'interrupted'].includes(item.payload.error_code))) invalidateActions();
        if (item.seq <= seen) return;
        if (item.type === 'message') messageVersions.set(item.payload.id, item.seq);
        else structuralFloor = Math.max(structuralFloor, item.seq);
        if (item.type === 'message') setSnapshot(old => {
          const found = old.messages.some(message => message.id === item.payload.id);
          return { ...old, cursor: item.seq, messages: found ? old.messages.map(message => message.id === item.payload.id ? item.payload : message) : [...old.messages, item.payload] };
        });
        else if (item.type === 'account_changed') { setUser(item.payload.user); }

        else if (item.type === 'preferences_changed') { applyPreferences(item.payload); void reload(); }
        else if (item.type === 'status') { setPhase(item.payload.phase || 'thinking'); void reload(); }
        else { if (item.type === 'reset') setSnapshot(old => ({ ...old, components: [] })); void reload(); }
      };
    })();
    return () => { disposed = true; source?.close(); };
  }, [session, user?.id]);
  useEffect(() => { if (nearBottom.current) end.current?.scrollIntoView({ behavior: 'instant' }); }, [snapshot.messages, busy]);
  useEffect(() => {
    if (!history || !user) return;
    let disposed = false; const isCurrent = currentGuard();
    const load = () => void api<Session[]>('/sessions').then(next => { if (!disposed && isCurrent()) setSessions(next); }).catch(e => { if (!disposed && isCurrent()) setError(e.message); });
    load(); const timer = window.setInterval(load, 3000);
    return () => { disposed = true; clearInterval(timer); };
  }, [history, user?.id]);

  function requestLogin() { setAuth(hasUsers ? 'login' : 'register'); }
  async function submit(value = text, mode: 'queue' | 'interrupt' = 'queue', fromComposer = true, throwOnError = false) {
    if ((!value.trim() && (!fromComposer || attached.length === 0)) || sending || resetting || uploading) {
      if (throwOnError) throw new Error('voice_busy');
      return;
    }
    if (!user) { requestLogin(); return; }
    const owner = user.id;
    let isCurrent = currentGuard(), sid = session;
    let sentDraft = drafts.current!.read(owner, sid);
    const sentFiles = new Set(fromComposer ? sentDraft.attached.map(file => file.id) : []);
    setSending(true); setError(''); nearBottom.current = true;
    try {
      if (!sid) {
        sid = (await api<{ id: string }>('/sessions', {})).id;
        if (!isCurrent()) return;
        const unchanged = drafts.current!.read(owner, null).revision === sentDraft.revision;
        navigate(sid, true); isCurrent = currentGuard(); setSending(true);
        sentDraft = { ...sentDraft, revision: unchanged ? drafts.current!.read(owner, sid).revision : -1 };
      }
      if (help && fromComposer && mode === 'queue' && help.questions.length === 1) {
        await api(`/sessions/${sid}/help/${help.id}/answer`, { answers: { [help.questions[0].id]: value }, file_ids: [...sentFiles] });
      } else await api(`/sessions/${sid}/messages`, { text: value, mode, file_ids: [...sentFiles] });
      if (fromComposer) {
        const remaining = drafts.current!.submitted(owner, sid, sentDraft);
        if (uiScope.current.owner === owner && uiScope.current.session === sid) showDraft(remaining);
        if (isCurrent()) input.current?.focus();
      }
    } catch (e) { if (isCurrent()) setError((e as Error).message); if (throwOnError) throw e; }
    finally { if (isCurrent()) setSending(false); }
  }
  async function ensureVoiceSession() {
    if (!user) { requestLogin(); return null; }
    if (session) return session;
    const isCurrent = currentGuard();
    const next = await api<{ id: string }>('/sessions', {});
    if (!isCurrent()) return null;
    navigate(next.id, true);
    return next.id;
  }
  async function submitVoice(value: string) {
    if (session && help && voiceQuestion) {
      const answers = { ...voiceAnswers, [voiceQuestion.id]: value };
      if (help.questions.some(question => !(question.id in answers))) {
        setVoiceHelpAnswers({ id: help.id, answers });
      } else {
        await api(`/sessions/${session}/help/${help.id}/answer`, { answers, file_ids: [] });
        setVoiceHelpAnswers({ id: '', answers: {} });
      }
    } else await submit(value, 'interrupt', false, true);
  }
  async function interruptVoice() {
    // Speaking over a clarification only stops its audio; the pending question
    // remains available for the user's spoken answer.
    if (!session || help) return;
    invalidateActions();
    await api(`/sessions/${session}/stop`, {});
  }
  function invalidateActions() {
    uiScope.current.control++; setChooseFiles(false); setSending(false); setUploading(false);
  }
  async function stop() {
    if (!session) return;
    setVoiceRecovery(old => old + 1);
    invalidateActions(); const isCurrent = currentGuard();
    try { await api(`/sessions/${session}/stop`, {}); if (isCurrent()) setPhase('stopped'); }
    catch (e) { if (isCurrent()) setError((e as Error).message); }
  }
  async function answerHelp(answers: Record<string, string>) {
    if (!session || !help || sending || uploading) return;
    const sid = session, owner = user!.id, isCurrent = currentGuard();
    const sentFiles = new Set(attached.map(file => file.id));
    setSending(true); setError('');
    try {
      await api(`/sessions/${sid}/help/${help.id}/answer`, { answers, file_ids: [...sentFiles] });
      const draft = drafts.current!.read(owner, sid);
      const remaining = drafts.current!.write(owner, sid, { ...draft, attached: draft.attached.filter(file => !sentFiles.has(file.id)) });
      if (!isCurrent()) return;
      showDraft(remaining);
      const next = await api<Snapshot>(`/sessions/${sid}`);
      if (isCurrent()) setSnapshot(old => next.cursor >= old.cursor ? next : old);
    } catch (e) { if (isCurrent()) setError((e as Error).message); }
    finally { if (isCurrent()) setSending(false); }
  }
  async function reset() {
    surfaceDrafts.current.clear(); setSurfaces(false);
    setVoiceRecovery(old => old + 1);
    invalidateActions(); const isCurrent = currentGuard();
    setSnapshot(old => ({ ...old, components: [] })); setTextEffects({}); applyPreferences(defaultPreferences);
    if (!user) { setError(''); setToast(t('resetGuest')); return; }
    const owner = user.id, authTicket = authEpoch.current;
    setResetting(true); setError('');
    try {
      await api('/reset', {});
      // Reset broadcasts also invalidate the action scope. Account identity still gates the receipt.
      if (uiScope.current.owner !== owner || authEpoch.current !== authTicket) return;
      if (isCurrent() && session) {
        const next = await api<Snapshot>(`/sessions/${session}`);
        if (isCurrent()) setSnapshot(old => next.cursor >= old.cursor ? next : old);
      }
      if (uiScope.current.owner === owner && authEpoch.current === authTicket) setToast(t('resetDone'));
    } catch (e) { if (uiScope.current.owner === owner && authEpoch.current === authTicket) setError((e as Error).message); }
    finally { if (uiScope.current.owner === owner && authEpoch.current === authTicket) setResetting(false); }
  }
  async function developer(enabled: boolean) {
    const isCurrent = currentGuard();
    if (!enabled) setUser(old => old ? { ...old, developer_mode: false } : old);
    try {
      await api('/settings/developer', { enabled });
      if (!isCurrent()) return;
      applyPreferences({ developer_mode: enabled }); setUser(old => old ? { ...old, developer_mode: enabled } : old);
    } catch (e) { if (isCurrent()) setError((e as Error).message); }
  }
  function receiveAction(action: { seq: number; action: string }) {
    if (action.action !== 'change_password' || action.seq <= actionSeen.current || action.seq <= dismissedAction.current) return;
    actionSeen.current = action.seq; setAccount(true); setPasswordRequested(action.seq);
  }
  function closeAccount() {
    setAccount(false);
    const sequence = actionSeen.current;
    if (sequence > dismissedAction.current) {
      dismissedAction.current = sequence;
      void api(`/account/actions/${sequence}/dismiss`, {}).catch(e => setError(e.message));
    }
    setPasswordRequested(0);
  }
  async function changePreferences(change: Partial<Preferences>) {
    if (!user) { applyPreferences(change); return; }
    const isCurrent = currentGuard();
    try { const next = await api<Preferences>('/settings/preferences', change); if (isCurrent()) applyPreferences(next); }
    catch (e) { if (isCurrent()) setError((e as Error).message); }
  }
  async function openFiles() {
    if (!user) { requestLogin(); return; }
    const isCurrent = currentGuard();
    try { const result = await api<{ files: FileEntry[] }>('/files'); if (isCurrent()) setFiles(result.files); }
    catch (e) { if (isCurrent()) setError((e as Error).message); }
  }
  async function upload(selection: FileList | null) {
    if (!selection?.length || uploading) return;
    if (!user) { requestLogin(); return; }
    const chosen = [...selection], owner = user.id, authTicket = authEpoch.current;
    if (chosen.length + attached.length > 10) { setError(t('files.limit')); return; }
    if (chosen.some(file => file.size > 20 * 1024 * 1024)) { setError(t('error.file_too_large')); return; }
    let isCurrent = currentGuard(), sid = session;
    setUploading(true); setError('');
    try {
      if (!sid) {
        sid = (await api<{ id: string }>('/sessions', {})).id;
        if (!isCurrent()) return;
        navigate(sid, true); isCurrent = currentGuard(); setUploading(true);
      }
      for (const file of chosen) {
        if (authEpoch.current !== authTicket || uiScope.current.owner !== owner) return;
        const response = await fetch(`/api/sessions/${sid}/files?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } });
        const result = await response.json();
        if (!response.ok) throw new Error(errorMessage(result.code));
        const draft = drafts.current!.read(owner, sid);
        const next = drafts.current!.write(owner, sid, { ...draft, attached: [...draft.attached, result].slice(0, 10) });
        if (authEpoch.current === authTicket && uiScope.current.owner === owner && uiScope.current.session === sid) showDraft(next);
      }
    } catch (e) { if (isCurrent()) setError((e as Error).message); }
    finally { if (isCurrent()) { setUploading(false); if (fileInput.current) fileInput.current.value = ''; } }
  }
  async function signOut() {
    const ticket = authEpoch.current;
    invalidateActions();
    try {
      try { await api('/auth/logout', {}); }
      catch (error) {
        if (authEpoch.current !== ticket) return;
        const result = await api<{ user: User | null }>('/auth/me');
        if (result.user) throw error;
      }
      if (authEpoch.current === ticket) acceptUser(null);
    } catch (e) { if (authEpoch.current === ticket) setError((e as Error).message); }
  }
  async function forkSession() {
    if (!session) return;
    const isCurrent = currentGuard();
    try {
      const next = await api<{ id: string }>(`/sessions/${session}/fork`, {});
      if (isCurrent()) { navigate(next.id); setHistory(false); }
    } catch (e) { if (isCurrent()) setError((e as Error).message); }
  }
  function fileCard(file: FileEntry, removable = false) {
    return <div className="attachment" key={file.id}><button type="button" className="attachment-open" onClick={() => setPreview(file)} title={t('files.preview', { name: file.name })}><span className="file-type">{file.name.split('.').pop()?.slice(0, 5).toUpperCase()}</span><span><strong>{file.name}</strong><small>{new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(file.size / 1024)} KB</small></span></button>{removable && <button className="icon-button" type="button" aria-label={t('files.remove')} onClick={() => changeAttachments(old => old.filter(item => item.id !== file.id))}>×</button>}</div>;
  }
  function renderWidget(component: Component) {
    const rect = floatingPlacements.has(placementOf(component)) ? panelLayout[component.id] : undefined;
    const key = `${user?.id}:${session}:${component.id}:${component.version}`;
    return <Widget key={key} component={component} sessionId={session!}
      submit={value => void submit(value, 'queue', false)} onStyleText={styleText} onNavigate={navigateFromWidget}
      onView={changeComponentView} floating={rect || undefined} fallback={rect === null} bounds={surfaceBounds} onHeight={measureWidget}
      draft={surfaceDrafts.current.get(key)} onDraft={value => { if (!surfaceDrafts.current.has(key) && surfaceDrafts.current.size >= 64) surfaceDrafts.current.delete(surfaceDrafts.current.keys().next().value!); surfaceDrafts.current.set(key, value); }} />;
  }
  const tracedRuns = new Set<string>();
  const runsById = new Map(snapshot.runs.map(run => [run.id, run]));
  return <div className="app" data-reading={reading} data-phase={orbPhase}>
    <header className="topbar">
      <button className="brand" onClick={() => { navigate(null); }} aria-label={t('newChat')}><AthenaMark compact /><span className="brand-wordmark">athena</span></button>
      <nav aria-label={t('chatControls')}>{workspace && !resetting && <button className="quiet workspace-return" onClick={() => { void Promise.all(workspaces.filter(item => !item.view?.hidden).map(item => changeComponentView(item, { hidden: true }))).catch(() => {}); }}>{t('backToChat')}</button>}<button className="icon-button menu-button" title={t('menu')} aria-label={t('menu')} onClick={() => setMenu(true)}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></svg></button></nav>
    </header>
    <div className="workspace-layout">
      {!resetting && components.some(component => placementOf(component) === 'sidebar-start') && <aside className="surface-sidebar" data-side="start">{components.filter(component => placementOf(component) === 'sidebar-start').map(component => renderWidget(component))}</aside>}
      <div className="reading-pane">
    {!workspace && <div className="agent-presence">{presence && !resetting ? <div className="custom-presence">{renderWidget(presence)}</div> : <Orb phase={orbPhase} />}<span className="presence-label" role="status">{t(waitingAgents && !waiting ? 'agents.waiting' : orbPhase === 'responding' ? 'responding' : orbPhase === 'idle' ? 'status.ready' : `status.${orbPhase}`)}</span></div>}
    {!resetting && floating.filter(component => panelLayout[component.id]).map(component => renderWidget(component))}
    {!resetting && components.filter(component => placementOf(component) === 'overlay').map(component => <div className="scene-overlay" key={`${component.id}-${component.version}`}>{renderWidget(component)}</div>)}
    <main className={`scroll-area${workspace ? ' has-workspace' : ''}`} onWheel={event => { if (event.deltaY < 0) scrollIntent.current = Date.now(); }} onTouchMove={() => { scrollIntent.current = Date.now(); }} onKeyDown={event => { if (['Home', 'PageUp', 'ArrowUp'].includes(event.key)) scrollIntent.current = Date.now(); }} onScroll={event => { const el = event.currentTarget; nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 160; if (el.scrollTop > 80) setReading(true); else if (el.scrollTop <= 0 && Date.now() - scrollIntent.current < 1500) setReading(false); }}>
      {!resetting && components.filter(component => placementOf(component) === 'banner').map(component => <div className="agent-banner" role="status" key={`${component.id}-${component.version}`}>{renderWidget(component)}</div>)}
      {workspace && !resetting && <section className="workspace-panel">{renderWidget(workspace)}</section>}
      <div className="conversation" hidden={Boolean(workspace)}>
        <article className="message assistant greeting"><div>{t('greeting')}</div></article>
        {snapshot.messages.map(message => {
          const trace = message.run_id && !tracedRuns.has(message.run_id);
          if (trace) tracedRuns.add(message.run_id!);
          const origin = taskOrigin(message) || agentOrigin(message);
          const run = message.run_id ? runsById.get(message.run_id) : undefined;
          const visible = Boolean(message.content || message.attachments?.length);
          return <Fragment key={message.id}>
            {trace && message.role === 'assistant' && <RunTrace sessionId={session!} runId={message.run_id!} revision={snapshot.cursor} phase={run?.phase} status={run?.status} />}
            {message.help_reply && <><HelpReceipt reply={message.help_reply} />{Boolean(message.attachments?.length) && <div className="help-response-files">{message.attachments?.map(file => fileCard(file))}</div>}</>}
            {origin?.kind === 'agent_results' && <AgentResults origin={origin} />}
            {visible && !message.help_reply && !origin && <article className={`message ${message.role}`}>
              <div dir="auto" data-message-id={message.id} data-message-role={message.role}>{message.role === 'assistant' ? <MessageBody content={message.content} effect={effectFor(message.id)} /> : <span className="user-text"><StyledText text={message.content} effect={effectFor(message.id)} /></span>}{message.attachments?.map(file => fileCard(file))}</div>
              {message.status === 'queued' && <span className="message-note">{t('queued')}</span>}
              {['interrupted', 'cancelled', 'failed'].includes(message.status) && <span className="message-note">{message.status === 'cancelled' ? t('cancelled') : t('stopped')}</span>}
            </article>}
            {trace && message.role === 'user' && <RunTrace sessionId={session!} runId={message.run_id!} revision={snapshot.cursor} phase={run?.phase} status={run?.status} origin={origin} />}
            {trace && Boolean(snapshot.agents?.some(job => job.root_run_id === message.run_id)) && <AgentActivity sessionId={session!} revision={snapshot.cursor} jobs={snapshot.agents!.filter(job => job.root_run_id === message.run_id)} />}
          </Fragment>;
        })}
        {!resetting && components.filter(component => placementOf(component) === 'inline' || (floatingPlacements.has(placementOf(component)) && !panelLayout[component.id])).map(component => renderWidget(component))}
        {snapshot.tasks.length > 0 && <section className="tasks">{snapshot.tasks.map(task => <div className="task" key={task.id}><div><strong>{task.title}</strong><span>{task.status === 'paused' ? t('paused') : task.last_error ? t('watchRetry') : task.condition_command ? t('watching') : new Date(task.due_at).toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span></div>{task.status !== 'paused' && <button className="quiet" onClick={() => void api(`/sessions/${session}/tasks/${task.id}/pause`, {}).catch(e => setError(e.message))}>{t('pause')}</button>}</div>)}</section>}
        {snapshot.files?.some(file => file.source === 'output') && <section className="output-files" aria-label={t('files.outputs')}>{snapshot.files.filter(file => file.source === 'output').map(file => fileCard(file))}</section>}
        {lastError && <p className="notice" role="status">{lastError}</p>}<div ref={end} />
      </div>
    </main>
      </div>
      {!resetting && components.some(component => placementOf(component) === 'sidebar-end') && <aside className="surface-sidebar" data-side="end">{components.filter(component => placementOf(component) === 'sidebar-end').map(component => renderWidget(component))}</aside>}
    </div>
    <footer className="composer-area" ref={composer}><div className="composer-wrap">
      {help && <UserHelp key={help.id} request={help} busy={sending || uploading} answer={answerHelp} select={value => { editDraft({ text: value }); input.current?.focus(); }} />}
      {busy && !waiting && <div className="activity" role="status"><span className="typing"><i /><i /><i /></span>{reconnecting ? t('reconnecting') : waitingAgents ? t('agents.waiting') : orbPhase === 'executing' ? t('working') : orbPhase === 'responding' ? t('responding') : t('thinking')}{queued > 0 && <small>{t('queuedCount', { count: queued })}</small>}</div>}
      {error && <div className="notice" role="alert">{error}{!ready && <button className="quiet" onClick={() => void loadAccount()}>{t('retry')}</button>}</div>}
      {!error && serviceMessage && <div className="notice" role="status">{serviceMessage}</div>}
      {attached.length > 0 && <div className="attachment-tray" aria-label={t('files.attached')}>{attached.map(file => fileCard(file, true))}</div>}
      {uploading && <p className="activity" role="status">{t('files.uploading')}</p>}
      {chooseFiles && <div className="activity"><button className="quiet" type="button" onClick={() => { setChooseFiles(false); fileInput.current?.click(); }}>{t('files.attach')}</button><button className="icon-button" type="button" aria-label={t('close')} onClick={() => setChooseFiles(false)}>×</button></div>}
      <input type="file" multiple hidden ref={fileInput} onChange={event => void upload(event.target.files)} />
      <form className="composer" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <button className="icon-button attach-button" type="button" title={t('files.attach')} aria-label={t('files.attach')} disabled={uploading || sending} onClick={() => user ? fileInput.current?.click() : requestLogin()}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2m-7 13 8-8" /></svg></button>
        <textarea dir="auto" ref={input} aria-label={t('message')} placeholder={t(help ? help.questions.length === 1 ? 'help.placeholder' : 'queue' : 'placeholder')} value={text} rows={1} maxLength={30000} onChange={event => { editDraft({ text: event.target.value }); }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(); } }} />
        <div className="send-actions"><VoiceCall key={voiceRecovery} sessionId={session} ownerId={user?.id || null} messages={voiceMessages} busy={busy} disabled={!ready || resetting || sending || uploading} ensureSession={ensureVoiceSession} submit={submitVoice} interrupt={interruptVoice} onActiveChange={setVoiceActive} />{(busy || waiting) && (text.trim() || attached.length > 0) && <button className="interrupt" type="button" disabled={sending} onClick={() => void submit(text, 'interrupt')}>{t('interrupt')}</button>}{(busy || waiting) && !text.trim() && !attached.length ? <button className="send stop" aria-label={t('stop')} title={t('stop')} type="button" onClick={() => void stop()}><Icon name="stop" /></button> : <button className="send" aria-label={(busy && !waiting) || (help && help.questions.length > 1) ? t('queue') : t('send')} disabled={(!text.trim() && !attached.length) || sending || uploading || !ready || resetting} type="submit"><Icon name="send" /></button>}</div>
      </form>
    </div></footer>
    {surfaces && <div className="overlay centered" onMouseDown={event => { if (event.target === event.currentTarget) setSurfaces(false); }}><section className="dialog surface-dialog" role="dialog" aria-modal="true" aria-label={t('surface.all')}><div className="dialog-top"><h2>{t('surface.all')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setSurfaces(false)}>×</button></div><div className="surface-list">{snapshot.components.map(component => <div className="surface-row" key={component.id}><button className="surface-open" onClick={() => { adjustComponent(component, { hidden: false }); setSurfaces(false); }}><span>{component.title}</span><small>{t(component.view?.hidden ? 'surface.hidden' : 'surface.visible')}</small></button><ComponentControls component={component} change={view => adjustComponent(component, view)} /></div>)}</div></section></div>}
    {toast && <div className="toast" role="status">{toast}</div>}
    {Boolean(user?.developer_mode) && <ErrorBoundary panel close={() => void developer(false)}><DeveloperPanel session={session} close={() => void developer(false)} /></ErrorBoundary>}
    {menu && <div className="overlay centered" onMouseDown={event => { if (event.target === event.currentTarget) setMenu(false); }}><section className="dialog menu-dialog" role="dialog" aria-modal="true" aria-label={t('menu')}><div className="dialog-top"><h2>Athena</h2><button className="icon-button" aria-label={t('close')} onClick={() => setMenu(false)}>×</button></div><div className="menu-items"><button onClick={() => { setMenu(false); user ? setHistory(true) : requestLogin(); }}><Icon name="history" />{t('history')}</button><button onClick={() => { setMenu(false); void openFiles(); }}><Icon name="files" />{t('files')}</button>{snapshot.components.length > 0 && <button onClick={() => { setMenu(false); setSurfaces(true); }}><span className="menu-glyph">◇</span>{t('surface.all')}</button>}<button onClick={() => { setMenu(false); setAppearance(true); }}><span className="menu-glyph">Aa</span>{t('appearance')}</button><button onClick={() => { setMenu(false); user ? setAccount(true) : requestLogin(); }}><Icon name="user" />{user ? t('account') : t('signIn')}</button><button disabled={resetting} onClick={() => { setMenu(false); void reset(); }}><Icon name="reset" />{t('reset')}</button></div></section></div>}
    {appearance && <div className="overlay centered"><section className="dialog" role="dialog" aria-modal="true" aria-label={t('appearance')}><div className="dialog-top"><h2>{t('appearance')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setAppearance(false)}>×</button></div><label htmlFor="language-choice">{t('language')}</label><select id="language-choice" value={choice} onChange={event => void changePreferences({ ui_language: event.target.value })}><option value="system">{t('systemLanguage')}</option>{languages.map(language => <option key={language.tag} value={language.tag} lang={language.tag}>{language.name}</option>)}</select>{(['ui_font', 'ui_palette'] as const).map(key => <label key={key}>{t(key === 'ui_font' ? 'appearance.font' : 'appearance.palette')}<select value={preferences[key]} onChange={event => void changePreferences({ [key]: event.target.value })}>{key === 'ui_palette' && preferences.ui_palette.startsWith('#') && <option value={preferences.ui_palette}>{preferences.ui_palette}</option>}{(key === 'ui_font' ? ['system', 'humanist', 'serif', 'mono'] as const : ['dream', 'rose', 'ocean', 'forest'] as const).map(value => <option key={value} value={value}>{t(`appearance.${value}`)}</option>)}</select></label>)}<label className="toggle-row"><span>{t('reduceMotion')}</span><input type="checkbox" checked={reduced} onChange={event => void changePreferences({ reduced_motion: event.target.checked ? 'reduce' : 'system' })} /></label></section></div>}
    {history && <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) setHistory(false); }}><aside className="drawer" role="dialog" aria-modal="true" aria-label={t('history')}><div className="dialog-top"><h2>{t('conversations')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setHistory(false)}>×</button></div><button className="primary wide" onClick={() => { navigate(null); setHistory(false); }}>{t('newChat')}</button><div className="session-list">{sessions.map(item => <button className={`session ${item.id === session ? 'selected' : ''}`} key={item.id} onClick={() => { navigate(item.id); setHistory(false); }}><span>{item.title === '新的事情' ? t('newChat') : item.title}</span>{item.pending > 0 && <small>•••</small>}</button>)}</div>{session && <button className="quiet wide" disabled={busy || waiting} onClick={() => void forkSession()}>{t('fork')}</button>}</aside></div>}
    {auth && <div className="overlay centered"><form className="dialog" role="dialog" aria-modal="true" aria-label={auth === 'login' ? t('signIn') : t('createAccount')} onSubmit={async event => {
      event.preventDefault(); const ticket = ++authEpoch.current; invalidateActions(); setSending(true); setError('');
      try { const result = await api<{ user: User; preferences: Preferences }>(`/auth/${auth}`, { username, password }); if (authEpoch.current === ticket) { acceptUser(result.user, result.preferences); setHasUsers(true); } }
      catch (e) { if (authEpoch.current === ticket) setError((e as Error).message); } finally { if (authEpoch.current === ticket) setSending(false); }
    }}><div className="dialog-top"><h2>{auth === 'login' ? t('welcomeBack') : t('niceToMeet')}</h2><button className="icon-button" type="button" aria-label={t('close')} onClick={() => { setAuth(null); setPassword(''); }}>×</button></div><label htmlFor="username">{t('username')}</label><input id="username" autoComplete="username" autoFocus aria-describedby="username-hint" value={username} onChange={event => setUsername(event.target.value)} required minLength={2} maxLength={40} /><p id="username-hint" className="field-hint">{t('usernameHint')}</p><label htmlFor="password">{t('password')}</label><input id="password" type="password" autoComplete={auth === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={8} maxLength={128} placeholder={t('passwordHint')} />{error && <p className="notice">{error}</p>}<button className="primary wide" disabled={sending}>{sending ? t('wait') : auth === 'login' ? t('signIn') : t('createAccount')}</button><button type="button" className="quiet wide" onClick={() => { setAuth(auth === 'login' ? 'register' : 'login'); setError(''); }}>{auth === 'login' ? t('needAccount') : t('haveAccount')}</button></form></div>}
    {account && user && <div className="overlay centered"><section className="dialog" role="dialog" aria-modal="true" aria-label={t('account')}><div className="dialog-top"><h2>{user.display_name || user.username}</h2><button className="icon-button" aria-label={t('close')} onClick={closeAccount}>×</button></div><AccountProfile user={user} onChange={next => { if (uiScope.current.owner === next.id) setUser(next); }} passwordRequested={passwordRequested} onPasswordChanged={closeAccount} /><label className="toggle-row"><span>{t('developerMode')}</span><input type="checkbox" checked={Boolean(user.developer_mode)} onChange={event => void developer(event.target.checked)} /></label>{error && <p className="notice">{error}</p>}<button className="quiet wide logout" onClick={() => void signOut()}>{t('signOut')}</button></section></div>}
    {files && <div className="overlay centered"><section className="dialog file-dialog" role="dialog" aria-modal="true" aria-label={t('files')}><div className="dialog-top"><h2>{t('files')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setFiles(null)}>×</button></div>{files.length === 0 ? <p className="empty-note">{t('filesEmpty')}</p> : <div className="file-list">{files.map(file => fileCard(file))}</div>}</section></div>}
    {preview && <div className="overlay centered preview-overlay"><div className="preview-dialog" role="dialog" aria-modal="true" aria-label={preview.name}><FilePreview file={preview} onClose={() => setPreview(null)} /></div></div>}
  </div>;
}
