import { useCallback, useEffect, useRef } from 'react';
import type { AppAction } from './api';
import { FrontendReporter, observeFrontend, type FrontendFacts } from './frontend-observation';

/** Observe the fixed shell, not the user's form contents or generated iframe DOM. */
export function useFrontendObservation(owner: string | null, clientId: string, facts: FrontendFacts, onAction: (action: AppAction) => void, onRevoked: () => void, onIdentityLost: () => void) {
  const current = useRef(facts), receive = useRef(onAction), channel = useRef<FrontendReporter | null>(null);
  const revoked = useRef(onRevoked); revoked.current = onRevoked;
  const identityLost = useRef(onIdentityLost); identityLost.current = onIdentityLost;
  current.current = facts; receive.current = onAction;
  const schedule = useRef<() => void>(() => {});
  useEffect(() => {
    if (!owner) return;
    const controller = new AbortController();
    let disposed = false, departing = false, checkingIdentity = false, timer: ReturnType<typeof setTimeout> | undefined, events: EventSource | null = null;
    const reporter = new FrontendReporter(clientId, () => {
      const state = observeFrontend(document, current.current);
      return departing ? { ...state, foreground: false } : state;
    }, async observation => {
      const response = await fetch('/api/frontend/observation', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(observation), signal: controller.signal, keepalive: true });
      const result = await response.json();
      if (!disposed && ['frontend_identity_revoked', 'frontend_identity_changed', 'login_required'].includes(result.code)) {
        checkingIdentity = true; events?.close(); events = null;
        try {
          const auth = await fetch('/api/auth/me', { signal: controller.signal });
          if (!auth.ok) throw new Error('frontend_authentication_unavailable');
          const identity = await auth.json();
          if (!disposed) {
            reporter.stop();
            // Cookies are shared by tabs. Rebind only after verifying the same owner;
            // never let an old tab attach its UI or drafts to a different account.
            if (identity.user?.id === owner) revoked.current(); else identityLost.current();
          }
        } finally { checkingIdentity = false; }
      }
      if (!response.ok || result.accepted !== true) throw new Error('frontend_observation_unavailable');
    });
    channel.current = reporter;
    const connect = () => {
      if (disposed || departing || checkingIdentity || document.hidden || events) return;
      events = new EventSource(`/api/frontend/clients/${encodeURIComponent(clientId)}/events`);
      const message = (event: MessageEvent) => {
        if (disposed || checkingIdentity || document.hidden) return;
        try {
          const item = JSON.parse(event.data);
          if (item.type === 'app_action' && item.payload?.target_client_id === clientId) receive.current(item.payload);
        } catch { /* Invalid/disconnected events cannot execute a UI action. */ }
      };
      events.onmessage = message;
      events.addEventListener('app_action', message as EventListener);
    };
    const report = (force = false) => {
      if (disposed) return;
      void reporter.report(force).then(value => { if (value) connect(); }).catch(() => { /* Diagnostics must not break the user's work. */ });
    };
    const enqueue = () => {
      // Continuous streaming must not postpone a meaningful view change indefinitely.
      if (timer !== undefined) return;
      timer = setTimeout(() => { timer = undefined; report(); }, 200);
    };
    schedule.current = enqueue;
    const visibility = () => {
      if (document.hidden) { events?.close(); events = null; }
      report(true);
    };
    const pagehide = () => { departing = true; events?.close(); events = null; report(true); };
    const pageshow = () => { departing = false; report(true); };
    const mutations = new MutationObserver(records => {
      if (records.some(record => record.type === 'childList' || record.attributeName !== 'style' || (record.target instanceof Element && record.target.matches('.widget')))) enqueue();
    });
    const app = document.querySelector('.app');
    if (app) mutations.observe(app, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['data-app-view', 'data-component-state', 'data-component-error', 'data-component-instance', 'data-placement', 'style', 'hidden'] });
    const interval = setInterval(() => { if (!document.hidden && !departing) report(true); }, 30000);
    window.addEventListener('resize', enqueue); window.visualViewport?.addEventListener('resize', enqueue);
    window.addEventListener('scroll', enqueue, true); window.addEventListener('pointerup', enqueue);
    window.addEventListener('online', visibility); window.addEventListener('pagehide', pagehide); window.addEventListener('pageshow', pageshow);
    document.addEventListener('visibilitychange', visibility);
    report(true);
    return () => {
      disposed = true; reporter.stop(); controller.abort(); events?.close(); mutations.disconnect(); clearInterval(interval);
      if (timer !== undefined) clearTimeout(timer);
      if (channel.current === reporter) channel.current = null;
      schedule.current = () => {};
      window.removeEventListener('resize', enqueue); window.visualViewport?.removeEventListener('resize', enqueue);
      window.removeEventListener('scroll', enqueue, true); window.removeEventListener('pointerup', enqueue);
      window.removeEventListener('online', visibility); window.removeEventListener('pagehide', pagehide); window.removeEventListener('pageshow', pageshow);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [owner, clientId]);
  useEffect(() => { schedule.current(); });
  const report = useCallback(() => channel.current?.report(true) || Promise.resolve(null), []);
  const snapshot = useCallback(() => channel.current?.snapshot() || null, []);
  return { report, snapshot };
}
