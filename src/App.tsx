import { useEffect, useRef, useState } from 'react';
import { api, empty, defaultPreferences } from './api';
import type { User, Snapshot, Session, Preferences } from './api';
import Orb from './Orb';
import type { Phase } from './Orb';
import Widget from './Widget';
import { MessageBody } from './MessageBody';
import { FilePreview } from './FilePreview';
import type { FileEntry } from './FilePreview';
import DeveloperPanel from './DeveloperPanel';
import { AccountProfile } from './AccountProfile';
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
  const [dismissedWorkspace, setDismissedWorkspace] = useState('');
  const [preview, setPreview] = useState<FileEntry | null>(null);
  const [attached, setAttached] = useState<FileEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const currentSession = useRef(session); currentSession.current = session;
  const generation = useRef(0);
  const draftRevision = useRef(0);
  const actionSeen = useRef(0);
  const dismissedAction = useRef(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const busy = snapshot.runs.some(run => ['running', 'queued'].includes(run.status));
  const queued = snapshot.runs.filter(run => run.status === 'queued').length;
  const lastError = snapshot.runs[0]?.error ? errorMessage(snapshot.runs[0]?.error_code) : '';
  const activePhase = snapshot.runs.find(run => run.status === 'running')?.phase;
  const orbPhase: Phase = busy ? (activePhase === 'responding' || activePhase === 'executing' ? activePhase : phase === 'responding' || phase === 'executing' ? phase : 'thinking') : snapshot.runs[0]?.status === 'interrupted' ? 'stopped' : 'idle';

  const workspace = snapshot.components.filter(component => component.placement === 'workspace' && `${component.id}:${component.version}` !== dismissedWorkspace).at(-1);
  function navigate(next: string | null) {
    generation.current++; currentSession.current = next; setSession(next); setAttached([]); setSnapshot(empty()); setPhase('idle'); setError('');
  }
  function acceptUser(next: User | null, saved?: Preferences) {
    generation.current++;
    applyPreferences(saved || defaultPreferences); actionSeen.current = 0; dismissedAction.current = 0; setPasswordRequested(0); setAttached([]); setPreview(null); setFiles(null);
    setUser(next); setAccount(false); setAuth(null); setPassword('');
    setSession(next ? localStorage.getItem('athena-session:' + next.id) : null);
    setSnapshot(empty()); setError('');
  }
  async function loadAccount() {
    try {
      const result = await api<{ user: User | null; preferences?: Preferences; configured: boolean; has_users: boolean }>('/auth/me');
      acceptUser(result.user, result.preferences); setHasUsers(result.has_users); setReady(true);
    } catch { setError(t('error.connection')); }
  }
  useEffect(() => { void loadAccount(); }, []);
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
    if (user) {
      if (session) localStorage.setItem('athena-session:' + user.id, session);
      else localStorage.removeItem('athena-session:' + user.id);
    }
    const ticket = ++generation.current;
    setSnapshot(empty()); setPhase('idle'); setReconnecting(false);
    if (!session || !user) return;
    let source: EventSource | undefined;
    let disposed = false;
    let seen = 0, eventCursor = 0, loading = false, again = false;
    const reload = async () => {
      if (loading) { again = true; return; }
      loading = true;
      try {
        do {
          again = false;
          const next = await api<Snapshot>(`/sessions/${session}`);
          if (disposed || ticket !== generation.current) return;
          if (next.cursor >= seen) { seen = next.cursor; setSnapshot(next); if (next.preferences) applyPreferences(next.preferences); if (next.account_action) receiveAction(next.account_action); const active = next.runs.find(run => run.status === 'running'); if (active?.phase) setPhase(active.phase as Phase); }
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
        if (item.seq <= seen) return;
        seen = item.seq;
        if (item.type === 'message') setSnapshot(old => {
          const found = old.messages.some(message => message.id === item.payload.id);
          return { ...old, cursor: item.seq, messages: found ? old.messages.map(message => message.id === item.payload.id ? item.payload : message) : [...old.messages, item.payload] };
        });
        else if (item.type === 'account_changed') { setUser(item.payload.user); }

        else if (item.type === 'preferences_changed') { applyPreferences(item.payload); }
        else if (item.type === 'status') { setPhase(item.payload.phase || 'thinking'); void reload(); }
        else { if (item.type === 'reset') setSnapshot(old => ({ ...old, components: [] })); void reload(); }
      };
    })();
    return () => { disposed = true; source?.close(); };
  }, [session, user?.id]);
  useEffect(() => { if (nearBottom.current) end.current?.scrollIntoView({ behavior: 'instant' }); }, [snapshot.messages, busy]);
  useEffect(() => {
    if (!history || !user) return;
    const load = () => void api<Session[]>('/sessions').then(setSessions).catch(e => setError(e.message));
    load(); const timer = window.setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [history, user?.id]);

  function requestLogin() { setAuth(hasUsers ? 'login' : 'register'); }
  async function submit(value = text, mode: 'queue' | 'interrupt' = 'queue', fromComposer = true) {
    if ((!value.trim() && (!fromComposer || attached.length === 0)) || sending || resetting || uploading) return;
    if (!user) { requestLogin(); return; }
    const ticket = generation.current;
    const sentRevision = draftRevision.current;
    const sentFiles = new Set(fromComposer ? attached.map(file => file.id) : []);
    setSending(true); setError(''); nearBottom.current = true;
    try {
      let id = session;
      if (!id) { id = (await api<{ id: string }>('/sessions', {})).id; if (generation.current === ticket) setSession(id); }
      await api(`/sessions/${id}/messages`, { text: value, mode, file_ids: [...sentFiles] });
      if (fromComposer && (currentSession.current === id || (session === null && generation.current === ticket))) {
        if (draftRevision.current === sentRevision) setText('');
        setAttached(old => old.filter(file => !sentFiles.has(file.id)));
        input.current?.focus();
      }
    } catch (e) { setError((e as Error).message); }
    finally { setSending(false); }
  }
  async function stop() {
    if (!session) return;
    try { await api(`/sessions/${session}/stop`, {}); setPhase('stopped'); } catch (e) { setError((e as Error).message); }
  }
  async function reset() {
    setSnapshot(old => ({ ...old, components: [] })); applyPreferences(defaultPreferences);
    if (!user) { setError(''); setText(''); setToast(t('resetGuest')); return; }
    setResetting(true); setError('');
    try {
      await api('/reset', {});
      if (session) setSnapshot(await api<Snapshot>(`/sessions/${session}`));
      setToast(t('resetDone'));
    } catch (e) { setError((e as Error).message); }
    finally { setResetting(false); }
  }
  async function developer(enabled: boolean) {
    if (!enabled) setUser(old => old ? { ...old, developer_mode: false } : old);
    try { await api('/settings/developer', { enabled }); setUser(old => old ? { ...old, developer_mode: enabled } : old); }
    catch (e) { setError((e as Error).message); }
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
    try { applyPreferences(await api<Preferences>('/settings/preferences', change)); }
    catch (e) { setError((e as Error).message); }
  }
  async function openFiles() {
    if (!user) { requestLogin(); return; }
    try { setFiles((await api<{ files: FileEntry[] }>('/files')).files); }
    catch (e) { setError((e as Error).message); }
  }
  async function upload(selection: FileList | null) {
    if (!selection?.length || uploading) return;
    if (!user) { requestLogin(); return; }
    const chosen = [...selection];
    if (chosen.length + attached.length > 10) { setError(t('files.limit')); return; }
    if (chosen.some(file => file.size > 20 * 1024 * 1024)) { setError(t('error.file_too_large')); return; }
    const ticket = generation.current;
    setUploading(true); setError('');
    try {
      let sid = session;
      if (!sid) { sid = (await api<{ id: string }>('/sessions', {})).id; if (generation.current !== ticket) return; currentSession.current = sid; setSession(sid); }
      for (const file of chosen) {
        const response = await fetch(`/api/sessions/${sid}/files?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } });
        const result = await response.json();
        if (!response.ok) throw new Error(errorMessage(result.code));
        if (currentSession.current === sid) setAttached(old => [...old, result]);
      }
    } catch (e) { setError((e as Error).message); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ''; }
  }
  function fileCard(file: FileEntry, removable = false) {
    return <div className="attachment" key={file.id}><button type="button" className="attachment-open" onClick={() => setPreview(file)} title={t('files.preview', { name: file.name })}><span className="file-type">{file.name.split('.').pop()?.slice(0, 5).toUpperCase()}</span><span><strong>{file.name}</strong><small>{new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(file.size / 1024)} KB</small></span></button>{removable && <button className="icon-button" type="button" aria-label={t('files.remove')} onClick={() => setAttached(old => old.filter(item => item.id !== file.id))}>×</button>}</div>;
  }
  return <div className="app">
    <header className="topbar">
      <button className="brand" onClick={() => { navigate(null); }} aria-label={t('newChat')}>athena<span>✦</span></button>
      <nav aria-label={t('chatControls')}><button className="icon-button menu-button" title={t('menu')} aria-label={t('menu')} onClick={() => setMenu(true)}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></svg></button></nav>
    </header>
    {!workspace && <div className="agent-presence"><Orb phase={orbPhase} /><span className="presence-label" role="status">{t(orbPhase === 'responding' ? 'responding' : orbPhase === 'idle' ? 'status.ready' : `status.${orbPhase}`)}</span></div>}
    <main className="scroll-area" onScroll={event => { const el = event.currentTarget; nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 160; }}>
      {!resetting && snapshot.components.filter(component => component.placement === 'banner').map(component => <div className="agent-banner" role="status" key={`${component.id}-${component.version}`}><Widget component={component} submit={value => void submit(value, 'queue', false)} /></div>)}
      {workspace && !resetting && <section className="workspace-panel"><button className="quiet" onClick={() => setDismissedWorkspace(`${workspace.id}:${workspace.version}`)}>{t('backToChat')}</button><Widget key={`${workspace.id}-${workspace.version}`} component={workspace} submit={value => void submit(value, 'queue', false)} /></section>}
      <div className="conversation">
        <article className="message assistant greeting"><div>{t('greeting')}</div></article>
        {snapshot.messages.map(message => <article key={message.id} className={`message ${message.role}`}><div dir="auto">{message.role === 'assistant' ? <MessageBody content={message.content} reasoning={message.reasoning} streaming={message.status === 'streaming' && !message.content} /> : <span className="user-text">{message.content}</span>}{message.attachments?.map(file => fileCard(file))}</div>{message.status === 'queued' && <span className="message-note">{t('queued')}</span>}{['interrupted', 'cancelled', 'failed'].includes(message.status) && <span className="message-note">{message.status === 'cancelled' ? t('cancelled') : t('stopped')}</span>}</article>)}
        {!resetting && snapshot.components.filter(component => !component.placement || component.placement === 'inline').map(component => <Widget key={`${component.id}-${component.version}`} component={component} submit={value => void submit(value, 'queue', false)} />)}
        {snapshot.tasks.length > 0 && <section className="tasks">{snapshot.tasks.map(task => <div className="task" key={task.id}><div><strong>{task.title}</strong><span>{task.status === 'paused' ? t('paused') : task.last_error ? t('watchRetry') : task.condition_command ? t('watching') : new Date(task.due_at).toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span></div>{task.status !== 'paused' && <button className="quiet" onClick={() => void api(`/sessions/${session}/tasks/${task.id}/pause`, {}).catch(e => setError(e.message))}>{t('pause')}</button>}</div>)}</section>}
        {snapshot.files?.some(file => file.source === 'output') && <section className="output-files" aria-label={t('files.outputs')}>{snapshot.files.filter(file => file.source === 'output').map(file => fileCard(file))}</section>}
        {lastError && <p className="notice" role="status">{lastError}</p>}<div ref={end} />
      </div>
    </main>
    <footer className="composer-area"><div className="composer-wrap">
      {busy && <div className="activity" role="status"><span className="typing"><i /><i /><i /></span>{reconnecting ? t('reconnecting') : orbPhase === 'executing' ? t('working') : orbPhase === 'responding' ? t('responding') : t('thinking')}{queued > 0 && <small>{t('queuedCount', { count: queued })}</small>}</div>}
      {error && <div className="notice" role="alert">{error}{!ready && <button className="quiet" onClick={() => void loadAccount()}>{t('retry')}</button>}</div>}
      {!error && serviceMessage && <div className="notice" role="status">{serviceMessage}</div>}
      {attached.length > 0 && <div className="attachment-tray" aria-label={t('files.attached')}>{attached.map(file => fileCard(file, true))}</div>}
      {uploading && <p className="activity" role="status">{t('files.uploading')}</p>}
      <input type="file" multiple hidden ref={fileInput} onChange={event => void upload(event.target.files)} />
      <form className="composer" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <button className="icon-button attach-button" type="button" title={t('files.attach')} aria-label={t('files.attach')} disabled={uploading || sending} onClick={() => user ? fileInput.current?.click() : requestLogin()}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2m-7 13 8-8" /></svg></button>
        <textarea dir="auto" ref={input} aria-label={t('message')} placeholder={t('placeholder')} value={text} rows={1} maxLength={30000} onChange={event => { draftRevision.current++; setText(event.target.value); }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(); } }} />
        <div className="send-actions">{busy && (text.trim() || attached.length > 0) && <button className="interrupt" type="button" disabled={sending} onClick={() => void submit(text, 'interrupt')}>{t('interrupt')}</button>}{busy && !text.trim() && !attached.length ? <button className="send stop" aria-label={t('stop')} title={t('stop')} type="button" onClick={() => void stop()}><Icon name="stop" /></button> : <button className="send" aria-label={busy ? t('queue') : t('send')} disabled={(!text.trim() && !attached.length) || sending || uploading || !ready || resetting} type="submit"><Icon name="send" /></button>}</div>
      </form>
    </div></footer>
    {toast && <div className="toast" role="status">{toast}</div>}
    {Boolean(user?.developer_mode) && <ErrorBoundary panel close={() => void developer(false)}><DeveloperPanel session={session} close={() => void developer(false)} /></ErrorBoundary>}
    {menu && <div className="overlay centered" onMouseDown={event => { if (event.target === event.currentTarget) setMenu(false); }}><section className="dialog menu-dialog" role="dialog" aria-modal="true" aria-label={t('menu')}><div className="dialog-top"><h2>Athena</h2><button className="icon-button" aria-label={t('close')} onClick={() => setMenu(false)}>×</button></div><div className="menu-items"><button onClick={() => { setMenu(false); user ? setHistory(true) : requestLogin(); }}><Icon name="history" />{t('history')}</button><button onClick={() => { setMenu(false); void openFiles(); }}><Icon name="files" />{t('files')}</button><button onClick={() => { setMenu(false); setAppearance(true); }}><span className="menu-glyph">Aa</span>{t('appearance')}</button><button onClick={() => { setMenu(false); user ? setAccount(true) : requestLogin(); }}><Icon name="user" />{user ? t('account') : t('signIn')}</button><button disabled={resetting} onClick={() => { setMenu(false); void reset(); }}><Icon name="reset" />{t('reset')}</button></div></section></div>}
    {appearance && <div className="overlay centered"><section className="dialog" role="dialog" aria-modal="true" aria-label={t('appearance')}><div className="dialog-top"><h2>{t('appearance')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setAppearance(false)}>×</button></div><label htmlFor="language-choice">{t('language')}</label><select id="language-choice" value={choice} onChange={event => void changePreferences({ ui_language: event.target.value })}><option value="system">{t('systemLanguage')}</option>{languages.map(language => <option key={language.tag} value={language.tag} lang={language.tag}>{language.name}</option>)}</select>{(['ui_font', 'ui_palette'] as const).map(key => <label key={key}>{t(key === 'ui_font' ? 'appearance.font' : 'appearance.palette')}<select value={preferences[key]} onChange={event => void changePreferences({ [key]: event.target.value })}>{(key === 'ui_font' ? ['system', 'humanist', 'serif', 'mono'] as const : ['dream', 'rose', 'ocean', 'forest'] as const).map(value => <option key={value} value={value}>{t(`appearance.${value}`)}</option>)}</select></label>)}<label className="toggle-row"><span>{t('reduceMotion')}</span><input type="checkbox" checked={reduced} onChange={event => void changePreferences({ reduced_motion: event.target.checked ? 'reduce' : 'system' })} /></label></section></div>}
    {history && <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) setHistory(false); }}><aside className="drawer" role="dialog" aria-modal="true" aria-label={t('history')}><div className="dialog-top"><h2>{t('conversations')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setHistory(false)}>×</button></div><button className="primary wide" onClick={() => { navigate(null); setHistory(false); }}>{t('newChat')}</button><div className="session-list">{sessions.map(item => <button className={`session ${item.id === session ? 'selected' : ''}`} key={item.id} onClick={() => { navigate(item.id); setHistory(false); }}><span>{item.title === '新的事情' ? t('newChat') : item.title}</span>{item.pending > 0 && <small>•••</small>}</button>)}</div>{session && <button className="quiet wide" disabled={busy} onClick={async () => { try { navigate((await api<{ id: string }>(`/sessions/${session}/fork`, {})).id); setHistory(false); } catch (e) { setError((e as Error).message); } }}>{t('fork')}</button>}</aside></div>}
    {auth && <div className="overlay centered"><form className="dialog" role="dialog" aria-modal="true" aria-label={auth === 'login' ? t('signIn') : t('createAccount')} onSubmit={async event => {
      event.preventDefault(); setSending(true); setError('');
      try { const result = await api<{ user: User; preferences: Preferences }>(`/auth/${auth}`, { username, password }); acceptUser(result.user, result.preferences); setHasUsers(true); }
      catch (e) { setError((e as Error).message); } finally { setSending(false); }
    }}><div className="dialog-top"><h2>{auth === 'login' ? t('welcomeBack') : t('niceToMeet')}</h2><button className="icon-button" type="button" aria-label={t('close')} onClick={() => { setAuth(null); setPassword(''); }}>×</button></div><label htmlFor="username">{t('username')}</label><input id="username" autoComplete="username" autoFocus aria-describedby="username-hint" value={username} onChange={event => setUsername(event.target.value)} required minLength={2} maxLength={40} /><p id="username-hint" className="field-hint">{t('usernameHint')}</p><label htmlFor="password">{t('password')}</label><input id="password" type="password" autoComplete={auth === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={8} maxLength={128} placeholder={t('passwordHint')} />{error && <p className="notice">{error}</p>}<button className="primary wide" disabled={sending}>{sending ? t('wait') : auth === 'login' ? t('signIn') : t('createAccount')}</button><button type="button" className="quiet wide" onClick={() => { setAuth(auth === 'login' ? 'register' : 'login'); setError(''); }}>{auth === 'login' ? t('needAccount') : t('haveAccount')}</button></form></div>}
    {account && user && <div className="overlay centered"><section className="dialog" role="dialog" aria-modal="true" aria-label={t('account')}><div className="dialog-top"><h2>{user.display_name || user.username}</h2><button className="icon-button" aria-label={t('close')} onClick={closeAccount}>×</button></div><AccountProfile user={user} onChange={setUser} passwordRequested={passwordRequested} onPasswordChanged={closeAccount} /><label className="toggle-row"><span>{t('developerMode')}</span><input type="checkbox" checked={Boolean(user.developer_mode)} onChange={event => void developer(event.target.checked)} /></label>{error && <p className="notice">{error}</p>}<button className="quiet wide logout" onClick={async () => { try { await api('/auth/logout', {}); acceptUser(null); } catch (e) { setError((e as Error).message); } }}>{t('signOut')}</button></section></div>}
    {files && <div className="overlay centered"><section className="dialog file-dialog" role="dialog" aria-modal="true" aria-label={t('files')}><div className="dialog-top"><h2>{t('files')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setFiles(null)}>×</button></div>{files.length === 0 ? <p className="empty-note">{t('filesEmpty')}</p> : <div className="file-list">{files.map(file => fileCard(file))}</div>}</section></div>}
    {preview && <div className="overlay centered preview-overlay"><div className="preview-dialog" role="dialog" aria-modal="true" aria-label={preview.name}><FilePreview file={preview} onClose={() => setPreview(null)} /></div></div>}
  </div>;
}
