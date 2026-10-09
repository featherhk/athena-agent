import { Component as ReactComponent, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, type Component, type ViewChange } from './api';
import { useI18n } from './i18n';
import Widget from './Widget';
import { desktopPost, desktopRole } from './desktop-bridge';
import { changeDesktopWidgetView, desktopWidgetRequest, DesktopWidgetUnavailable, loadDesktopWidget } from './desktop-widget-loader';
import type { FormDraft } from './component-forms';
import './desktop.css';

class WidgetBoundary extends ReactComponent<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { desktopPost({ action: 'widgetHealth', state: 'failed', error_code: 'script_error' }); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export default function DesktopWidgetPage() {
  const { t, applyPreferences } = useI18n();
  const target = useMemo(() => desktopWidgetRequest(window.location.search, desktopRole() === 'widget'), []);
  const [component, setComponent] = useState<Component | null>(null);
  const [failed, setFailed] = useState(false);
  const [layoutFailed, setLayoutFailed] = useState(false);
  const [bounds, setBounds] = useState(() => ({ x: 0, y: 0, width: window.innerWidth, height: window.innerHeight }));
  const draft = useRef<FormDraft | undefined>(undefined);
  const operations = useRef(new Set<AbortController>());
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
    const controller = new AbortController();
    const fail = () => {
      if (disposed) return;
      setFailed(true);
      desktopPost({ action: 'widgetHealth', state: 'failed', error_code: 'native_unavailable' });
    };
    if (!target) fail();
    else void loadDesktopWidget(target, api, controller.signal).then(result => {
      if (disposed) return;
      if (result.preferences) applyPreferences(result.preferences);
      setComponent(result.component);
    }, fail);
    const resize = () => setBounds({ x: 0, y: 0, width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', resize);
    return () => {
      disposed = true; controller.abort();
      for (const operation of operations.current) operation.abort();
      operations.current.clear(); window.removeEventListener('resize', resize);
    };
  }, [target, applyPreferences]);
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
  const failure = <p className="desktop-widget-error" role="status">{t('componentUnavailable')}</p>;
  return <main className="app desktop-widget">
    <WidgetBoundary fallback={failure}>
      {failed ? failure : component && target && <><Widget component={component} sessionId={target.session}
        bounds={bounds} onView={changeView} draft={draft.current} onDraft={value => { draft.current = value; }}
        submit={text => { desktopPost({ action: 'submit', text }); }}
        onNavigate={session => { desktopPost({ action: 'navigate', session_id: session }); }}
        onHealth={health => { desktopPost({ action: 'widgetHealth', state: health.state, ...(health.error_code ? { error_code: health.error_code } : {}) }); }}
        onHitRegions={regions => { desktopPost({ action: 'hitRegions', regions }); }} />{layoutFailed && failure}</>}
    </WidgetBoundary>
  </main>;
}
