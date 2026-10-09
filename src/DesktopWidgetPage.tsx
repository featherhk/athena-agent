import { Component as ReactComponent, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, type Component, type ViewChange } from './api';
import { useI18n } from './i18n';
import Widget from './Widget';
import { desktopPost, desktopRole } from './desktop-bridge';
import { changeDesktopWidgetView, desktopWidgetRequest, DesktopWidgetUnavailable, loadDesktopWidget, type DesktopFileComponent } from './desktop-widget-loader';
import type { FormDraft } from './component-forms';
import type { PreviewObservation } from './frontend-observation';
import DesktopArtifact from './DesktopArtifact';
import './desktop.css';

class WidgetBoundary extends ReactComponent<{ children: ReactNode; fallback: ReactNode; onFailure: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onFailure(); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export default function DesktopWidgetPage() {
  const { t, applyPreferences } = useI18n();
  const target = useMemo(() => desktopWidgetRequest(window.location.search, desktopRole() === 'widget'), []);
  const [component, setComponent] = useState<DesktopFileComponent | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [layoutFailed, setLayoutFailed] = useState(false);
  const [bounds, setBounds] = useState(() => ({ x: 0, y: 0, width: window.innerWidth, height: window.innerHeight }));
  const draft = useRef<FormDraft | undefined>(undefined);
  const operations = useRef(new Set<AbortController>());
  const current = useRef(component); current.current = component;
  useLayoutEffect(() => {
    const root = window.document.documentElement, previous = root.dataset.desktop;
    root.dataset.desktop = 'widget';
    return () => {
      if (root.dataset.desktop !== 'widget') return;
      if (previous === undefined) delete root.dataset.desktop;
      else root.dataset.desktop = previous;
    };
  }, []);
  useEffect(() => {
    let disposed = false;
    let active: AbortController | null = null;
    let queued = false;
    const fail = () => {
      if (disposed) return;
      setFailed(true);
      desktopPost({ action: 'widgetHealth', state: 'failed', error_code: 'native_unavailable',
        ...(target?.file ? { file_id: target.file, content_revision: target.contentRevision ?? null } : {}) });
    };
    const load = async () => {
      if (disposed) return;
      if (!target) { fail(); return; }
      if (active) { queued = true; return; }
      const controller = new AbortController(); active = controller;
      try {
        const result = await loadDesktopWidget(target, api, controller.signal);
        if (disposed) return;
        if (result.preferences) applyPreferences(result.preferences);
        setComponent(result.component); setFailed(false); setLayoutFailed(false);
      } catch (error) {
        if (disposed) return;
        if (current.current && !(error instanceof DesktopWidgetUnavailable) && ![401, 403, 404].includes((error as { status?: number }).status || 0)) setLayoutFailed(true);
        else fail();
      }
      finally { active = null; if (queued && !disposed) { queued = false; void load(); } }
    };
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (target && detail?.scope === `${target.owner}:${target.session}` && detail.component_id === target.component && detail.version === target.version) void load();
    };
    window.addEventListener('athena:widget-refresh', refresh);
    void load();
    const resize = () => setBounds({ x: 0, y: 0, width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', resize);
    return () => {
      disposed = true; active?.abort();
      for (const operation of operations.current) operation.abort();
      operations.current.clear(); window.removeEventListener('resize', resize); window.removeEventListener('athena:widget-refresh', refresh);
    };
  }, [target, applyPreferences, attempt]);
  useEffect(() => {
    if (!failed && component) return;
    // Initial failures have no mounted renderer to declare hit regions. Keep
    // the trusted retry control reachable instead of a click-through dead end.
    desktopPost({ action: 'hitRegions', regions: [{ x: 0, y: 0, width: bounds.width, height: bounds.height }] });
    desktopPost({ action: 'surfaceDragRegions', regions: [] });
  }, [failed, component, bounds.width, bounds.height]);
  async function changeView(_component: Component, view: ViewChange) {
    if (!target) return;
    const controller = new AbortController(); operations.current.add(controller);
    try {
      const next = await changeDesktopWidgetView(target, view, api, controller.signal);
      if (!controller.signal.aborted) { setComponent(next); setLayoutFailed(false); }
    } catch (error) {
      if (!controller.signal.aborted) {
        if (error instanceof DesktopWidgetUnavailable || [401, 403, 404].includes((error as { status?: number }).status || 0)) {
          setFailed(true);
          desktopPost({ action: 'widgetHealth', state: 'failed', error_code: 'native_unavailable' });
        } else {
          // A failed or conflicting layout write must not discard a working
          // document, its form draft, selection, or animation state.
          setLayoutFailed(true);
        }
      }
      throw error;
    } finally { operations.current.delete(controller); }
  }
  function observeFile(value: PreviewObservation) {
    const file = current.current?.file;
    if (!file || file.id !== value.file_id) return;
    desktopPost({ action: 'widgetHealth', state: value.status === 'ready' ? 'mounted' : value.status, file_id: file.id,
      content_revision: file.content_revision ?? null, ...(value.status === 'failed' ? { error_code: 'preview_unavailable' } : {}) });
  }
  const failure = <p className="desktop-widget-error" role="status">{t('componentUnavailable')}<button className="quiet" type="button" onClick={() => setAttempt(value => value + 1)}>{t('retry')}</button></p>;
  return <main className="app desktop-widget">
    <WidgetBoundary key={attempt} fallback={failure} onFailure={() => {
      setFailed(true);
      desktopPost({ action: 'widgetHealth', state: 'failed', error_code: 'script_error',
        ...(component?.file_id && component.file ? { file_id: component.file_id, content_revision: component.file.content_revision ?? null } : {}) });
    }}>
      {failed ? failure : component && target ? component.file_id && component.file ? <DesktopArtifact file={component.file} onObservation={observeFile} layoutFailed={layoutFailed} /> : <><Widget component={component} sessionId={target.session}
        bounds={bounds} onView={changeView} draft={draft.current} onDraft={value => { draft.current = value; }}
        submit={text => { desktopPost({ action: 'submit', text }); }}
        onNavigate={session => { desktopPost({ action: 'navigate', session_id: session }); }}
        onHealth={health => { desktopPost({ action: 'widgetHealth', state: health.state, ...(health.error_code ? { error_code: health.error_code } : {}) }); }}
        onHitRegions={regions => { desktopPost({ action: 'hitRegions', regions }); }} />{layoutFailed && failure}</> : <p className="desktop-widget-error" role="status">{t('files.loading')}</p>}
    </WidgetBoundary>
  </main>;
}
