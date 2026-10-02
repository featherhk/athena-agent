import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { api, type Component, type ViewChange } from './api';
import { useI18n } from './i18n';
import { widgetDocument } from './widget-document';
import { customPalette } from './appearance';
import { measureSurface, parseTextStyleEffect, readSurfaceTheme, type TextStyleEffect } from './effects';
import { placementOf, movePanel, resizePanel, panelView, type Rect } from './component-layout';
import ComponentControls from './ComponentControls';
import { parseFormDraft, type FormDraft } from './component-forms';
import './widgets.css';

type Health = { state: 'mounted' | 'failed'; has_content: boolean; error_code: string | null };
const errors = new Set(['script_error', 'unhandled_rejection', 'blocked_resource', 'empty_document', 'load_timeout']);

export default function Widget({ component, sessionId, submit, onStyleText, onNavigate, onView, floating, bounds, onHeight, fallback = false, draft, onDraft }: { component: Component; sessionId: string; submit: (text: string) => void; onStyleText?: (key: string, effect: TextStyleEffect | null) => void; onNavigate?: (sessionId: string) => void; onView: (component: Component, view: ViewChange) => Promise<void>; floating?: Rect; bounds: Rect; onHeight?: (id: string, height: number) => void; fallback?: boolean; draft?: FormDraft; onDraft: (value: FormDraft) => void }) {
  const { locale, t, preferences, reduced } = useI18n();
  const frame = useRef<HTMLIFrameElement>(null);
  const wrapper = useRef<HTMLElement>(null);
  const submitRef = useRef(submit);
  const styleRef = useRef(onStyleText);
  const navigateRef = useRef(onNavigate);
  const layoutMeta = useRef({ locale, reduced });
  const refreshLayout = useRef<() => void>(() => {});
  const [height, setHeight] = useState(240);
  const [failure, setFailure] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [dragged, setDragged] = useState<Rect | null>(null);
  const drag = useRef<{ pointer: number; x: number; y: number; start: Rect; current: Rect; resize: boolean } | null>(null);
  const placement = fallback ? 'inline' : placementOf(component);
  const measuredRef = useRef(onHeight); measuredRef.current = onHeight;
  const form = useRef(draft), saveForm = useRef(onDraft); saveForm.current = onDraft;
  async function change(view: ViewChange) {
    if (saving) return;
    setSaving(true);
    try { await onView(component, view); }
    catch { /* The protected shell reports the failed save. */ }
    finally { setSaving(false); setDragged(null); }
  }
  function startDrag(event: ReactPointerEvent<HTMLButtonElement>, resize = false) {
    if (!floating || saving || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, start: floating, current: floating, resize };
    setDragged(floating); event.preventDefault();
  }
  function moveDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    const rect = (current.resize ? resizePanel : movePanel)(current.start, event.clientX - current.x, event.clientY - current.y, bounds);
    current.current = rect; setDragged(rect);
  }
  function endDrag(event: ReactPointerEvent<HTMLButtonElement>, cancel = false) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!cancel && JSON.stringify(current.start) !== JSON.stringify(current.current)) void change(panelView(current.current, bounds));
    else setDragged(null);
  }
  function keyMove(event: ReactKeyboardEvent<HTMLButtonElement>, resize = false) {
    if (!floating || saving || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? 40 : 10;
    const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
    const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    void change(panelView((resize ? resizePanel : movePanel)(floating, dx, dy, bounds), bounds));
  }
  const instance = useMemo(() => crypto.randomUUID(), [component.id, component.version, locale, attempt]);
  // Later theme changes use the bridge, preserving forms and animation state.
  const [initialPalette] = useState(() => customPalette(preferences.ui_palette));
  const html = useMemo(() => {
    const palette = initialPalette;
    const theme = palette ? { ink: palette.tokens['--ink'], surface: palette.tokens['--surface'], scheme: palette.scheme } : undefined;
    return widgetDocument(component.html, locale, instance, theme, placement === 'overlay' || placement === 'presence');
  }, [component.html, placement, locale, instance, initialPalette]);
  useEffect(() => { submitRef.current = submit; }, [submit]);
  useEffect(() => { styleRef.current = onStyleText; }, [onStyleText]);
  useEffect(() => { navigateRef.current = onNavigate; }, [onNavigate]);
  useEffect(() => { layoutMeta.current = { locale, reduced }; refreshLayout.current(); }, [locale, reduced]);
  useEffect(() => {
    let health: Health | null = null, inView = false, lastReport = '', disposed = false;
    let observing = false, layoutFrame = 0, styleFrame = 0, lastLayout = '', lastTheme = '';
    const pendingStyles = new Map<string, TextStyleEffect>();
    let clearStyles = false;
    const componentKey = `${component.id}:${component.version}`;
    const app = wrapper.current?.closest('.app');
    const sendTheme = () => {
      if (!app || !frame.current || disposed) return;
      const theme = readSurfaceTheme(app);
      const signature = JSON.stringify(theme);
      if (signature !== lastTheme) {
        lastTheme = signature;
        frame.current.contentWindow?.postMessage({ type: 'athena:theme', theme, instance }, '*');
      }
      return theme;
    };
    const sendLayout = () => {
      layoutFrame = 0;
      if (!observing || disposed || !app || !frame.current) return;
      const layout = measureSurface(app, frame.current, layoutMeta.current.locale, layoutMeta.current.reduced, sendTheme());
      const signature = JSON.stringify(layout);
      if (signature === lastLayout) return;
      lastLayout = signature;
      frame.current.contentWindow?.postMessage({ type: 'athena:layout', layout, instance }, '*');
    };
    const scheduleLayout = () => { if (observing && !layoutFrame) layoutFrame = requestAnimationFrame(sendLayout); };
    refreshLayout.current = scheduleLayout;
    setFailure(false);
    const report = () => {
      if (!health || disposed || !sessionId) return;
      const value = { ...health, version: component.version, instance, in_view: inView && !document.hidden };
      const signature = JSON.stringify(value);
      if (signature === lastReport) return;
      lastReport = signature;
      // A diagnostic upload must not break the user's chat when offline.
      void api(`/sessions/${sessionId}/components/${component.id}/report`, value).catch(() => { if (!disposed) lastReport = ''; });
    };
    const timeout = window.setTimeout(() => {
      if (!health) { health = { state: 'failed', has_content: false, error_code: 'load_timeout' }; setFailure(true); report(); }
    }, 5000);
    const listener = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.instance !== instance) return;
      if (event.data.type === 'athena:form-state') {
        const next = parseFormDraft(event.data.draft);
        if (next) { form.current = next; saveForm.current(next); }
      }
      if (event.data.type === 'athena:resize' && Number.isFinite(event.data.height)) {
        const next = Math.max(80, Math.min(1000, event.data.height));
        setHeight(next); measuredRef.current?.(component.id, next);
      }
      if (event.data.type === 'athena:submit' && typeof event.data.text === 'string') submitRef.current(event.data.text.slice(0, 30000));
      if (event.data.type === 'athena:navigate' && typeof event.data.session_id === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(event.data.session_id) && navigator.userActivation?.isActive !== false) navigateRef.current?.(event.data.session_id);
      if (event.data.type === 'athena:observe') { observing = true; lastLayout = ''; scheduleLayout(); }
      if (event.data.type === 'athena:unobserve') observing = false;
      if (event.data.type === 'athena:style-text') {
        const effect = parseTextStyleEffect(event.data.effect);
        if (effect !== undefined) {
          if (effect === null) { pendingStyles.clear(); clearStyles = true; }
          else if (pendingStyles.size < 128 || pendingStyles.has(effect.message_id)) pendingStyles.set(effect.message_id, effect);
          if (!styleFrame) styleFrame = requestAnimationFrame(() => {
            styleFrame = 0;
            if (clearStyles) styleRef.current?.(componentKey, null);
            for (const effect of pendingStyles.values()) styleRef.current?.(componentKey, effect);
            pendingStyles.clear(); clearStyles = false;
          });
        }
      }
      if (event.data.type === 'athena:health' && typeof event.data.has_content === 'boolean') {
        if (form.current) frame.current?.contentWindow?.postMessage({ type: 'athena:restore-form', instance, draft: form.current }, '*');
        lastTheme = ''; sendTheme();
        const { state, has_content, error_code } = event.data;
        if ((state === 'mounted' && error_code === null) || (state === 'failed' && errors.has(error_code))) {
          health = { state, has_content, error_code };
          setFailure(state === 'failed'); report();
        }
      }
    };
    const observer = new IntersectionObserver(entries => { inView = entries.some(entry => entry.isIntersecting); report(); });
    if (wrapper.current) observer.observe(wrapper.current);
    const sizeObserver = new ResizeObserver(scheduleLayout);
    if (app) sizeObserver.observe(app);
    if (frame.current) sizeObserver.observe(frame.current);
    const changeObserver = new MutationObserver(scheduleLayout);
    if (app) changeObserver.observe(app, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['style', 'class', 'data-message-id', 'data-message-role'] });
    const themeObserver = new MutationObserver(() => { sendTheme(); scheduleLayout(); });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'class', 'data-palette', 'data-font'] });
    window.addEventListener('scroll', scheduleLayout, true);
    window.addEventListener('resize', scheduleLayout);
    app?.addEventListener('load', scheduleLayout, true);
    document.fonts?.addEventListener('loadingdone', scheduleLayout);
    document.addEventListener('visibilitychange', report);
    window.addEventListener('message', listener);
    // Ask again if the iframe loaded before React installed this listener.
    frame.current?.contentWindow?.postMessage({ type: 'athena:inspect', instance }, '*');
    return () => {
      disposed = true; window.clearTimeout(timeout); observer.disconnect();
      cancelAnimationFrame(layoutFrame); cancelAnimationFrame(styleFrame);
      sizeObserver.disconnect(); changeObserver.disconnect(); themeObserver.disconnect();
      window.removeEventListener('scroll', scheduleLayout, true); window.removeEventListener('resize', scheduleLayout);
      app?.removeEventListener('load', scheduleLayout, true); document.fonts?.removeEventListener('loadingdone', scheduleLayout);
      refreshLayout.current = () => {};
      styleRef.current?.(componentKey, null);
      window.removeEventListener('message', listener); document.removeEventListener('visibilitychange', report);
    };
  }, [sessionId, component.id, component.version, instance]);
  const rect = dragged || floating;
  return <section ref={wrapper} className={`widget${floating ? ' widget-floating' : ''}${dragged ? ' widget-adjusting' : ''}`} data-placement={placement} data-component-id={component.id} style={rect ? { left: rect.x, top: rect.y, width: rect.width, height: rect.height } : ['workspace', 'fullscreen', 'presence', 'overlay'].includes(placement) ? undefined : { maxWidth: component.view?.width, marginInline: 'auto' }}>
    <header className="widget-toolbar">
      {floating ? <button className="widget-handle" disabled={saving} aria-label={t('surface.move', { name: component.title })} onPointerDown={event => startDrag(event)} onPointerMove={moveDrag} onPointerUp={event => endDrag(event)} onPointerCancel={event => endDrag(event, true)} onLostPointerCapture={event => endDrag(event, true)} onKeyDown={event => keyMove(event)}>{component.title}</button> : <span className="section-label">{component.title}</span>}
      <ComponentControls component={component} change={view => void change(view)} busy={saving} />
    </header>
    {failure && <div className="widget-failure" role="status"><span>{t('componentUnavailable')}</span><button className="quiet" onClick={() => setAttempt(value => value + 1)}>{t('componentRetry')}</button></div>}
    <iframe ref={frame} title={component.title} sandbox="allow-scripts" srcDoc={html} style={{ height: component.view?.height || height }} onLoad={() => frame.current?.contentWindow?.postMessage({ type: 'athena:inspect', instance }, '*')} />
    {floating && <button className="widget-resize" disabled={saving} aria-label={t('surface.resize', { name: component.title })} onPointerDown={event => startDrag(event, true)} onPointerMove={moveDrag} onPointerUp={event => endDrag(event)} onPointerCancel={event => endDrag(event, true)} onLostPointerCapture={event => endDrag(event, true)} onKeyDown={event => keyMove(event, true)}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m5 12 7-7m-2 7 2-2" /></svg></button>}
  </section>;
}
