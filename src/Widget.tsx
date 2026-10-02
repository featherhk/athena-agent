import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type Component } from './api';
import { useI18n } from './i18n';
import { widgetDocument } from './widget-document';
import { customPalette } from './appearance';
import { measureSurface, parseTextStyleEffect, readSurfaceTheme, type TextStyleEffect } from './effects';
import './widgets.css';

type Health = { state: 'mounted' | 'failed'; has_content: boolean; error_code: string | null };
const errors = new Set(['script_error', 'unhandled_rejection', 'blocked_resource', 'empty_document', 'load_timeout']);
const anchored = new Set(['top-start', 'top-end', 'bottom-start', 'bottom-end']);

export default function Widget({ component, sessionId, submit, onStyleText, onNavigate }: { component: Component; sessionId: string; submit: (text: string) => void; onStyleText?: (key: string, effect: TextStyleEffect | null) => void; onNavigate?: (sessionId: string) => void }) {
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
  const instance = useMemo(() => crypto.randomUUID(), [component.id, component.version, locale, attempt]);
  // Later theme changes use the bridge, preserving forms and animation state.
  const [initialPalette] = useState(() => customPalette(preferences.ui_palette));
  const html = useMemo(() => {
    const palette = initialPalette;
    const theme = palette ? { ink: palette.tokens['--ink'], surface: palette.tokens['--surface'], scheme: palette.scheme } : undefined;
    return widgetDocument(component.html, locale, instance, theme, component.placement === 'overlay' || component.placement === 'presence');
  }, [component.html, component.placement, locale, instance, initialPalette]);
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
      if (event.data.type === 'athena:resize' && Number.isFinite(event.data.height)) setHeight(Math.max(80, Math.min(1000, event.data.height)));
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
  const placement = component.placement || 'inline';
  return <section ref={wrapper} className={`widget${anchored.has(placement) ? ' widget-anchored' : ''}`} data-placement={placement}>
    <div className="section-label">{component.title}</div>
    {failure && <div className="widget-failure" role="status"><span>{t('componentUnavailable')}</span><button className="quiet" onClick={() => setAttempt(value => value + 1)}>{t('componentRetry')}</button></div>}
    <iframe ref={frame} title={component.title} sandbox="allow-scripts" srcDoc={html} style={{ height }} onLoad={() => frame.current?.contentWindow?.postMessage({ type: 'athena:inspect', instance }, '*')} />
  </section>;
}
