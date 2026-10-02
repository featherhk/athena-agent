import { useEffect, useRef, useState } from 'react';
import { api, empty } from './api';
import type { User, Snapshot, Session } from './api';
import Orb from './Orb';
import type { Phase } from './Orb';
import Widget from './Widget';
import DeveloperPanel from './DeveloperPanel';
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
  const { t, locale, choice, setChoice, reduced, setReduced } = useI18n();
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
  const [appearance, setAppearance] = useState(false);
  const [auth, setAuth] = useState<'login' | 'register' | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [sending, setSending] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [reconnecting, setReconnecting] = useState(false);
  const [files, setFiles] = useState<{ name: string; version: number; active: number; path: string }[] | null>(null);
  const generation = useRef(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const busy = snapshot.runs.some(run => ['running', 'queued'].includes(run.status));
  const queued = snapshot.runs.filter(run => run.status === 'queued').length;
  const lastError = snapshot.runs[0]?.error ? errorMessage(snapshot.runs[0]?.error_code) : '';
  const orbPhase = busy ? phase === 'executing' ? 'executing' : 'thinking' : snapshot.runs[0]?.status === 'interrupted' ? 'stopped' : 'idle';

  function acceptUser(next: User | null) {
    setUser(next); setAccount(false); setAuth(null); setPassword('');
    setSession(next ? localStorage.getItem('athena-session:' + next.id) : null);
    setSnapshot(empty()); setError('');
  }
  async function loadAccount() {
    try {
      const result = await api<{ user: User | null; configured: boolean; has_users: boolean }>('/auth/me');
      acceptUser(result.user); setHasUsers(result.has_users); setReady(true);
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
      if (event.key === 'Escape') { setAccount(false); setAppearance(false); setAuth(null); setHistory(false); setFiles(null); setPassword(''); }
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
    let seen = 0, loading = false, again = false;
    const reload = async () => {
      if (loading) { again = true; return; }
      loading = true;
      try {
        do {
          again = false;
          const next = await api<Snapshot>(`/sessions/${session}`);
          if (disposed || ticket !== generation.current) return;
          if (next.cursor >= seen) { seen = next.cursor; setSnapshot(next); }
          else again = true;
        } while (again && !disposed);
      } catch (e) { if (!disposed) setError((e as Error).message); }
      finally { loading = false; }
    };
    void (async () => {
      await reload();
      if (disposed) return;
      source = new EventSource(`/api/sessions/${session}/events?after=${seen}`);
      source.onopen = () => { setReconnecting(false); void reload(); };
      source.onerror = () => { if (!disposed) setReconnecting(true); };
      source.onmessage = event => {
        if (disposed) return;
        const item = JSON.parse(event.data);
        if (item.seq <= seen) return;
        seen = item.seq;
        if (item.type === 'message') setSnapshot(old => {
          const found = old.messages.some(message => message.id === item.payload.id);
          return { ...old, cursor: item.seq, messages: found ? old.messages.map(message => message.id === item.payload.id ? item.payload : message) : [...old.messages, item.payload] };
        });
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
  async function submit(value = text, mode: 'queue' | 'interrupt' = 'queue') {
    if (!value.trim() || sending || resetting) return;
    if (!user) { requestLogin(); return; }
    const ticket = generation.current;
    setSending(true); setError(''); nearBottom.current = true;
    try {
      let id = session;
      if (!id) { id = (await api<{ id: string }>('/sessions', {})).id; if (generation.current === ticket) setSession(id); }
      await api(`/sessions/${id}/messages`, { text: value, mode });
      setText(''); input.current?.focus();
    } catch (e) { setError((e as Error).message); }
    finally { setSending(false); }
  }
  async function stop() {
    if (!session) return;
    try { await api(`/sessions/${session}/stop`, {}); setPhase('stopped'); } catch (e) { setError((e as Error).message); }
  }
  async function reset() {
    setSnapshot(old => ({ ...old, components: [] }));
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
  async function openFiles() {
    if (!user) { requestLogin(); return; }
    try {
      const data = await api<{ extensions: { name: string; version: number; active: number; file_path: string }[]; components: { title: string; version: number; active: number; file_path: string }[] }>('/files');
      setFiles([...data.extensions.map(item => ({ ...item, path: item.file_path })), ...data.components.map(item => ({ ...item, name: item.title, path: item.file_path }))]);
    } catch (e) { setError((e as Error).message); }
  }
  return <div className="app">
    <header className="topbar">
      <button className="brand" onClick={() => { setSession(null); setError(''); }} aria-label={t('newChat')}>athena<span>✦</span></button>
      <nav aria-label={t('chatControls')}>
        <button className="icon-button language-button" title={t('appearance')} aria-label={t('appearance')} onClick={() => setAppearance(true)}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h12M9 3v2m-4 3c1 4 4 6 8 8M13 5c-1 6-5 10-10 12m10 4 4-11 4 11m-6-4h4" /></svg></button>
        <button className="icon-button" title={t('history')} aria-label={t('history')} onClick={() => user ? setHistory(true) : requestLogin()}><Icon name="history" /></button>
        <button className="icon-button" title={t('files')} aria-label={t('files')} onClick={() => void openFiles()}><Icon name="files" /></button>
        <button className={`icon-button ${resetting ? 'resetting' : ''}`} title={t('reset')} aria-label={t('reset')} disabled={resetting} onClick={() => void reset()}><Icon name="reset" /></button>
        <button className="icon-button account-button" title={user?.username || t('signIn')} aria-label={user ? t('account') : t('signIn')} onClick={() => user ? setAccount(true) : requestLogin()}>{user ? <span>{user.username.slice(0, 1)}</span> : <Icon name="user" />}</button>
      </nav>
    </header>
    <div className="agent-presence"><Orb phase={orbPhase} /></div>
    <main className="scroll-area" onScroll={event => { const el = event.currentTarget; nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 160; }}>
      <div className="conversation">
        <article className="message assistant greeting"><div>{t('greeting')}</div></article>
        {snapshot.messages.map(message => <article key={message.id} className={`message ${message.role}`}><div dir="auto">{message.content}</div>{message.status === 'queued' && <span className="message-note">{t('queued')}</span>}{['interrupted', 'cancelled', 'failed'].includes(message.status) && <span className="message-note">{message.status === 'cancelled' ? t('cancelled') : t('stopped')}</span>}</article>)}
        {!resetting && snapshot.components.map(component => <Widget key={`${component.id}-${component.version}`} component={component} submit={value => void submit(value)} />)}
        {snapshot.tasks.length > 0 && <section className="tasks">{snapshot.tasks.map(task => <div className="task" key={task.id}><div><strong>{task.title}</strong><span>{task.status === 'paused' ? t('paused') : task.last_error ? t('watchRetry') : task.condition_command ? t('watching') : new Date(task.due_at).toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span></div>{task.status !== 'paused' && <button className="quiet" onClick={() => void api(`/sessions/${session}/tasks/${task.id}/pause`, {}).catch(e => setError(e.message))}>{t('pause')}</button>}</div>)}</section>}
        {lastError && <p className="notice" role="status">{lastError}</p>}<div ref={end} />
      </div>
    </main>
    <footer className="composer-area"><div className="composer-wrap">
      {busy && <div className="activity" role="status"><span className="typing"><i /><i /><i /></span>{reconnecting ? t('reconnecting') : phase === 'executing' ? t('working') : t('thinking')}{queued > 0 && <small>{t('queuedCount', { count: queued })}</small>}</div>}
      {error && <div className="notice" role="alert">{error}{!ready && <button className="quiet" onClick={() => void loadAccount()}>{t('retry')}</button>}</div>}
      {!error && serviceMessage && <div className="notice" role="status">{serviceMessage}</div>}
      <form className="composer" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <textarea dir="auto" ref={input} aria-label={t('message')} placeholder={t('placeholder')} value={text} rows={1} maxLength={30000} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(); } }} />
        <div className="send-actions">{busy && text.trim() && <button className="interrupt" type="button" disabled={sending} onClick={() => void submit(text, 'interrupt')}>{t('interrupt')}</button>}{busy && !text.trim() ? <button className="send stop" aria-label={t('stop')} title={t('stop')} type="button" onClick={() => void stop()}><Icon name="stop" /></button> : <button className="send" aria-label={busy ? t('queue') : t('send')} disabled={!text.trim() || sending || !ready || resetting} type="submit"><Icon name="send" /></button>}</div>
      </form>
    </div></footer>
    {toast && <div className="toast" role="status">{toast}</div>}
    {Boolean(user?.developer_mode) && <ErrorBoundary panel close={() => void developer(false)}><DeveloperPanel session={session} close={() => void developer(false)} /></ErrorBoundary>}
    {appearance && <div className="overlay centered"><section className="dialog" role="dialog" aria-modal="true" aria-label={t('appearance')}><div className="dialog-top"><h2>{t('appearance')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setAppearance(false)}>×</button></div><label htmlFor="language-choice">{t('language')}</label><select id="language-choice" value={choice} onChange={event => setChoice(event.target.value)}><option value="system">{t('systemLanguage')}</option>{languages.map(language => <option key={language.tag} value={language.tag} lang={language.tag}>{language.name}</option>)}</select><label className="toggle-row"><span>{t('reduceMotion')}</span><input type="checkbox" checked={reduced} onChange={event => setReduced(event.target.checked)} /></label></section></div>}
    {history && <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) setHistory(false); }}><aside className="drawer" role="dialog" aria-modal="true" aria-label={t('history')}><div className="dialog-top"><h2>{t('conversations')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setHistory(false)}>×</button></div><button className="primary wide" onClick={() => { setSession(null); setHistory(false); setError(''); }}>{t('newChat')}</button><div className="session-list">{sessions.map(item => <button className={`session ${item.id === session ? 'selected' : ''}`} key={item.id} onClick={() => { setSession(item.id); setHistory(false); setError(''); }}><span>{item.title === '新的事情' ? t('newChat') : item.title}</span>{item.pending > 0 && <small>•••</small>}</button>)}</div>{session && <button className="quiet wide" disabled={busy} onClick={async () => { try { setSession((await api<{ id: string }>(`/sessions/${session}/fork`, {})).id); setHistory(false); } catch (e) { setError((e as Error).message); } }}>{t('fork')}</button>}</aside></div>}
    {auth && <div className="overlay centered"><form className="dialog" role="dialog" aria-modal="true" aria-label={auth === 'login' ? t('signIn') : t('createAccount')} onSubmit={async event => {
      event.preventDefault(); setSending(true); setError('');
      try { const result = await api<{ user: User }>(`/auth/${auth}`, { username, password }); acceptUser(result.user); setHasUsers(true); }
      catch (e) { setError((e as Error).message); } finally { setSending(false); }
    }}><div className="dialog-top"><h2>{auth === 'login' ? t('welcomeBack') : t('niceToMeet')}</h2><button className="icon-button" type="button" aria-label={t('close')} onClick={() => { setAuth(null); setPassword(''); }}>×</button></div><label htmlFor="username">{t('username')}</label><input id="username" autoComplete="username" autoFocus aria-describedby="username-hint" value={username} onChange={event => setUsername(event.target.value)} required minLength={2} maxLength={40} /><p id="username-hint" className="field-hint">{t('usernameHint')}</p><label htmlFor="password">{t('password')}</label><input id="password" type="password" autoComplete={auth === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={8} maxLength={128} placeholder={t('passwordHint')} />{error && <p className="notice">{error}</p>}<button className="primary wide" disabled={sending}>{sending ? t('wait') : auth === 'login' ? t('signIn') : t('createAccount')}</button><button type="button" className="quiet wide" onClick={() => { setAuth(auth === 'login' ? 'register' : 'login'); setError(''); }}>{auth === 'login' ? t('needAccount') : t('haveAccount')}</button></form></div>}
    {account && user && <div className="overlay centered"><section className="dialog" role="dialog" aria-modal="true" aria-label={t('account')}><div className="dialog-top"><h2>{user.username}</h2><button className="icon-button" aria-label={t('close')} onClick={() => { setAccount(false); }}>×</button></div><label className="toggle-row"><span>{t('developerMode')}</span><input type="checkbox" checked={Boolean(user.developer_mode)} onChange={event => void developer(event.target.checked)} /></label>{error && <p className="notice">{error}</p>}<button className="quiet wide logout" onClick={async () => { try { await api('/auth/logout', {}); acceptUser(null); } catch (e) { setError((e as Error).message); } }}>{t('signOut')}</button></section></div>}
    {files && <div className="overlay centered"><section className="dialog file-dialog" role="dialog" aria-modal="true" aria-label={t('files')}><div className="dialog-top"><h2>{t('files')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setFiles(null)}>×</button></div>{files.length === 0 ? <p className="empty-note">{t('filesEmpty')}</p> : <div className="file-list">{files.map((file, i) => <div key={file.path + i}><strong>{file.name}</strong><span>v{file.version} · {file.active ? t('registered') : t('disabled')}</span>{Boolean(user?.developer_mode) && <code>{file.path}</code>}</div>)}</div>}</section></div>}
  </div>;
}
