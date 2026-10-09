import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, empty, defaultPreferences } from './api';
import type { User, Snapshot, Session, Preferences, AppAction, Component, ViewChange } from './api';
import Orb, { AthenaMark } from './Orb';
import type { Phase } from './Orb';
import TaskContinuity, { useTaskContinuity, useVisibleActivity } from './TaskContinuity';
import type { ActivityItem } from './task-continuity';
import { SubmissionSourceError } from './frontend-submission';
import { frontendRequest } from './frontend-request';
import { SubmissionIntents, submissionBody, readSubmissionReceipt, readSubmissionLookup, readStopReceipt, submissionRejected } from './submission-intents';
import type { SubmissionIntent, SubmissionReceipt, StopIntent } from './submission-intents';
import Widget from './Widget';
import { selectedSurface, surfaceRecovery, messagePage, dismissSurfaces, surfacesDismissed, surfaceFailed, SurfaceError, type SurfaceHealth, type SurfaceDismissal, type SurfaceHost, type SurfaceState } from './surface-runtime';
import { findSurfaceFile, readSurfaceFile } from './surface-files';
import ComponentControls from './ComponentControls';
import { arrangePanels, floatingPlacements, placementOf } from './component-layout';
import { useSurfaceBounds } from './use-surface-bounds';
import { useConversationScroll } from './useConversationScroll';
import { useAutosizeTextarea } from './useAutosizeTextarea';
import type { FormDraft } from './component-forms';
import { MessageBody } from './MessageBody';
import { FilePreview } from './FilePreview';
import { ArtifactResult, canDisplayArtifact } from './ArtifactResult';
import type { FileEntry } from './FilePreview';
import DeveloperPanel from './DeveloperPanel';
import { AccountProfile } from './AccountProfile';
import UserHelp, { HelpReceipt } from './UserHelp';
import ComposerActions from './ComposerActions';
import VoiceCall from './VoiceCall';
import { VoiceSubmissionError, VoiceSubmissionReceipt } from './voice-recovery';
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
import { HelpDraftStore } from './help-drafts';
import { SessionSelection } from './session-selection';
import { SubmissionBarrier, prepareAppClaim } from './submission-barrier';
import type { SubmissionTicket } from './submission-barrier';
import type { Draft } from './draft-store';
import ErrorBoundary from './ErrorBoundary';
import { useI18n, languages, errorMessage } from './i18n';
import { useFrontendObservation } from './useFrontendObservation';
import { observesAppAction, type PreviewObservation } from './frontend-observation';
import { manageModalFocus, hasNativeModal } from './modal-focus';
import CompanionPortrait from './CompanionPortrait';
import { desktopRole, desktopPost, desktopScene, readDesktopState, interactionVisible, setDesktopPresented, companionHitRegions, type DesktopState } from './desktop-bridge';
import './desktop.css';

function Icon({ name }: { name: 'history' | 'reset' | 'user' | 'files' }) {
  const paths = {
    history: <><path d="M4 5v5h5M4.5 9a8 8 0 1 1-.2 6" /><path d="M12 7v5l3 2" /></>,
    reset: <><path d="M19 4v5h-5M19 9a7 7 0 1 0 .2 6" /><path d="m11 9-2 4h4l-2 3" /></>,
    user: <><circle cx="12" cy="8" r="3" /><path d="M5 21v-2a7 7 0 0 1 14 0v2" /></>,
    files: <path d="M3 7h7l2-3h8a1 1 0 0 1 1 1v14H3V7Zm0 0V5h7" />,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

export default function App() {
  const [desktop] = useState(() => desktopRole() === 'companion');
  const [desktopLog, setDesktopLog] = useState(false);
  const [desktopReplyOpen, setDesktopReplyOpen] = useState(false);
  const desktopEscape = useRef<() => boolean>(() => false);
  const [desktopState, setDesktopState] = useState<{ scope: string; value: DesktopState } | undefined>();
  const appRoot = useRef<HTMLDivElement>(null);
  useEffect(() => appRoot.current ? manageModalFocus(appRoot.current) : undefined, []);
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
  const [activity, setActivity] = useState(false);
  const pendingActivityView = useRef<string | null>(null);
  const [account, setAccount] = useState(false);
  const [passwordRequested, setPasswordRequested] = useState(0);
  const [menu, setMenu] = useState(false);
  const [appearance, setAppearance] = useState(false);
  const [auth, setAuth] = useState<'login' | 'register' | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [sending, setSending] = useState(false);
  const [submissionCommit, setSubmissionCommit] = useState(0);
  const submissions = useRef(new SubmissionBarrier());
  useLayoutEffect(() => { submissions.current.commit(submissionCommit); }, [submissionCommit]);
  const [resetting, setResetting] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [reconnecting, setReconnecting] = useState(false);
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [surfaces, setSurfaces] = useState(false);
  const [recoveryMode, setRecoveryMode] = useState(() => surfaceRecovery(location.search));
  const [rendererHealth, setRendererHealth] = useState<Record<string, SurfaceHealth>>({});
  const [dismissedRenderer, setDismissedRenderer] = useState<SurfaceDismissal | null>(null);
  const [surfaceHeights, setSurfaceHeights] = useState<Record<string, number>>({});
  const surfaceDrafts = useRef(new Map<string, FormDraft>());
  const surfaceBounds = useSurfaceBounds(snapshot.components.some(component => !component.view?.hidden && floatingPlacements.has(placementOf(component))));
  const [preview, setPreview] = useState<FileEntry | null>(null);
  const [inlinePreviewSelection, setInlinePreview] = useState<FileEntry | null>(null);
  const inlinePreview = inlinePreviewSelection ? snapshot.files.find(file => file.id === inlinePreviewSelection.id) || inlinePreviewSelection : null;
  const [inlineObservation, setInlineObservation] = useState<PreviewObservation | null>(null);
  const [previewObservation, setPreviewObservation] = useState<PreviewObservation | null>(null);
  const [attached, setAttached] = useState<FileEntry[]>([]);
  const [uploading, setUploading] = useState(false);
  const [reading, setReading] = useState(false);
  const [textEffects, setTextEffects] = useState<Record<string, Record<string, TextStyleEffect>>>({});
  const [chooseFiles, setChooseFiles] = useState(false);
  const [actionWake, setActionWake] = useState(0);
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceRecovery, setVoiceRecovery] = useState(0);
  const appClient = useRef(crypto.randomUUID());
  const appActionActive = useRef(0);
  const authEpoch = useRef(0);
  const uiScope = useRef<AppScope>({ owner: null, session: null, generation: 0, control: 0 });
  const drafts = useRef<DraftStore | null>(null);
  const intents = useRef<SubmissionIntents | null>(null);
  const [, refreshIntents] = useState(0);
  const reconcilingIntents = useRef(new Set<string>());
  const helpDrafts = useRef<HelpDraftStore | null>(null);
  const [, setHelpDraftRevision] = useState(0);
  const selection = useRef<SessionSelection | null>(null);
  if (!selection.current) {
    let tabStorage: Storage | null = null, sharedStorage: Storage | null = null;
    try { tabStorage = sessionStorage; } catch { /* Memory fallback. */ }
    try { sharedStorage = localStorage; } catch { /* Tab persistence still works. */ }
    selection.current = new SessionSelection(tabStorage, sharedStorage);
  }
  if (!drafts.current) {
    let storage: Storage | null = null; try { storage = sessionStorage; } catch { /* Use memory. */ }
    drafts.current = new DraftStore(storage);
    intents.current = new SubmissionIntents(storage);
    helpDrafts.current = new HelpDraftStore(storage);
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
  const conversationScroll = useConversationScroll(setReading);
  const pendingIntents = user && session ? intents.current!.list(user.id, session) : [];
  const pendingStops = user && session ? intents.current!.pendingStops(user.id, session) : [];
  const upgradeState = useRef({ user, session, text, attached, safe: false });
  upgradeState.current = { user, session, text, attached, safe: pendingIntents.length === 0 && pendingStops.length === 0 && !voiceActive && !sending && !uploading && !resetting && !chooseFiles && !snapshot.help_requests?.length && !auth && !account && !appearance && !preview && !files && !history && !activity && !menu && !surfaces };
  const busy = snapshot.runs.some(run => ['running', 'queued', 'waiting_agents'].includes(run.status));
  const waitingAgents = snapshot.runs.some(run => run.status === 'waiting_agents');
  const help = snapshot.help_requests?.[0];
  const helpDraftScope = user && session && help ? { owner: user.id, session, request: help.id } : null;
  const helpAnswers = helpDraftScope ? helpDrafts.current!.read(helpDraftScope).answers : {};
  const waiting = Boolean(help);
  const voiceQuestion = help?.questions.find(question => !helpAnswers[question.id]?.trim());
  const voiceMessages = voiceQuestion && help ? [...snapshot.messages, { id: `voice-help:${help.id}:${voiceQuestion.id}`, role: 'assistant', content: voiceQuestion.question, status: 'complete' }] : snapshot.messages;
  const queued = snapshot.runs.filter(run => run.status === 'queued').length;
  const lastError = snapshot.runs[0]?.error ? errorMessage(snapshot.runs[0]?.error_code) : '';
  const activePhase = snapshot.runs.find(run => run.status === 'running')?.phase;
  const orbPhase: Phase = waiting ? 'waiting' : busy ? (activePhase === 'responding' || activePhase === 'executing' ? activePhase : phase === 'responding' || phase === 'executing' ? phase : 'thinking') : snapshot.runs[0]?.status === 'interrupted' ? 'stopped' : 'idle';

  const selectedRenderer = selectedSurface(snapshot.components);
  const rendererKey = selectedRenderer ? `${user?.id}:${session}:${selectedRenderer.id}:${selectedRenderer.version}:${locale}` : '';
  const rendererUnavailable = surfaceFailed(rendererHealth[rendererKey], selectedRenderer);
  const renderer = !recoveryMode && !resetting && !surfacesDismissed(dismissedRenderer, user?.id || null, session, snapshot.components) && !rendererUnavailable ? selectedRenderer : undefined;
  useAutosizeTextarea(input, text, !renderer, `${locale}:${preferences.ui_font}`);
  const rendererIdentity = useRef('');
  const rendererActivation = useRef(0);
  if (rendererIdentity.current !== (renderer ? rendererKey : '')) rendererActivation.current++;
  rendererIdentity.current = renderer ? rendererKey : '';
  const rendererRevision = useRef(0); rendererRevision.current = renderer?.view_revision || 0;
  const activeRendererActivation = rendererActivation.current;
  const actualSurface: SurfaceState = renderer ? { provider: 'component', id: renderer.id, version: renderer.version, view_revision: renderer.view_revision || 0,
    state: rendererHealth[rendererKey]?.state === 'mounted' && rendererHealth[rendererKey]?.activation === activeRendererActivation ? 'mounted' : 'loading' } : { provider: 'default', state: 'mounted' };
  const components = recoveryMode || renderer ? [] : snapshot.components.filter(component => !component.view?.hidden);
  const workspaces = (recoveryMode || renderer ? [] : snapshot.components).filter(component => placementOf(component) === 'fullscreen').sort((a, b) => (a.view_updated_at || a.created_at || '').localeCompare(b.view_updated_at || b.created_at || ''));
  const workspace = workspaces.filter(component => !component.view?.hidden).at(-1);
  const presence = components.filter(component => placementOf(component) === 'presence').at(-1);
  const floating = components.filter(component => floatingPlacements.has(placementOf(component)));
  const panelLayout = arrangePanels(floating, surfaceBounds, locale === 'ar', surfaceHeights);
  const frontendObservation = useFrontendObservation(user?.id || null, appClient.current, {
    ...(user?.id === uiScope.current.owner && session === uiScope.current.session
      && desktopState?.scope === (uiScope.current.owner && uiScope.current.session ? `${uiScope.current.owner}:${uiScope.current.session}` : '') ? { desktop: desktopState.value } : {}),
    sessionId: session, components: snapshot.components, surface: actualSurface,
    preview: preview ? previewObservation?.file_id === preview.id ? previewObservation : { file_id: preview.id, status: 'loading' } : inlinePreview ? inlineObservation?.file_id === inlinePreview.id ? inlineObservation : { file_id: inlinePreview.id, status: 'loading' } : null,
    helpIds: (snapshot.help_requests || []).map(item => item.id), choosingFiles: chooseFiles, uploading, reconnecting,
    hasError: Boolean(error), hasUnsentChanges: Boolean(text.trim() || attached.length || Object.values(helpAnswers).some(value => value.trim())), attachmentIds: attached.map(file => file.id),
  }, enqueueAppAction, () => { appClient.current = crypto.randomUUID(); setActionWake(old => old + 1); }, () => { acceptUser(null); setAuth('login'); setError(errorMessage('login_required')); });
  const continuity = useTaskContinuity(user?.id || null, () => { acceptUser(null); setAuth('login'); setError(errorMessage('login_required')); });
  useVisibleActivity(continuity, session, snapshot.runs,
    Boolean(preview || account || appearance || files || history || activity || menu || surfaces || auth || workspace || renderer || resetting), snapshot.cursor);
  useEffect(() => {
    const run = pendingActivityView.current;
    if (!run || !snapshot.runs.some(item => item.id === run)) return;
    const candidates = [...document.querySelectorAll<HTMLElement>('[data-run-id]')].filter(element => element.dataset.runId === run);
    const target = candidates.at(-1);
    if (target) { target.scrollIntoView({ block: 'center' }); target.focus({ preventScroll: true }); }
    else end.current?.scrollIntoView({ block: 'end' });
    pendingActivityView.current = null;
  }, [snapshot, session]);
  async function openActivityConversation(item: ActivityItem) {
    const isCurrent = currentGuard();
    const next = await api<Snapshot>(`/sessions/${encodeURIComponent(item.session_id)}`);
    if (!isCurrent()) return false;
    // Viewing a chosen result must not be overridden by the chat's follow-tail effect.
    setMenu(false); setHistory(false); setFiles(null); setPreview(null); closeAccount(); setAppearance(false); setSurfaces(false); setActivity(false);
    if (session !== item.session_id) navigate(item.session_id);
    else setSnapshot(next);
    if (desktop) setDesktopLog(true);
    conversationScroll.policy.pause();
    pendingActivityView.current = item.run_id;
    return true;
  }
  function activityControl(embedded = false) {
    return <TaskContinuity key={user?.id} continuity={continuity} currentSession={embedded ? null : session} embedded={embedded}
      open={activity} setOpen={setActivity} includeCurrentTerminal={!conversationScroll.policy.following} openConversation={openActivityConversation}
      openHistory={() => { setActivity(false); setPreview(null); setHistory(true); }} />;
  }
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
  function submissionScope() {
    const scope = uiScope.current;
    return JSON.stringify([scope.owner, scope.session, scope.generation, scope.control, appClient.current]);
  }
  function finishSubmission(ticket: SubmissionTicket, isCurrent?: () => boolean) {
    const revision = submissions.current.finish(ticket);
    if (revision !== null) {
      if (isCurrent?.()) setSending(false);
      setSubmissionCommit(revision);
    }
  }
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
    if (desktop) { setDesktopLog(false); setDesktopReplyOpen(Boolean(next)); }
    pendingActivityView.current = null; setInlinePreview(null); setInlineObservation(null);
    conversationScroll.policy.resume();
    const scope = uiScope.current;
    selection.current!.write(scope.owner, next);
    const draft = migrate ? drafts.current!.move(scope.owner, scope.session, next) : drafts.current!.read(scope.owner, next);
    uiScope.current = { ...scope, session: next, generation: scope.generation + 1 };
    generation.current++; setSession(next); showDraft(draft);
    setSnapshot(empty()); setPhase('idle'); setError(''); setActivity(false); setReading(false); setTextEffects({}); setChooseFiles(false); setSurfaceHeights({}); setSurfaces(false);
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
    if (action.expected_revision != null && frontendObservation.snapshot()?.revision !== action.expected_revision) return { status: 'dismissed', error_code: 'frontend_conflict' };
    let effectIsCurrent = isCurrent;
    const { target, file } = action.payload;
    if (action.action === 'navigate') {
      const next = action.payload.session_id!;
      await api<Snapshot>(`/sessions/${encodeURIComponent(next)}`);
      if (!isCurrent()) return interruptedReceipt();
      setMenu(false); setHistory(false); setFiles(null); setPreview(null); closeAccount(); setAppearance(false);
      navigate(next);
      // This action's navigation advances the generation; subsequent user navigation still cancels it.
      const afterNavigation = currentGuard(); effectIsCurrent = () => afterNavigation() && interactionVisible();
    } else if (action.action === 'open' || action.action === 'close') {
      const open = action.action === 'open';
      if (target === 'activity') {
        if (open) { setMenu(false); setHistory(false); setFiles(null); setPreview(null); closeAccount(); setAppearance(false); setSurfaces(false); }
        setActivity(open);
      } else if (target === 'history') setHistory(open);
      else if (target === 'surfaces') setSurfaces(open);
      else if (target === 'menu') setMenu(open);
      else if (target === 'preview' && !open) { setPreview(null); setInlinePreview(null); }
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
        const latest = snapshot.components.filter(item => ['workspace', 'fullscreen'].includes(placementOf(item))).at(-1);
        if (!latest) return { status: 'failed', error_code: 'unavailable' };
        if (open) await changeComponentView(latest, { hidden: false, placement: 'fullscreen' });
        else await Promise.all(workspaces.filter(item => !item.view?.hidden).map(item => changeComponentView(item, { hidden: true })));
      } else return { status: 'failed', error_code: 'unavailable' };
    } else if (action.action === 'preview' && file) { setInlinePreview(file); setPreview(null); }
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
    // Confirm React's committed shell, then persist that observation before acknowledging.
    await new Promise<void>(resolve => { requestAnimationFrame(() => requestAnimationFrame(() => resolve())); window.setTimeout(resolve, 250); });
    for (let attempt = 0; attempt < 3; attempt++) {
      if (!effectIsCurrent()) return interruptedReceipt();
      const observation = await frontendObservation.report();
      if (!effectIsCurrent()) return interruptedReceipt();
      if (!observation) return { status: 'failed', error_code: 'frontend_unavailable' };
      if (frontendObservation.snapshot()?.revision !== observation.revision) continue;
      return observesAppAction(action, observation) ? { status: 'applied', observed_revision: observation.revision } : { status: 'failed', error_code: 'frontend_conflict', observed_revision: observation.revision };
    }
    return { status: 'failed', error_code: 'frontend_conflict' };
  }
  const appActionHandler = useRef(applyAppAction); appActionHandler.current = applyAppAction;
  useEffect(() => {
    const wake = () => { if (interactionVisible()) setActionWake(old => old + 1); };
    document.addEventListener('visibilitychange', wake);
    return () => document.removeEventListener('visibilitychange', wake);
  }, []);
  function enqueueAppAction(action: AppAction) {
    if (!user || !interactionVisible() || appActionSeen.current.has(action.id) || (action.target_client_id && action.target_client_id !== appClient.current)) return;
    const authTicket = authEpoch.current, owner = user.id, client = appClient.current, control = uiScope.current.control;
    const isSameAccount = () => authEpoch.current === authTicket && uiScope.current.owner === owner && appClient.current === client;
    appActionSeen.current.add(action.id);
    appActionQueue.current = appActionQueue.current.then(async () => {
      if (!isSameAccount() || uiScope.current.control !== control || !interactionVisible()) return;
      // A preceding action may have navigated this client to another owned conversation.
      const scopeStillCurrent = currentGuard();
      const isCurrent = () => scopeStillCurrent() && isSameAccount() && interactionVisible();
      appActionActive.current++;
      try {
        if (!await prepareAppClaim({ barrier: submissions.current, scope: submissionScope(), current: isCurrent, report: frontendObservation.report })) return;
        const applied = await consumeAppAction(action, client, api, (request, guard) => appActionHandler.current(request, guard), isCurrent, isSameAccount);
        if (applied && action.action === 'sign_out' && isSameAccount()) acceptUser(null);
      } finally { appActionActive.current--; }
    }).catch(() => { /* A dispatched effect is never replayed; unclaimed requests can reconnect. */ })
      .finally(() => { appActionSeen.current.delete(action.id); });
  }
  useEffect(() => { for (const action of snapshot.app_actions || []) enqueueAppAction(action); }, [snapshot.app_actions, session, user?.id, actionWake]);
  function acceptUser(next: User | null, saved?: Preferences) {
    if (uiScope.current.owner !== (next?.id || null)) { surfaceDrafts.current.clear(); setSurfaces(false); }
    const previous = uiScope.current.owner;
    appClient.current = crypto.randomUUID();
    authEpoch.current++; generation.current++;
    let nextSession = selection.current!.read(next?.id || null);
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
    pendingActivityView.current = null; conversationScroll.policy.resume(); setActivity(false); setUser(next); setAccount(false); setAuth(null); setPassword(''); setChooseFiles(false); setMenu(false); setHistory(false); setAppearance(false);
    setDesktopLog(false); setDesktopReplyOpen(false);
    setSending(false); setUploading(false); setResetting(false); setSession(nextSession); showDraft(drafts.current!.read(next?.id || null, nextSession));
    setSnapshot(empty()); setError(''); setTextEffects({}); setInlinePreview(null); setInlineObservation(null);
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
      if (checking || stopped || !interactionVisible() || !upgradeState.current.safe || appActionActive.current || document.activeElement?.tagName === 'IFRAME' || readingActivity()) return;
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
      if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229 || event.defaultPrevented || hasNativeModal(document)) return;
      if (desktopEscape.current()) { event.preventDefault(); return; }
      closeAccount(); setMenu(false); setAppearance(false); setAuth(null); setHistory(false); setFiles(null); setPreview(null); setSurfaces(false); setActivity(false); setPassword('');
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, []);
  useEffect(() => {
    selection.current!.write(user?.id || null, session);
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
          intents.current!.observe(user.id, session, next.submission_revision);
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
        else { if (item.type === 'reset') { setActivity(false); pendingActivityView.current = null; continuity.source.current?.refresh(); setSnapshot(old => ({ ...old, components: [] })); void frontendObservation.report().catch(() => {}); } void reload(); }
      };
    })();
    return () => { disposed = true; source?.close(); };
  }, [session, user?.id]);
  useEffect(() => { if (!workspace && !renderer) conversationScroll.follow(); }, [snapshot.messages, snapshot.files, snapshot.components, busy, Boolean(workspace), Boolean(renderer)]);
  useEffect(() => {
    const content = end.current?.parentElement;
    if (!content || workspace || renderer) return;
    // Late-loaded images and document previews grow the page too. Respect the
    // same reader intent as streamed text; never pull someone away from reading.
    const observer = new ResizeObserver(() => conversationScroll.follow());
    observer.observe(content);
    return () => observer.disconnect();
  }, [session, Boolean(workspace), Boolean(renderer)]);
  useEffect(() => {
    if (!history || !user) return;
    let disposed = false; const isCurrent = currentGuard();
    const load = () => void api<Session[]>('/sessions').then(next => { if (!disposed && isCurrent()) setSessions(next); }).catch(e => { if (!disposed && isCurrent()) setError(e.message); });
    load(); const timer = window.setInterval(load, 3000);
    return () => { disposed = true; clearInterval(timer); };
  }, [history, user?.id]);

  useEffect(() => {
    if (!user || !session) return;
    const owner = user.id, sid = session;
    const check = () => {
      if (!interactionVisible() || uiScope.current.owner !== owner || uiScope.current.session !== sid) return;
      for (const intent of intents.current!.pendingStops(owner, sid)) void reconcileStop(intent);
      for (const intent of intents.current!.list(owner, sid)) void reconcileSubmission(intent);
    };
    check();
    window.addEventListener('online', check);
    document.addEventListener('visibilitychange', check);
    return () => { window.removeEventListener('online', check); document.removeEventListener('visibilitychange', check); };
  }, [user?.id, session]);

  function acceptSubmission(intent: SubmissionIntent, receipt: SubmissionReceipt) {
    intents.current!.observe(intent.owner, intent.session, receipt.submission_revision);
    const stored = intents.current!.list(intent.owner, intent.session).find(item => item.id === intent.id);
    if (stored && !stored.preserveDraft && intent.draft) {
      const remaining = drafts.current!.acknowledged(intent.owner, intent.session, intent.draft);
      if (uiScope.current.owner === intent.owner && uiScope.current.session === intent.session) showDraft(remaining);
    }
    intents.current!.remove(intent); refreshIntents(old => old + 1);
  }
  async function reconcileSubmission(intent: SubmissionIntent): Promise<SubmissionReceipt | null> {
    const key = JSON.stringify([intent.owner, intent.session, intent.id]);
    if (reconcilingIntents.current.has(key) || uiScope.current.owner !== intent.owner) return null;
    reconcilingIntents.current.add(key);
    try {
      const raw = await frontendRequest(new AbortController().signal, signal => api(`/sessions/${intent.session}/submissions/${intent.id}`, undefined, signal));
      if (uiScope.current.owner !== intent.owner) return null;
      const receipt = readSubmissionLookup(raw, intent.id);
      intents.current!.observe(intent.owner, intent.session, receipt.submission_revision);
      if (!receipt.found) return null;
      acceptSubmission(intent, receipt); return receipt;
    } catch { return null; /* A missing or unreachable receipt is still uncertain. */ }
    finally { reconcilingIntents.current.delete(key); }
  }
  async function submissionRevision(owner: string, sid: string): Promise<number> {
    await Promise.all(intents.current!.pendingStops(owner, sid).map(intent => reconcileStop(intent)));
    if (intents.current!.needsStopReceipt(owner, sid) || intents.current!.pendingStops(owner, sid).length) throw new Error(t('send.stopUnknown'));
    const cached = intents.current!.revision(owner, sid);
    if (cached !== undefined) return cached;
    const epoch = intents.current!.epoch(owner, sid);
    const next = await frontendRequest(new AbortController().signal, signal => api<Snapshot>(`/sessions/${sid}`, undefined, signal));
    return intents.current!.refreshed(owner, sid, next.submission_revision, epoch);
  }
  async function reconcileStop(intent: StopIntent): Promise<number | null> {
    if (uiScope.current.owner !== intent.owner) return null;
    try {
      const raw = await frontendRequest(new AbortController().signal, signal => api(`/sessions/${intent.session}/stops/${intent.id}`, undefined, signal));
      if (uiScope.current.owner !== intent.owner) return null;
      const revision = readStopReceipt(raw, intent.id, true);
      if (revision === null) return null;
      intents.current!.confirmStop(intent, revision); refreshIntents(old => old + 1);
      return revision;
    } catch { return null; }
  }
  async function stopSession(owner: string, sid: string, retry?: StopIntent) {
    const intent = retry || { id: crypto.randomUUID(), owner, session: sid };
    if (intent.owner !== owner || intent.session !== sid) throw new Error('submission_conflict');
    if (retry && await reconcileStop(intent) !== null) return;
    intents.current!.addStop(intent); intents.current!.stopStarted(owner, sid); refreshIntents(old => old + 1);
    let revision: number | undefined;
    try {
      const receipt = await frontendRequest(new AbortController().signal, signal => api(`/sessions/${sid}/stop`, { client_stop_id: intent.id }, signal), 20000);
      revision = readStopReceipt(receipt, intent.id)!;
      intents.current!.confirmStop(intent, revision);
    } catch (error) {
      const found = await reconcileStop(intent);
      if (found === null) throw error;
      revision = found;
    } finally { intents.current!.stopFinished(owner, sid, revision); refreshIntents(old => old + 1); }
  }

  function requestLogin() { setAuth(hasUsers ? 'login' : 'register'); }
  async function submit(value = text, mode: 'queue' | 'interrupt' = 'queue', fromComposer = true, throwOnError = false, retryIntent?: SubmissionIntent) {
    if ((!value.trim() && (!fromComposer || attached.length === 0) && !retryIntent) || sending || resetting || uploading) {
      if (throwOnError) throw new VoiceSubmissionError('not_sent', new Error('voice_busy'));
      return;
    }
    if (!user) { requestLogin(); if (throwOnError) throw new VoiceSubmissionError('not_sent'); return; }
    const submission = submissions.current.begin(submissionScope());
    if (!submission) { if (throwOnError) throw new VoiceSubmissionError('not_sent', new Error('voice_busy')); return; }
    const voiceReceipt = throwOnError ? new VoiceSubmissionReceipt() : null;
    let acceptedReceipt: { status: 'accepted'; run_id?: string; help_id?: string } | undefined;
    let intent: SubmissionIntent | undefined, dispatched = false, retrying = false;
    const owner = user.id, control = uiScope.current.control;
    let isCurrent = currentGuard(), sid = session;
    let sentDraft = drafts.current!.read(owner, sid);
    const sentFiles = new Set(fromComposer ? sentDraft.attached.map(file => file.id) : []);
    setSending(true); setError(''); conversationScroll.policy.resume();
    try {
      if (!sid) {
        sid = (await api<{ id: string }>('/sessions', {})).id;
        if (!isCurrent()) { if (throwOnError) throw new VoiceSubmissionError('not_sent'); return; }
        const unchanged = drafts.current!.read(owner, null).revision === sentDraft.revision;
        navigate(sid, true); submissions.current.move(submission, submissionScope()); isCurrent = currentGuard(); setSending(true);
        sentDraft = { ...sentDraft, revision: unchanged ? drafts.current!.read(owner, sid).revision : -1 };
      }
      intent = retryIntent || (fromComposer ? intents.current!.forDraft(owner, sid, sentDraft) : undefined);
      if (!intent && help && fromComposer && mode === 'queue' && help.questions.length === 1) {
        if (intents.current!.pendingStops(owner, sid).length || intents.current!.needsStopReceipt(owner, sid)) {
          await submissionRevision(owner, sid);
          if (!isCurrent()) { if (throwOnError) throw new VoiceSubmissionError('not_sent'); return; }
        }
        const sentAnswers = helpDraftScope ? helpDrafts.current!.read(helpDraftScope) : null;
        voiceReceipt?.dispatch();
        const accepted = await api(`/sessions/${sid}/help/${help.id}/answer`, { answers: { [help.questions[0].id]: value }, file_ids: [...sentFiles] });
        if (helpDraftScope && sentAnswers) {
          if (!helpDrafts.current!.acknowledge(helpDraftScope, sentAnswers, accepted)) throw new Error(errorMessage('invalid_response'));
          setHelpDraftRevision(old => old + 1);
        }
        voiceReceipt?.confirm();
        acceptedReceipt = { status: 'accepted', help_id: help.id };
      } else {
        if (intent && (intent.owner !== owner || intent.session !== sid)) throw new Error('submission_conflict');
        retrying = Boolean(intent);
        if (intent) {
          const known = await reconcileSubmission(intent);
          if (known) { voiceReceipt?.confirm(); return { status: 'accepted' as const, run_id: known.run_id }; }
        }
        // Freeze the server fence before asynchronous frontend preparation. A
        // later Stop must reject this intent, not let it acquire a newer fence.
        const observedRevision = await submissionRevision(owner, sid);
        const revision = intent?.revision ?? observedRevision;
        const clientId = appClient.current;
        const frontendClientId = await frontendObservation.prepareSubmission(sid, clientId, () => isCurrent() && appClient.current === clientId);
        if (!isCurrent()) { if (throwOnError) throw new VoiceSubmissionError('not_sent'); return; }
        if (!intent) {
          intent = { id: crypto.randomUUID(), owner, session: sid, text: value, mode, file_ids: [...sentFiles], revision,
            draft: fromComposer && sentDraft.revision >= 0 ? sentDraft : null };
          intents.current!.add(intent);
        }
        voiceReceipt?.dispatch(); dispatched = true;
        const raw = await frontendRequest(new AbortController().signal,
          signal => api(`/sessions/${sid}/messages`, submissionBody(intent!, frontendClientId), signal), 20000);
        const receipt = readSubmissionReceipt(raw, intent.id);
        voiceReceipt?.confirm(); acceptSubmission(intent, receipt);
        acceptedReceipt = { status: 'accepted', run_id: receipt.run_id };
      }
      if (!intent && fromComposer && uiScope.current.control === control) {
        const remaining = drafts.current!.submitted(owner, sid, sentDraft);
        if (uiScope.current.owner === owner && uiScope.current.session === sid) showDraft(remaining);
        if (isCurrent()) input.current?.focus();
      }
    } catch (e) {
      if (intent && dispatched) {
        const rejected = submissionRejected(e) && (!retrying || (e as { code?: string }).code === 'submission_revision_conflict');
        if (rejected) {
          if ((e as { code?: string }).code === 'submission_revision_conflict') intents.current!.invalidate(owner, sid!);
          intents.current!.remove(intent); refreshIntents(old => old + 1);
          if (isCurrent()) setError((e as { code?: string }).code === 'submission_revision_conflict' ? t('send.notSent') : (e as Error).message);
          if (throwOnError) throw new VoiceSubmissionError('not_sent', e);
        } else {
          refreshIntents(old => old + 1);
          // Check once; never automatically repeat a possibly accepted POST.
          const known = await reconcileSubmission(intent);
          if (known) return { status: 'accepted' as const, run_id: known.run_id };
          if (throwOnError) throw new VoiceSubmissionError('unknown', e);
        }
      } else {
        const failure = retrying && intent && throwOnError ? new VoiceSubmissionError('unknown', e) : voiceReceipt?.failure(e);
        if (isCurrent() && !failure) setError(e instanceof SubmissionSourceError ? fromComposer ? t('send.notSent') : errorMessage(e.code) : (e as Error).message);
        if (failure) throw failure;
      }
    }
    finally { finishSubmission(submission, isCurrent); }
    return acceptedReceipt;
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
    if (session && help && helpDraftScope) {
      // Read at submission time: a typed edit may have committed since this
      // utterance began. A full saved form is never an implicit retry command.
      const saved = helpDrafts.current!.read(helpDraftScope);
      const question = help.questions.find(item => !saved.answers[item.id]?.trim());
      if (!question || !value.trim()) throw new VoiceSubmissionError('not_sent', new Error('voice_busy'));
      const submission = submissions.current.begin(submissionScope());
      if (!submission) throw new VoiceSubmissionError('not_sent', new Error('voice_busy'));
      const receipt = new VoiceSubmissionReceipt(), isCurrent = currentGuard();
      try {
        const sent = helpDrafts.current!.write(helpDraftScope, { ...saved.answers, [question.id]: value });
        setHelpDraftRevision(old => old + 1);
        if (help.questions.every(item => sent.answers[item.id]?.trim())) {
          if (intents.current!.pendingStops(user!.id, session).length || intents.current!.needsStopReceipt(user!.id, session)) {
            await submissionRevision(user!.id, session);
            if (!isCurrent()) throw new VoiceSubmissionError('not_sent');
          }
          receipt.dispatch();
          const accepted = await api(`/sessions/${session}/help/${help.id}/answer`, { answers: sent.answers, file_ids: [] });
          if (!helpDrafts.current!.acknowledge(helpDraftScope, sent, accepted)) throw new Error('voice_interrupted');
          receipt.confirm();
          setHelpDraftRevision(old => old + 1);
        }
      } catch (error) {
        const failure = receipt.failure(error); if (failure) throw failure;
      } finally { finishSubmission(submission); }
    } else await submit(value, 'interrupt', false, true);
  }
  async function interruptVoice() {
    // Speaking over a clarification only stops its audio; the pending question
    // remains available for the user's spoken answer.
    if (!session || help) return;
    invalidateActions();
    await stopSession(user!.id, session);
  }
  function invalidateActions() {
    const { owner, session: sid } = uiScope.current;
    if (owner && sid) { intents.current!.invalidate(owner, sid); intents.current!.preserveDrafts(owner, sid); }
    uiScope.current.control++; setChooseFiles(false); setSending(false); setUploading(false);
  }
  async function stop(throwOnError = false, retry?: StopIntent) {
    setVoiceRecovery(old => old + 1);
    invalidateActions(); const isCurrent = currentGuard();
    // Also cancel local preparation before the first session has been created.
    if (!session) return;
    try { await stopSession(user!.id, session, retry); if (isCurrent()) setPhase('stopped'); }
    catch (e) { if (isCurrent()) setError((e as Error).message); if (throwOnError) throw e; }
  }
  async function answerHelp(answers: Record<string, string>) {
    if (!session || !help || !helpDraftScope || sending || uploading) return;
    const submission = submissions.current.begin(submissionScope());
    if (!submission) return;
    const sid = session, owner = user!.id, isCurrent = currentGuard();
    const answerScope = helpDraftScope, sentAnswers = helpDrafts.current!.read(answerScope);
    const sentFiles = new Set(attached.map(file => file.id));
    setSending(true); setError('');
    try {
      if (intents.current!.pendingStops(owner, sid).length || intents.current!.needsStopReceipt(owner, sid)) {
        await submissionRevision(owner, sid);
        if (!isCurrent()) return;
      }
      const receipt = await api(`/sessions/${sid}/help/${help.id}/answer`, { answers, file_ids: [...sentFiles] });
      if (!helpDrafts.current!.acknowledge(answerScope, sentAnswers, receipt)) throw new Error(errorMessage('invalid_response'));
      setHelpDraftRevision(old => old + 1);
      const draft = drafts.current!.read(owner, sid);
      const remaining = drafts.current!.write(owner, sid, { ...draft, attached: draft.attached.filter(file => !sentFiles.has(file.id)) });
      if (!isCurrent()) return;
      showDraft(remaining);
      const next = await api<Snapshot>(`/sessions/${sid}`);
      if (isCurrent()) setSnapshot(old => next.cursor >= old.cursor ? next : old);
    } catch (e) { if (isCurrent()) setError((e as Error).message); }
    finally { finishSubmission(submission, isCurrent); }
  }
  async function reset() {
    setRecoveryMode(true);
    setActivity(false); pendingActivityView.current = null;
    surfaceDrafts.current.clear(); setSurfaces(false);
    setVoiceRecovery(old => old + 1);
    invalidateActions(); const isCurrent = currentGuard();
    setSnapshot(old => ({ ...old, components: [] })); setTextEffects({}); applyPreferences(defaultPreferences);
    if (!user) { setError(''); setToast(t('resetGuest')); return; }
    const owner = user.id, authTicket = authEpoch.current;
    setResetting(true); setError('');
    try {
      await api('/reset', {});
      if (uiScope.current.owner === owner && authEpoch.current === authTicket) { setSnapshot(old => ({ ...old, components: [] })); const url = new URL(location.href); url.searchParams.delete('recovery'); window.history.replaceState(null, '', url); setRecoveryMode(false); }
      void frontendObservation.report().catch(() => {});
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
    const chosen = selection ? [...selection] : [];
    // Clear the native selection before any early return or await, so choosing
    // the same file after a validation failure always emits another change.
    if (fileInput.current) fileInput.current.value = '';
    if (!chosen.length || uploading) return;
    if (!user) { requestLogin(); return; }
    const owner = user.id, authTicket = authEpoch.current;
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
    finally { if (isCurrent()) setUploading(false); }
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
  const observeInline = useCallback((value: PreviewObservation) => setInlineObservation(old => JSON.stringify(old) === JSON.stringify(value) ? old : value), []);
  function fileCard(file: FileEntry, removable = false) {
    return <div className="attachment" key={file.id}><button type="button" className="attachment-open" onClick={() => { setInlinePreview(file); setFiles(null); }} title={t('files.preview', { name: file.name })}><span className="file-type">{file.name.split('.').pop()?.slice(0, 5).toUpperCase()}</span><span><strong>{file.name}</strong><small>{new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(file.size / 1024)} KB</small></span></button>{removable && <button className="icon-button" type="button" aria-label={t('files.remove')} onClick={() => changeAttachments(old => old.filter(item => item.id !== file.id))}>×</button>}</div>;
  }
  function returnToDefault() {
    setDismissedRenderer(dismissSurfaces(user?.id || null, session, snapshot.components));
    for (const item of snapshot.components.filter(item => !item.view?.hidden && placementOf(item) === 'surface')) void changeComponentView(item, { hidden: true }).catch(() => {});
  }
  const surfaceScope = currentGuard();
  const surfaceFileSnapshot = useRef(snapshot); surfaceFileSnapshot.current = snapshot;
  const surfaceHost: SurfaceHost | undefined = renderer && user && session ? {
    revision: `${snapshot.cursor}:${drafts.current!.read(user.id, session).revision}:${orbPhase}:${locale}:${reduced}:${sending}:${uploading}`,
    async call(method, args) {
      const isCurrentSurface = () => surfaceScope() && rendererIdentity.current === rendererKey && rendererActivation.current === activeRendererActivation && uiScope.current.owner === user.id && uiScope.current.session === session;
      if (!isCurrentSurface()) throw new SurfaceError('surface_closed');
      if (method === 'snapshot') return { protocol: 1, session_id: session, revision: snapshot.cursor, phase: orbPhase, locale, reduced_motion: reduced,
        runs: snapshot.runs.map(({ id, status, phase, error_code }) => ({ id, status, phase, error_code })), help_requests: snapshot.help_requests || [], files: snapshot.files };
      if (method === 'messages') return messagePage(snapshot.messages, args);
      if (method === 'file') return readSurfaceFile(() => surfaceFileSnapshot.current, args.file_id, isCurrentSurface);
      if (method === 'draft') return drafts.current!.read(user.id, session);
      if (method === 'writeDraft') {
        const draft = drafts.current!.read(user.id, session);
        if (args.expected_revision !== draft.revision) throw new SurfaceError('draft_conflict');
        if (typeof args.text !== 'string' || args.text.length > 30000) throw new SurfaceError('invalid_request');
        editDraft({ text: args.text }); return drafts.current!.read(user.id, session);
      }
      if (method !== 'request') throw new SurfaceError('unsupported_method');
      if (args.action === 'send') {
        const draft = drafts.current!.read(user.id, session);
        if (args.expected_revision !== draft.revision) throw new SurfaceError('draft_conflict');
        if (args.mode !== undefined && args.mode !== 'queue' && args.mode !== 'interrupt') throw new SurfaceError('invalid_request');
        try { return await submit(draft.text, args.mode || 'queue', true, true); }
        catch (error) { throw new SurfaceError(error instanceof VoiceSubmissionError && error.delivery === 'unknown' ? 'delivery_unknown' : 'not_sent'); }
      }
      if (args.action === 'stop') { await stop(true); return { status: 'applied' }; }
      if (args.action === 'default') { returnToDefault(); return { status: 'requested' }; }
      if (args.action === 'preview') {
        const file = findSurfaceFile(surfaceFileSnapshot.current, args.file_id);
        if (!file) throw new SurfaceError('file_not_found');
        setInlinePreview(file); return { status: 'requested' };
      }
      if (args.action === 'open') {
        if (args.target === 'history') setHistory(true);
        else if (args.target === 'files') { await openFiles(); }
        else if (args.target === 'account') setAccount(true);
        else if (args.target === 'appearance') setAppearance(true);
        else if (args.target === 'activity') setActivity(true);
        else if (args.target === 'surfaces') setSurfaces(true);
        else throw new SurfaceError('unsupported_target');
        return { status: 'requested' };
      }
      throw new SurfaceError('unsupported_action');
    },
  } : undefined;
  function observeRendererHealth(key: string, revision: number, activation: number, state: SurfaceHealth['state']) {
    setRendererHealth(old => {
      // A late report cannot describe a replacement source or reactivated frame.
      if (rendererIdentity.current !== key || rendererRevision.current !== revision || rendererActivation.current !== activation) return old;
      const previous = old[key];
      if (previous && (previous.view_revision > revision || (previous.view_revision === revision && (previous.state === 'failed' || (previous.state === state && previous.activation === activation))))) return old;
      return { ...old, [key]: { state, view_revision: revision, activation } };
    });
  }
  function renderWidget(component: Component) {
    const rect = floatingPlacements.has(placementOf(component)) ? panelLayout[component.id] : undefined;
    const key = `${user?.id}:${session}:${component.id}:${component.version}`;
    const healthKey = rendererKey, healthRevision = component.view_revision || 0, healthActivation = activeRendererActivation;
    return <Widget key={key} surfaceHost={renderer?.id === component.id ? surfaceHost : undefined} onHealth={renderer?.id === component.id ? health => observeRendererHealth(healthKey, healthRevision, healthActivation, health.state) : undefined} component={component} sessionId={session!}
      submit={value => void submit(value, 'queue', false)} onStyleText={styleText} onNavigate={navigateFromWidget}
      onView={changeComponentView} floating={rect || undefined} fallback={rect === null} bounds={surfaceBounds} onHeight={measureWidget}
      draft={surfaceDrafts.current.get(key)} onDraft={value => { if (!surfaceDrafts.current.has(key) && surfaceDrafts.current.size >= 64) surfaceDrafts.current.delete(surfaceDrafts.current.keys().next().value!); surfaceDrafts.current.set(key, value); }} />;
  }
  const tracedRuns = new Set<string>();
  const runsById = new Map(snapshot.runs.map(run => [run.id, run]));
  const lastAnswerByRun = new Map(snapshot.messages.filter(message => message.run_id && message.role === 'assistant' && (message.content || message.attachments?.length)).map(message => [message.run_id, message.id]));
  const latestRun = snapshot.runs[0]?.id;
  const desktopMessages = snapshot.messages.filter(message => ['user', 'assistant'].includes(message.role));
  const desktopWidgets = components.filter(item => !['desktop', 'presence', 'surface', 'overlay'].includes(placementOf(item)));
  const attachedFileIds = new Set(desktopMessages.flatMap(message => message.attachments?.map(file => file.id) || []));
  const latestFiles = snapshot.files.filter(file => file.source === 'output' && !attachedFileIds.has(file.id));
  const desktopHasResult = Boolean(desktopMessages.length || latestRun || latestFiles.length || lastError || inlinePreview || desktopWidgets.length);
  const desktopResult = desktopHasResult && (desktopReplyOpen || Boolean(inlinePreview));
  const desktopQuestion = desktopMessages.findLast(message => message.role === 'user' && message.content)?.content;
  const desktopTransient = Boolean(auth || menu || history || account || files || preview || appearance || surfaces || activity);
  desktopEscape.current = () => {
    if (!desktop || desktopTransient) return false;
    if (voiceActive) { window.dispatchEvent(new Event('athena:background')); return true; }
    if (desktopLog || desktopResult) { setDesktopLog(false); setDesktopReplyOpen(false); setInlinePreview(null); return true; }
    desktopPost({ action: 'hide' }); return true;
  };
  useEffect(() => { if (desktop && (busy || waiting)) setDesktopReplyOpen(true); }, [desktop, busy, waiting]);
  const desktopExpanded = Boolean(error || serviceMessage || attached.length || uploading || chooseFiles || desktopLog || desktopResult || renderer || help || auth || history || account || files || preview || menu || appearance || surfaces || activity || voiceActive || pendingIntents.length || pendingStops.length);
  const nativeActions = useRef<(detail: Record<string, unknown>) => void>(() => {});
  nativeActions.current = detail => {
    const action = detail.action;
    if (action === 'state') {
      const current = uiScope.current;
      const scope = current.owner && current.session ? `${current.owner}:${current.session}` : '';
      const state = readDesktopState(detail.desktop);
      if (state && detail.scope === scope) {
        setDesktopState(old => old?.scope === scope && JSON.stringify(old.value) === JSON.stringify(state) ? old : { scope, value: state });
        setDesktopPresented(state.presented);
      }
      return;
    }
    if (action === 'hidden') { setDesktopPresented(false); window.dispatchEvent(new Event('athena:background')); return; }
    if (action === 'show') { setDesktopPresented(true); requestAnimationFrame(() => { if (!document.querySelector('[aria-modal="true"]')) input.current?.focus({ preventScroll: true }); }); return; }
    if (action === 'menu') { setMenu(true); return; }
    if (action === 'history') { setHistory(true); return; }
    if (action === 'new') { setMenu(false); setHistory(false); navigate(null); requestAnimationFrame(() => input.current?.focus({ preventScroll: true })); return; }
    if (action === 'stop') { void stop(); return; }
    if (action === 'reset') { void reset(); return; }
    if (action === 'submit' || action === 'navigate') {
      if (!interactionVisible() || recoveryMode || resetting) return;
      const component = snapshot.components.find(item => item.id === detail.component_id && item.version === detail.version && !item.view?.hidden);
      const current = uiScope.current;
      if (!user || !session || current.owner !== user.id || current.session !== session || detail.scope !== `${current.owner}:${current.session}` || !component || placementOf(component) !== 'desktop') return;
      if (action === 'submit' && typeof detail.text === 'string' && detail.text.trim() && detail.text.length <= 30000) void submit(detail.text);
      if (action === 'navigate' && typeof detail.session_id === 'string' && /^[\w-]{1,64}$/.test(detail.session_id)) navigate(detail.session_id);
    }
  };
  useEffect(() => {
    if (!desktop) return;
    document.documentElement.dataset.desktop = 'companion';
    const receive = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail && typeof detail === 'object') nativeActions.current(detail); };
    window.addEventListener('athena:desktop', receive);
    desktopPost({ action: 'observe' });
    return () => { window.removeEventListener('athena:desktop', receive); delete document.documentElement.dataset.desktop; delete document.documentElement.dataset.desktopPresented; };
  }, [desktop]);
  const desktopLayout = useRef({ expanded: false, modal: false });
  desktopLayout.current = { expanded: desktopExpanded, modal: Boolean(desktopLog || renderer || help || auth || history || account || files || preview || menu || appearance || surfaces || activity) };
  const scene = JSON.stringify(desktopScene(user?.id || null, session, snapshot.components, recoveryMode || resetting));
  useEffect(() => { if (desktop) desktopPost(JSON.parse(scene)); }, [desktop, scene]);
  useEffect(() => {
    if (!desktop || !appRoot.current) return;
    let scheduled = 0, lastSize = '', lastRegions = '';
    const watched = new Set<Element>();
    const report = () => { if (!scheduled) scheduled = window.setTimeout(() => {
      scheduled = 0;
      const app = appRoot.current;
      if (!app) return;
      // Autofocus may scroll the shell before its native panel has expanded.
      // Reading areas own their scroll; the transparent outer canvas does not.
      if (app.scrollTop || app.scrollLeft) app.scrollTo(0, 0);
      const result = app.querySelector<HTMLElement>('.companion-result:not([hidden])');
      const content = result ? Math.max(0, ...[...result.children].map(child => child.getBoundingClientRect().bottom))
        - result.getBoundingClientRect().top + result.scrollTop + 20 : 0;
      const dialog = app.querySelector<HTMLElement>('[aria-modal="true"]');
      const size = { action: 'resize', mode: desktopLayout.current.expanded ? 'expanded' : 'compact',
        height: desktopLayout.current.modal ? (dialog?.classList.contains('menu-dialog') ? Math.min(680, Math.max(400, dialog.scrollHeight + 48)) : 680) : Math.min(680, Math.max(260, Math.ceil(content
          + (app.querySelector<HTMLElement>('.companion-presence')?.offsetHeight || 0)
          + (app.querySelector<HTMLElement>('.composer-area')?.offsetHeight || 0) + 26))) };
      const signature = JSON.stringify(size);
      if (signature !== lastSize) { desktopPost(size); lastSize = signature; }
      const regions = companionHitRegions(), regionSignature = JSON.stringify(regions);
      if (regionSignature !== lastRegions) { desktopPost({ action: 'hitRegions', regions }); lastRegions = regionSignature; }
      const next = new Set(app.querySelectorAll('.companion-result:not([hidden]),.composer-area,.companion-presence'));
      for (const node of watched) if (!next.has(node)) { observer.unobserve(node); watched.delete(node); }
      for (const node of next) if (!watched.has(node)) { watched.add(node); observer.observe(node); }
    }, 80); };
    const observer = new ResizeObserver(report); observer.observe(appRoot.current);
    const mutations = new MutationObserver(report); mutations.observe(appRoot.current, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'class', 'style', 'open', 'data-desktop-expanded'] });
    window.addEventListener('resize', report); document.addEventListener('focusin', report); report();
    return () => { observer.disconnect(); mutations.disconnect(); window.removeEventListener('resize', report); document.removeEventListener('focusin', report); clearTimeout(scheduled); };
  }, [desktop]);
  return <div ref={appRoot} className={`app${desktop ? ' desktop-companion' : ''}`} data-desktop-expanded={desktopExpanded} data-desktop-modal={desktop && desktopTransient} data-renderer={renderer ? "component" : "default"} data-reading={reading} data-phase={orbPhase}>
    {desktop && <div className="companion-presence" data-desktop-hit>
      {presence && !resetting ? <div className="custom-presence">{renderWidget(presence)}</div> : <button className="companion-avatar" type="button" aria-expanded={desktopResult} aria-label={desktopHasResult ? t(desktopResult ? 'desktop.collapse' : 'desktop.reopen') : t('desktop.focus')} onClick={() => { if (desktopHasResult) { setDesktopReplyOpen(!desktopResult); setInlinePreview(null); } else input.current?.focus(); }}><CompanionPortrait phase={orbPhase} listening={voiceActive} /></button>}
      <span className="companion-status" role="status" title={desktopQuestion}>{busy || waiting ? t(waitingAgents && !waiting ? 'agents.waiting' : orbPhase === 'responding' ? 'responding' : orbPhase === 'idle' ? 'status.ready' : `status.${orbPhase}`) : desktopQuestion ? t('desktop.continue') : t('desktop.prompt')}</span>
      <button className="companion-menu" aria-label={t('menu')} title={t('menu')} onClick={() => setMenu(true)}><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="4" cy="10" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="16" cy="10" r="1"/></svg></button>
      <button className="companion-hide" aria-label={t('desktop.hide')} title={t('desktop.hide')} onClick={() => desktopPost({ action: 'hide' })}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 6 8 8m0-8-8 8" /></svg></button>
    </div>}
    {(!desktop || desktopLog) && <header className="topbar">
      <button className="brand" onClick={() => { navigate(null); }} aria-label={t('newChat')}><AthenaMark compact /><span className="brand-wordmark">athena</span></button>
      <nav aria-label={t('chatControls')}>{desktop && <button className="quiet" onClick={() => setDesktopLog(false)}>{t('desktop.back')}</button>}{renderer && <><button className="quiet" onClick={returnToDefault}>{t('backToChat')}</button><button className="icon-button" aria-label={t('files.attach')} onClick={() => fileInput.current?.click()}><Icon name="files" /></button><button className="quiet" onClick={() => void stop()}>{t('stop')}</button><button className="icon-button" aria-label={t('reset')} onClick={() => void reset()}><Icon name="reset" /></button></>}{!preview && !auth && !account && !appearance && !files && !history && !menu && !surfaces && activityControl()}{workspace && !resetting && <button className="quiet workspace-return" onClick={() => { void Promise.all(workspaces.filter(item => !item.view?.hidden).map(item => changeComponentView(item, { hidden: true }))).catch(() => {}); }}>{t('backToChat')}</button>}<button className="icon-button menu-button" title={t('menu')} aria-label={t('menu')} onClick={() => setMenu(true)}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></svg></button></nav>
    </header>}
    {desktop && !desktopLog ? <>
      {!resetting && components.filter(item => placementOf(item) === 'overlay').map(item => <div className="scene-overlay" key={`${item.id}-${item.version}`} inert={desktopTransient}>{renderWidget(item)}</div>)}
      {renderer ? <section className="session-surface companion-result" data-desktop-hit aria-label={renderer.title}>{renderWidget(renderer)}</section> : desktopHasResult && <main className="companion-result scroll-area" hidden={!desktopResult} data-desktop-hit ref={conversationScroll.ref} {...conversationScroll.handlers}>
        <div className="companion-result-heading"><button className="quiet" onClick={() => { setDesktopReplyOpen(false); setInlinePreview(null); }}>{t('desktop.collapse')}</button></div>
        {desktopMessages.map(message => {
          const run = message.run_id ? runsById.get(message.run_id) : undefined;
          const trace = message.run_id && !tracedRuns.has(message.run_id);
          if (trace) tracedRuns.add(message.run_id!);
          const visible = Boolean(message.content || message.attachments?.length);
          return <Fragment key={message.id}>
            {message.help_reply ? <HelpReceipt reply={message.help_reply} /> : visible && <article className={`message ${message.role}`} data-run-id={message.run_id} data-activity-result={run?.status === 'complete' && lastAnswerByRun.get(message.run_id) === message.id ? message.run_id : undefined}>
              <div dir="auto" data-message-id={message.id} data-message-role={message.role}>{message.role === 'assistant' ? <MessageBody content={message.content} effect={effectFor(message.id)} /> : <span className="user-text"><StyledText text={message.content} effect={effectFor(message.id)} /></span>}</div>
              {message.attachments?.map(file => file.source === 'output' && canDisplayArtifact(file) ? <ArtifactResult key={file.id} file={file} selected={inlinePreview?.id === file.id} observe={inlinePreview?.id === file.id ? observeInline : undefined} /> : fileCard(file))}
              {message.status === 'queued' && <span className="message-note">{t('queued')}</span>}
            </article>}
            {trace && session && <RunTrace sessionId={session} runId={message.run_id!} revision={snapshot.cursor} phase={run?.phase} status={run?.status} />}
          </Fragment>;
        })}
        {snapshot.runs.filter(run => !tracedRuns.has(run.id)).map(run => session && <RunTrace key={run.id} sessionId={session} runId={run.id} revision={snapshot.cursor} phase={run.phase} status={run.status} />)}
        {latestFiles.length > 0 && <section className="output-files" aria-label={t('files.outputs')}>{latestFiles.map(file => canDisplayArtifact(file) ? <ArtifactResult key={file.id} file={file} selected={inlinePreview?.id === file.id} observe={inlinePreview?.id === file.id ? observeInline : undefined} /> : fileCard(file))}</section>}
        {inlinePreview && !latestFiles.some(file => file.id === inlinePreview.id) && <ArtifactResult file={inlinePreview} selected close={() => setInlinePreview(null)} observe={observeInline} />}
        {!resetting && desktopWidgets.map(item => renderWidget(item))}
        {lastError && <p className="notice" role="status">{lastError}</p>}
        <div ref={end} />
      </main>}
    </> : renderer ? <section className="session-surface" aria-label={renderer.title}>{renderWidget(renderer)}</section> : <div className="workspace-layout">
      {!resetting && components.some(component => placementOf(component) === 'sidebar-start') && <aside className="surface-sidebar" data-side="start">{components.filter(component => placementOf(component) === 'sidebar-start').map(component => renderWidget(component))}</aside>}
      <div className="reading-pane">
    {!workspace && <div className="agent-presence">{presence && !resetting ? <div className="custom-presence">{renderWidget(presence)}</div> : <Orb phase={orbPhase} />}<span className="presence-label" role="status">{t(waitingAgents && !waiting ? 'agents.waiting' : orbPhase === 'responding' ? 'responding' : orbPhase === 'idle' ? 'status.ready' : `status.${orbPhase}`)}</span></div>}
    {!resetting && floating.filter(component => panelLayout[component.id]).map(component => renderWidget(component))}
    {!resetting && components.filter(component => placementOf(component) === 'overlay').map(component => <div className="scene-overlay" inert={Boolean(menu || history || auth || account || appearance || files || preview || surfaces || activity)} key={`${component.id}-${component.version}`}>{renderWidget(component)}</div>)}
    <main ref={conversationScroll.ref} className={`scroll-area${workspace ? ' has-workspace' : ''}`} {...conversationScroll.handlers}>
      {!resetting && components.filter(component => placementOf(component) === 'banner').map(component => <div className="agent-banner" role="status" key={`${component.id}-${component.version}`}>{renderWidget(component)}</div>)}
      {workspace && !resetting && <section className="workspace-panel" data-app-view="workspace">{renderWidget(workspace)}</section>}
      <div className="conversation" hidden={Boolean(workspace)}>
        <article className="message assistant greeting"><div>{t('greeting')}</div></article>
        {snapshot.messages.map(message => {
          const trace = message.run_id && !tracedRuns.has(message.run_id);
          if (trace) tracedRuns.add(message.run_id!);
          const origin = taskOrigin(message) || agentOrigin(message);
          const run = message.run_id ? runsById.get(message.run_id) : undefined;
          const visible = Boolean(message.content || message.attachments?.length);
          return <Fragment key={message.id}>
            {trace && message.role === 'assistant' && <div data-run-id={message.run_id} tabIndex={-1} data-activity-result={run && ['failed', 'interrupted', 'cancelled'].includes(run.status) ? message.run_id : undefined}><RunTrace sessionId={session!} runId={message.run_id!} revision={snapshot.cursor} phase={run?.phase} status={run?.status} /></div>}
            {message.help_reply && <><HelpReceipt reply={message.help_reply} />{Boolean(message.attachments?.length) && <div className="help-response-files">{message.attachments?.map(file => fileCard(file))}</div>}</>}
            {origin?.kind === 'agent_results' && <AgentResults origin={origin} />}
            {visible && !message.help_reply && !origin && <article className={`message ${message.role}`} data-run-id={message.run_id} tabIndex={-1} data-activity-result={run?.status === 'complete' && lastAnswerByRun.get(message.run_id) === message.id ? message.run_id : undefined}>
              <div dir="auto" data-message-id={message.id} data-message-role={message.role}>{message.role === 'assistant' ? <MessageBody content={message.content} effect={effectFor(message.id)} /> : <span className="user-text" data-message-text><StyledText text={message.content} effect={effectFor(message.id)} /></span>}{message.attachments?.map(file => fileCard(file))}</div>
              {message.status === 'queued' && <span className="message-note">{t('queued')}</span>}
              {['interrupted', 'cancelled', 'failed'].includes(message.status) && <span className="message-note">{message.status === 'cancelled' ? t('cancelled') : t('stopped')}</span>}
            </article>}
            {trace && message.role === 'user' && <div data-run-id={message.run_id} tabIndex={-1} data-activity-result={run && ['failed', 'interrupted', 'cancelled'].includes(run.status) ? message.run_id : undefined}><RunTrace sessionId={session!} runId={message.run_id!} revision={snapshot.cursor} phase={run?.phase} status={run?.status} origin={origin} /></div>}
            {trace && Boolean(snapshot.agents?.some(job => job.root_run_id === message.run_id)) && <AgentActivity sessionId={session!} revision={snapshot.cursor} jobs={snapshot.agents!.filter(job => job.root_run_id === message.run_id)} />}
          </Fragment>;
        })}
        {!resetting && components.filter(component => ['inline', 'workspace'].includes(placementOf(component)) || (floatingPlacements.has(placementOf(component)) && !panelLayout[component.id])).map(component => renderWidget(component))}
        {snapshot.tasks.length > 0 && <section className="tasks">{snapshot.tasks.map(task => <div className="task" key={task.id}><div><strong>{task.title}</strong><span>{task.status === 'paused' ? t('paused') : task.last_error ? t('watchRetry') : task.condition_command ? t('watching') : new Date(task.due_at).toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span></div>{task.status !== 'paused' && <button className="quiet" onClick={() => void api(`/sessions/${session}/tasks/${task.id}/pause`, {}).catch(e => setError(e.message))}>{t('pause')}</button>}</div>)}</section>}
        {snapshot.files?.some(file => file.source === 'output') && <section className="output-files" aria-label={t('files.outputs')}>{snapshot.files.filter(file => file.source === 'output').map(file => canDisplayArtifact(file) ? <ArtifactResult key={file.id} file={file} selected={inlinePreview?.id === file.id} observe={inlinePreview?.id === file.id ? observeInline : undefined} /> : fileCard(file))}</section>}
        {inlinePreview && !snapshot.files.some(file => file.source === 'output' && file.id === inlinePreview.id && canDisplayArtifact(file)) && <ArtifactResult key={inlinePreview.id} file={inlinePreview} selected close={() => setInlinePreview(null)} observe={observeInline} />}
        {lastError && <p className="notice" role="status">{lastError}</p>}<div ref={end} />
      </div>
    </main>
      </div>
      {!resetting && components.some(component => placementOf(component) === 'sidebar-end') && <aside className="surface-sidebar" data-side="end">{components.filter(component => placementOf(component) === 'sidebar-end').map(component => renderWidget(component))}</aside>}
    </div>}
    {(renderer || workspace) && inlinePreview && <div className="surface-artifact"><ArtifactResult key={inlinePreview.id} file={inlinePreview} selected close={() => setInlinePreview(null)} observe={observeInline} /></div>}
    <footer className="composer-area" ref={composer} data-desktop-hit={desktop || undefined}><div className="composer-wrap">
      {recoveryMode && <p className="notice" role="status">{t('surface.recovery')}</p>}
      {rendererUnavailable && <p className="notice" role="status">{t('componentUnavailable')}</p>}
      {help && <UserHelp key={help.id} request={help} busy={sending || uploading} answer={answerHelp} answers={helpAnswers}
        change={answers => { if (!helpDraftScope) return; helpDrafts.current!.write(helpDraftScope, answers); setHelpDraftRevision(old => old + 1); }}
        select={value => { editDraft({ text: value }); input.current?.focus(); }} />}
      {busy && !waiting && (!desktop || reconnecting || queued > 0) && <div className="activity" role="status"><span className="typing"><i /><i /><i /></span>{reconnecting ? t('reconnecting') : waitingAgents ? t('agents.waiting') : orbPhase === 'executing' ? t('working') : orbPhase === 'responding' ? t('responding') : t('thinking')}{queued > 0 && <small>{t('queuedCount', { count: queued })}</small>}</div>}
      {pendingStops.length > 0 && <section className="submission-recovery notice" data-stop-recovery role="region" aria-label={t('send.stopUnknown')}>
        <p role="status">{t('send.stopUnknown')}</p>
        {pendingStops.map(intent => <div key={intent.id} data-stop-id={intent.id}>
          <button className="quiet" type="button" data-stop-check onClick={() => void reconcileStop(intent)}>{t('send.check')}</button>
          <button className="quiet" type="button" data-stop-retry disabled={intents.current!.needsStopReceipt(intent.owner, intent.session)} onClick={() => void stop(false, intent)}>{t('send.retryStop')}</button>
        </div>)}
      </section>}
      {pendingIntents.length > 0 && !sending && <section className="submission-recovery notice" data-submission-recovery role="region" aria-label={t('send.pending')}>
        <p role="status">{t('send.unknown')}</p>
        {pendingIntents.map(intent => <div key={intent.id} data-submission-id={intent.id}>
          <p className="submission-excerpt" dir="auto">{intent.text || t('files.attached')}{intent.file_ids.length > 0 && <small> · {t('files.attached')} ({intent.file_ids.length})</small>}</p>
          <button className="quiet" type="button" data-submission-check onClick={() => void reconcileSubmission(intent)}>{t('send.check')}</button>
          <button className="quiet" type="button" data-submission-retry disabled={resetting || uploading} onClick={() => void submit(intent.text, intent.mode, false, false, intent)}>{t('send.retryOriginal')}</button>
        </div>)}
      </section>}
      {error && error !== t('send.stopUnknown') && <div className="notice" role="alert">{error}{!ready && <button className="quiet" onClick={() => void loadAccount()}>{t('retry')}</button>}</div>}
      {!error && serviceMessage && <div className="notice" role="status">{serviceMessage}</div>}
      {attached.length > 0 && <div className="attachment-tray" aria-label={t('files.attached')}>{attached.map(file => fileCard(file, true))}</div>}
      {uploading && <p className="activity" role="status">{t('files.uploading')}</p>}
      {chooseFiles && <div className="activity"><button className="quiet" type="button" onClick={() => { setChooseFiles(false); fileInput.current?.click(); }}>{t('files.attach')}</button><button className="icon-button" type="button" aria-label={t('close')} onClick={() => setChooseFiles(false)}>×</button></div>}
      <input type="file" multiple hidden ref={fileInput} onChange={event => void upload(event.target.files)} />
      <form className={`composer${renderer ? ' surface-call-controls' : ''}`} data-task-active={busy || waiting || sending} onSubmit={event => { event.preventDefault(); void submit(); }}>
        {!renderer && <><button className="icon-button attach-button" type="button" title={t('files.attach')} aria-label={t('files.attach')} disabled={uploading || sending} onClick={() => user ? fileInput.current?.click() : requestLogin()}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2m-7 13 8-8" /></svg></button>
        <textarea dir="auto" ref={input} aria-label={t('message')} placeholder={t(help ? help.questions.length === 1 ? 'help.placeholder' : 'queue' : 'placeholder')} value={text} rows={1} maxLength={30000} onChange={event => { editDraft({ text: event.target.value }); }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(); } }} />
        </>}<div className="send-actions" key="actions">
          <VoiceCall key={voiceRecovery} sessionId={session} ownerId={user?.id || null} messages={voiceMessages} busy={busy} disabled={!ready || resetting || sending || uploading || pendingIntents.length > 0 || pendingStops.length > 0} hasPendingDelivery={value => Boolean(user && session && intents.current!.list(user.id, session).some(intent => !intent.draft && intent.text === value))} ensureSession={ensureVoiceSession} submit={submitVoice} interrupt={interruptVoice} onActiveChange={setVoiceActive} />
          {!renderer && <ComposerActions busy={busy} sending={sending} questions={help?.questions.length || 0} text={text} attachmentCount={attached.length}
            disabled={sending || uploading || !ready || resetting} stop={() => void stop()} interrupt={() => void submit(text, 'interrupt')} />}
        </div>
      </form>
    </div></footer>
    {surfaces && <div className="overlay centered" onMouseDown={event => { if (event.target === event.currentTarget) setSurfaces(false); }}><section className="dialog surface-dialog" data-app-view="surfaces" role="dialog" aria-modal="true" aria-label={t('surface.all')}><div className="dialog-top"><h2>{t('surface.all')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setSurfaces(false)}>×</button></div><div className="surface-list">{snapshot.components.map(component => <div className="surface-row" key={component.id}><button className="surface-open" onClick={() => { adjustComponent(component, { hidden: false }); setSurfaces(false); }}><span>{component.title}</span><small>{t(component.view?.hidden ? 'surface.hidden' : 'surface.visible')}</small></button><ComponentControls component={component} change={view => adjustComponent(component, view)} /></div>)}</div></section></div>}
    {toast && <div className="toast" role="status">{toast}</div>}
    {Boolean(user?.developer_mode) && <ErrorBoundary panel close={() => void developer(false)}><DeveloperPanel session={session} close={() => void developer(false)} /></ErrorBoundary>}
    {menu && <div className="overlay centered" onMouseDown={event => { if (event.target === event.currentTarget) setMenu(false); }}><section className="dialog menu-dialog" data-app-view="menu" role="dialog" aria-modal="true" aria-label={t('menu')}><div className="dialog-top"><h2>Athena</h2><button className="icon-button" aria-label={t('close')} onClick={() => setMenu(false)}>×</button></div><div className="menu-items">{desktop && <button onClick={() => { setMenu(false); navigate(null); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>{t('newChat')}</button>}<button onClick={() => { setMenu(false); user ? setHistory(true) : requestLogin(); }}><Icon name="history" />{t('history')}</button><button onClick={() => { setMenu(false); void openFiles(); }}><Icon name="files" />{t('files')}</button>{snapshot.components.length > 0 && <button onClick={() => { setMenu(false); setSurfaces(true); }}><span className="menu-glyph">◇</span>{t('surface.all')}</button>}<button onClick={() => { setMenu(false); setAppearance(true); }}><span className="menu-glyph">Aa</span>{t('appearance')}</button><button onClick={() => { setMenu(false); user ? setAccount(true) : requestLogin(); }}><Icon name="user" />{user ? t('account') : t('signIn')}</button><button disabled={resetting} onClick={() => { setMenu(false); void reset(); }}><Icon name="reset" />{t('reset')}</button></div></section></div>}
    {appearance && <div className="overlay centered"><section className="dialog" data-app-view="appearance" role="dialog" aria-modal="true" aria-label={t('appearance')}><div className="dialog-top"><h2>{t('appearance')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setAppearance(false)}>×</button></div><label htmlFor="language-choice">{t('language')}</label><select id="language-choice" value={choice} onChange={event => void changePreferences({ ui_language: event.target.value })}><option value="system">{t('systemLanguage')}</option>{languages.map(language => <option key={language.tag} value={language.tag} lang={language.tag}>{language.name}</option>)}</select>{(['ui_font', 'ui_palette'] as const).map(key => <label key={key}>{t(key === 'ui_font' ? 'appearance.font' : 'appearance.palette')}<select value={preferences[key]} onChange={event => void changePreferences({ [key]: event.target.value })}>{key === 'ui_palette' && preferences.ui_palette.startsWith('#') && <option value={preferences.ui_palette}>{preferences.ui_palette}</option>}{(key === 'ui_font' ? ['system', 'humanist', 'serif', 'mono'] as const : ['dream', 'rose', 'ocean', 'forest'] as const).map(value => <option key={value} value={value}>{t(`appearance.${value}`)}</option>)}</select></label>)}<label className="toggle-row"><span>{t('reduceMotion')}</span><input type="checkbox" checked={reduced} onChange={event => void changePreferences({ reduced_motion: event.target.checked ? 'reduce' : 'system' })} /></label></section></div>}
    {history && <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) setHistory(false); }}><aside className="drawer" data-app-view="history" role="dialog" aria-modal="true" aria-label={t('history')}><div className="dialog-top"><h2>{t('conversations')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setHistory(false)}>×</button></div><button className="primary wide" onClick={() => { navigate(null); setHistory(false); }}>{t('newChat')}</button><div className="session-list">{sessions.map(item => <button className={`session ${item.id === session ? 'selected' : ''}`} key={item.id} onClick={() => { navigate(item.id); setHistory(false); }}><span>{item.title === '新的事情' ? t('newChat') : item.title}</span>{item.pending > 0 && <small>•••</small>}</button>)}</div>{session && <button className="quiet wide" disabled={busy || waiting} onClick={() => void forkSession()}>{t('fork')}</button>}</aside></div>}
    {auth && <div className="overlay centered"><form className="dialog" data-app-view="authentication" role="dialog" aria-modal="true" aria-label={auth === 'login' ? t('signIn') : t('createAccount')} onSubmit={async event => {
      event.preventDefault(); const ticket = ++authEpoch.current; invalidateActions(); setSending(true); setError('');
      try { const result = await api<{ user: User; preferences: Preferences }>(`/auth/${auth}`, { username, password }); if (authEpoch.current === ticket) { acceptUser(result.user, result.preferences); setHasUsers(true); } }
      catch (e) { if (authEpoch.current === ticket) setError((e as Error).message); } finally { if (authEpoch.current === ticket) setSending(false); }
    }}><div className="dialog-top"><h2>{auth === 'login' ? t('welcomeBack') : t('niceToMeet')}</h2><button className="icon-button" type="button" aria-label={t('close')} onClick={() => { setAuth(null); setPassword(''); }}>×</button></div><label htmlFor="username">{t('username')}</label><input id="username" autoComplete="username" autoFocus aria-describedby="username-hint" value={username} onChange={event => setUsername(event.target.value)} required minLength={2} maxLength={40} /><p id="username-hint" className="field-hint">{t('usernameHint')}</p><label htmlFor="password">{t('password')}</label><input id="password" type="password" autoComplete={auth === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={8} maxLength={128} placeholder={t('passwordHint')} />{error && <p className="notice">{error}</p>}<button className="primary wide" disabled={sending}>{sending ? t('wait') : auth === 'login' ? t('signIn') : t('createAccount')}</button><button type="button" className="quiet wide" onClick={() => { setAuth(auth === 'login' ? 'register' : 'login'); setError(''); }}>{auth === 'login' ? t('needAccount') : t('haveAccount')}</button></form></div>}
    {account && user && <div className="overlay centered"><section className="dialog" data-app-view="account" role="dialog" aria-modal="true" aria-label={t('account')}><div className="dialog-top"><h2>{user.display_name || user.username}</h2><button className="icon-button" aria-label={t('close')} onClick={closeAccount}>×</button></div><AccountProfile user={user} onChange={next => { if (uiScope.current.owner === next.id) setUser(next); }} passwordRequested={passwordRequested} onPasswordChanged={closeAccount} /><label className="toggle-row"><span>{t('developerMode')}</span><input type="checkbox" checked={Boolean(user.developer_mode)} onChange={event => void developer(event.target.checked)} /></label>{error && <p className="notice">{error}</p>}<button className="quiet wide logout" onClick={() => void signOut()}>{t('signOut')}</button></section></div>}
    {files && <div className="overlay centered"><section className="dialog file-dialog" data-app-view="files" role="dialog" aria-modal="true" aria-label={t('files')}><div className="dialog-top"><h2>{t('files')}</h2><button className="icon-button" aria-label={t('close')} onClick={() => setFiles(null)}>×</button></div>{files.length === 0 ? <p className="empty-note">{t('filesEmpty')}</p> : <div className="file-list">{files.map(file => fileCard(file))}</div>}</section></div>}
    {preview && <div className="overlay centered preview-overlay"><div className="preview-dialog" data-app-view="preview" role="dialog" aria-modal="true" aria-label={preview.name}>{activityControl(true)}<FilePreview key={preview.id} file={preview} onObservation={value => setPreviewObservation(old => JSON.stringify(old) === JSON.stringify(value) ? old : value)} onClose={() => setPreview(null)} /></div></div>}
  </div>;
}
