import type { AppAction, Component } from './api';
import type { SurfaceState } from './surface-runtime';
import { placementOf } from './component-layout.ts';

export type PreviewObservation = { file_id: string; status: 'loading' | 'ready' | 'failed'; error_code?: 'preview_unavailable' };
export type FrontendObservation = {
  surface?: SurfaceState;
  client_id: string; revision: number; session_id: string | null; foreground: boolean;
  viewport: { width: number; height: number }; open_views: string[];
  preview: PreviewObservation | null; workspace: { id: string; version: number } | null;
  components: { id: string; version: number; view_revision: number; instance: string; placement: string;
    rect: { x: number; y: number; width: number; height: number }; visible: boolean;
    state: 'unobserved' | 'mounted' | 'failed'; error_code?: string }[];
  waiting: { kind: 'user_help' | 'file_selection' | 'upload' | 'authentication'; id?: string }[];
  errors: { source: 'connection' | 'ui' | 'preview' | 'component'; code: string }[];
  has_unsent_changes: boolean; attachment_ids: string[];
};
export type FrontendFacts = {
  surface?: SurfaceState;
  sessionId: string | null; components: Component[]; preview: PreviewObservation | null;
  helpIds: string[]; choosingFiles: boolean; uploading: boolean; reconnecting: boolean;
  hasError: boolean; hasUnsentChanges: boolean; attachmentIds: string[];
};
const views = new Set(['menu', 'history', 'files', 'account', 'appearance', 'developer', 'password', 'workspace', 'preview', 'surfaces', 'authentication', 'activity']);
const componentErrors = new Set(['script_error', 'unhandled_rejection', 'blocked_resource', 'empty_document', 'load_timeout']);
const bounded = (value: number, min = -32768, max = 32768) => Number.isFinite(value) ? Math.max(min, Math.min(max, Math.round(value))) : 0;

/** Project only host-owned metadata. No DOM text, form values or iframe access. */
export function observeFrontend(document: Document, facts: FrontendFacts): Omit<FrontendObservation, 'client_id' | 'revision'> {
  const window = document.defaultView!;
  const viewport = { width: bounded(window.visualViewport?.width || window.innerWidth, 0, 16384), height: bounded(window.visualViewport?.height || window.innerHeight, 0, 16384) };
  const open = new Set<string>();
  for (const node of document.querySelectorAll<HTMLElement>('[data-app-view]')) {
    const name = node.dataset.appView;
    if (name && views.has(name) && node.getBoundingClientRect().width > 0) open.add(name);
  }
  if (document.querySelector('.developer-panel')) open.add('developer');
  if (open.has('account') && document.querySelector('.account-profile input[type="password"]')) open.add('password');
  const nodes = new Map([...document.querySelectorAll<HTMLElement>('.widget[data-component-id]')].map(node => [node.dataset.componentId, node]));
  const foreground = !document.hidden;
  const covered = [...open].some(name => !['developer', 'workspace'].includes(name));
  const components = facts.components.slice(0, 32).map(component => {
    const node = nodes.get(component.id), box = node?.getBoundingClientRect();
    const error = node?.dataset.componentError;
    const state = node?.dataset.componentState === 'failed' ? 'failed' : node?.dataset.componentState === 'mounted' ? 'mounted' : 'unobserved';
    return { id: component.id, version: component.version, view_revision: component.view_revision || 0,
      instance: node?.dataset.componentInstance || 'unmounted', placement: node?.dataset.placement || placementOf(component),
      rect: { x: bounded(box?.x || 0), y: bounded(box?.y || 0), width: bounded(box?.width || 0, 0), height: bounded(box?.height || 0, 0) },
      visible: Boolean(foreground && !covered && !component.view?.hidden && box && box.width > 0 && box.height > 0 && box.bottom > 0 && box.right > 0 && box.top < viewport.height && box.left < viewport.width),
      state: state as 'unobserved' | 'mounted' | 'failed', ...(error && componentErrors.has(error) ? { error_code: error } : {}) };
  });
  const activeWorkspace = document.querySelector<HTMLElement>('.workspace-panel .widget[data-component-id]')?.dataset.componentId;
  const workspace = components.find(component => component.id === activeWorkspace);
  const inlinePreview = [...document.querySelectorAll<HTMLElement>('[data-inline-preview-id]')].some(node => node.dataset.inlinePreviewId === facts.preview?.file_id && node.getBoundingClientRect().width > 0);
  const preview = (open.has('preview') || inlinePreview) && facts.preview ? { file_id: facts.preview.file_id, status: facts.preview.status,
    ...(facts.preview.status === 'failed' ? { error_code: 'preview_unavailable' as const } : {}) } : null;
  const waiting: FrontendObservation['waiting'] = facts.helpIds.slice(0, 5).map(id => ({ kind: 'user_help', id }));
  if (facts.choosingFiles) waiting.push({ kind: 'file_selection' });
  if (facts.uploading) waiting.push({ kind: 'upload' });
  if (open.has('authentication') || open.has('password')) waiting.push({ kind: 'authentication' });
  const errors: FrontendObservation['errors'] = [];
  if (facts.reconnecting) errors.push({ source: 'connection', code: 'connection_lost' });
  if (facts.hasError || document.querySelector('.recovery-panel')) errors.push({ source: 'ui', code: 'ui_error' });
  if (preview?.status === 'failed') errors.push({ source: 'preview', code: 'preview_unavailable' });
  for (const component of components) if (component.error_code && errors.length < 8) errors.push({ source: 'component', code: component.error_code });
  return { ...(facts.surface ? { surface: facts.surface } : {}), session_id: facts.sessionId, foreground, viewport, open_views: [...open].sort(), preview,
    workspace: workspace ? { id: workspace.id, version: workspace.version } : null, components, waiting: waiting.slice(0, 8), errors,
    has_unsent_changes: facts.hasUnsentChanges, attachment_ids: facts.attachmentIds.slice(0, 10) };
}

type ReportSubmission = { observation: FrontendObservation; promise: Promise<FrontendObservation | null>;
  resolve: (value: FrontendObservation | null) => void; reject: (error: unknown) => void };

/** Keep one in-flight report and the latest unsent state, never a history backlog. */
export class FrontendReporter {
  private revision = 0;
  private signature = '';
  private sentRevision = 0;
  private inFlight: ReportSubmission | null = null;
  private queued: ReportSubmission | null = null;
  private stopped = false;
  private clientId: string;
  private read: () => Omit<FrontendObservation, 'client_id' | 'revision'>;
  private send: (observation: FrontendObservation) => Promise<unknown>;
  constructor(clientId: string, read: () => Omit<FrontendObservation, 'client_id' | 'revision'>,
    send: (observation: FrontendObservation) => Promise<unknown>) { this.clientId = clientId; this.read = read; this.send = send; }
  snapshot(): FrontendObservation {
    const state = this.read(), signature = JSON.stringify(state);
    if (signature !== this.signature) { this.signature = signature; this.revision++; }
    return { client_id: this.clientId, revision: this.revision, ...state };
  }
  report(force = false): Promise<FrontendObservation | null> {
    if (this.stopped) return Promise.resolve(null);
    const observation = this.snapshot();
    if (this.queued) { this.queued.observation = observation; return this.queued.promise; }
    if (this.inFlight?.observation.revision === observation.revision) return this.inFlight.promise;
    if (!force && observation.revision === this.sentRevision) return Promise.resolve(observation);
    let resolve!: ReportSubmission['resolve'], reject!: ReportSubmission['reject'];
    const promise = new Promise<FrontendObservation | null>((success, failure) => { resolve = success; reject = failure; });
    this.queued = { observation, promise, resolve, reject };
    void Promise.resolve().then(() => this.drain());
    return promise;
  }
  private async drain() {
    if (this.stopped || this.inFlight || !this.queued) return;
    const submission = this.queued; this.queued = null; this.inFlight = submission;
    try {
      // Layout may have changed since the debounced caller enqueued this report.
      submission.observation = this.snapshot();
      await this.send(submission.observation);
      this.sentRevision = submission.observation.revision;
      submission.resolve(this.stopped ? null : submission.observation);
    } catch (error) { submission.reject(error); }
    finally { this.inFlight = null; if (this.queued) void Promise.resolve().then(() => this.drain()); }
  }
  stop() {
    this.stopped = true;
    this.queued?.resolve(null); this.queued = null;
  }
}

/** Local confirmation describes UI state, never a saved download or changed password. */
export function observesAppAction(action: AppAction, observation: FrontendObservation): boolean {
  const { target, session_id, file_id } = action.payload;
  if (action.action === 'navigate') return observation.session_id === session_id;
  if (action.action === 'open') return Boolean(target && observation.open_views.includes(target));
  if (action.action === 'close') return Boolean(target && !observation.open_views.includes(target)
    && (target !== 'preview' || observation.preview === null) && (target !== 'workspace' || observation.workspace === null));
  if (action.action === 'preview') return observation.preview?.file_id === file_id;
  if (action.action === 'attach') return file_id ? observation.attachment_ids.includes(file_id) : observation.waiting.some(item => item.kind === 'file_selection');
  if (action.action === 'detach') return Boolean(file_id && !observation.attachment_ids.includes(file_id));
  return action.action === 'download' || action.action === 'sign_out';
}
