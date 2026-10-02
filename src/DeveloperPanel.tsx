import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import type { Key } from './i18n';
import { normalizeMetrics } from './telemetry';
import type { Metrics } from './telemetry';

const cost = (a: number | null | undefined, b: number | null | undefined) => a == null || b == null ? '—' : `$${a.toFixed(6)} – ${b.toFixed(6)}`;

export default function DeveloperPanel({ session, close }: { session: string | null; close: () => void }) {
  const { t, locale } = useI18n();
  const number = (n: number | null | undefined) => n == null ? '—' : n.toLocaleString(locale);
  const percent = (n: number | null | undefined) => n == null ? '—' : new Intl.NumberFormat(locale, { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(n);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState('');
  const [all, setAll] = useState(false);
  const [position, setPosition] = useState({ x: Math.max(12, window.innerWidth - 356), y: 90 });
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const value = await api<unknown>(`/developer/metrics${!all && session ? '?session_id=' + session : ''}`);
        if (!cancelled) { setMetrics(normalizeMetrics(value)); setError(''); }
      } catch (e) { if (!cancelled) setError((e as Error).message); }
    };
    void load(); const timer = window.setInterval(() => void load(), 1500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [session, all]);
  useEffect(() => {
    const resize = () => setPosition(p => ({ x: Math.max(8, Math.min(p.x, window.innerWidth - 340)), y: Math.max(8, Math.min(p.y, window.innerHeight - 180)) }));
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const total = metrics?.total;
  return <aside className="developer-panel" style={{ left: position.x, top: position.y }} aria-label={t('dev.window')}>
    <header onPointerDown={event => {
      if ((event.target as HTMLElement).closest('button')) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { x: event.clientX, y: event.clientY, left: position.x, top: position.y };
    }} onPointerMove={event => {
      if (!drag.current) return;
      setPosition({ x: Math.max(8, Math.min(window.innerWidth - 340, drag.current.left + event.clientX - drag.current.x)), y: Math.max(8, Math.min(window.innerHeight - 180, drag.current.top + event.clientY - drag.current.y)) });
    }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
      <span><i />{t('dev.title')}</span><button className="icon-button" aria-label={t('close')} onClick={close}>×</button>
    </header>
    <div className="dev-content">
      <div className="dev-scope"><button className={!all ? 'active' : ''} onClick={() => setAll(false)}>{t('dev.current')}</button><button className={all ? 'active' : ''} onClick={() => setAll(true)}>{t('dev.all')}</button></div>
      <div className="dev-model">{all ? t('dev.allModels') : metrics?.service?.model || t('dev.waiting')} <span>{t('dev.apiUsage')}</span></div>
      {metrics?.service && <p className="dev-note">{metrics.service.configured ? t('dev.configured') : t('dev.unavailable', { code: metrics.service.code })}<br />{!all && metrics.service.base_model && t('dev.context', { model: metrics.service.base_model, epoch: metrics.service.context_epoch || 0 })}</p>}
      <div className="dev-grid"><div><span>{t('dev.input')}</span><strong>{number(total?.input_tokens)}</strong></div><div><span>{t('dev.output')}</span><strong>{number(total?.output_tokens)}</strong></div><div><span>{t('dev.cache')}</span><strong>{percent(total?.cache_rate)}</strong></div><div><span>{t('dev.requests')}</span><strong>{number(total?.request_count)}</strong></div></div>
      <div className="dev-cost"><span>{t('dev.cost')}</span><strong>{cost(total?.cost_usd_low, total?.cost_usd_high)}</strong></div>
      <p className="dev-note">{t('dev.cacheCounts', { hit: number(total?.cache_hit_tokens), miss: number(total?.cache_miss_tokens) })}<br />{t('dev.missingUsage', { usage: number(total?.unreported_requests), price: number(total?.unpriced_requests) })}<br />{t('dev.cacheCoverage', { count: number(total?.cache_unreported_requests) })}</p>
      {metrics && <details><summary>{t('dev.recent')}</summary><div className="request-list">{metrics.requests.map(request => <div key={request.id}><span>{request.id.slice(0, 7)} · {t(('run.' + request.status) as Key)} · {request.model}</span><span>↑ {number(request.input_tokens)} ↓ {number(request.output_tokens)} · {percent(request.cache_rate)}</span>{request.error_code && <span>{request.error_code}</span>}{request.response_model && <span>{t('dev.returnedModel', { model: request.response_model })}</span>}</div>)}</div></details>}
      {error && <p className="notice">{error}</p>}
      <a className="pricing-link" href="https://api-docs.deepseek.com/quick_start/pricing/" target="_blank" rel="noreferrer">{t('dev.pricing')}</a>
    </div>
  </aside>;
}
