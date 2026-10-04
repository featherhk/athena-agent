import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, useI18n } from './i18n';
import type { Key } from './i18n';
import './recharge.css';

type Status = 'creating' | 'pending' | 'confirming' | 'credited' | 'expired' | 'review' | 'unknown';
type Order = { id: string; amount_micros: number; currency: 'USD'; status: Status; checkout_url: string | null; created_at: string; updated_at: string };
type Catalog = { enabled: boolean; amounts_micros: number[]; currency: 'USD'; orders: Order[] };
type Intent = { amount_micros: number; idempotency_key: string };
class RechargeRequestError extends Error {
  constructor(readonly code: string) { super(errorMessage(code)); }
}
const statusKeys: Record<Status, Key> = {
  creating: 'recharge.creating', pending: 'recharge.pending', confirming: 'recharge.confirming',
  credited: 'recharge.credited', expired: 'recharge.expired', review: 'recharge.review', unknown: 'recharge.unknown',
};
const active = (order: Order) => ['creating', 'pending', 'confirming', 'unknown'].includes(order.status);
const uncertain = (order: Order) => order.status === 'creating' || order.status === 'unknown';
const storageKey = (userId: string) => `athena-recharge-intent:${userId}`;
const validAmount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0 && value <= 1_000_000_000;

function restoredIntent(userId: string): Intent | null {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(userId)) || 'null');
    return value && validAmount(value.amount_micros) && typeof value.idempotency_key === 'string'
      && /^[A-Za-z0-9_-]{16,80}$/.test(value.idempotency_key) ? value : null;
  } catch { return null; }
}
function saveIntent(userId: string, value: Intent | null) {
  // Persist before sending. If storage is unavailable, do not risk an unrecoverable payment request.
  if (value) localStorage.setItem(storageKey(userId), JSON.stringify(value));
  else localStorage.removeItem(storageKey(userId));
}
function checkedOrder(value: Order): Order {
  if (!value || typeof value.id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(value.id)
    || !validAmount(value.amount_micros) || value.currency !== 'USD' || !Object.hasOwn(statusKeys, value.status)) throw new Error('invalid_recharge_response');
  return value;
}
function checkoutUrl(order: Order): string | null {
  if (order.status !== 'pending' || !order.checkout_url) return null;
  try {
    const url = new URL(order.checkout_url);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function Recharge({ userId, onCredited }: { userId: string; onCredited: () => void }) {
  const { t, locale } = useI18n();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState<number | null>(null);
  const [intent, setIntent] = useState(() => restoredIntent(userId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pollingPaused, setPollingPaused] = useState(false);
  const mounted = useRef(false), generation = useRef(0), running = useRef(false), controllers = useRef(new Set<AbortController>());
  const creditCallback = useRef(onCredited), knownCredited = useRef(new Set<string>());
  creditCallback.current = onCredited;

  const request = useCallback(async <T,>(path = '', body?: unknown): Promise<T> => {
    const controller = new AbortController(); controllers.current.add(controller);
    const timeout = window.setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(`/api/account/recharge${path}`, {
        signal: controller.signal,
        ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      });
      const value = await response.json();
      if (!response.ok) throw new RechargeRequestError(typeof value.code === 'string' ? value.code : '');
      return value;
    } finally { window.clearTimeout(timeout); controllers.current.delete(controller); }
  }, []);

  const receive = useCallback((orders: Order[]) => {
    let changed = false;
    for (const order of orders) if (order.status === 'credited' && !knownCredited.current.has(order.id)) {
      knownCredited.current.add(order.id); changed = true;
    }
    if (changed) creditCallback.current();
  }, []);

  const refresh = useCallback(async (reconcile: boolean, notice = '') => {
    if (running.current || !mounted.current) return;
    const epoch = generation.current;
    running.current = true; setBusy(true); setError(notice);
    try {
      const value = await request<Catalog>();
      if (typeof value.enabled !== 'boolean' || value.currency !== 'USD' || !Array.isArray(value.orders) || !Array.isArray(value.amounts_micros)) throw new Error('invalid_recharge_response');
      const orders = value.orders.map(checkedOrder);
      if (reconcile && value.enabled) {
        for (let index = 0, count = 0; index < orders.length && count < 3; index++) if (active(orders[index])) {
          orders[index] = checkedOrder(await request<Order>(`/${encodeURIComponent(orders[index].id)}`)); count++;
        }
      }
      if (!mounted.current || epoch !== generation.current) return;
      setCatalog({ ...value, orders, amounts_micros: value.amounts_micros.filter(validAmount) });
      setAmount(previous => value.amounts_micros.includes(previous!) ? previous : value.amounts_micros.find(validAmount) ?? null);
      receive(orders);
    } catch { if (mounted.current && epoch === generation.current) setError('recharge.loadError'); }
    finally { if (epoch === generation.current) { running.current = false; if (mounted.current) setBusy(false); } }
  }, [receive, request]);

  useEffect(() => {
    generation.current++; mounted.current = true; void refresh(false);
    return () => { generation.current++; mounted.current = false; running.current = false; for (const controller of controllers.current) controller.abort(); };
  }, [refresh]);

  const needsPolling = Boolean(catalog?.enabled && catalog.orders.some(active));
  useEffect(() => {
    if (!open || !needsPolling) return;
    let attempts = 0; setPollingPaused(false);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'hidden' || running.current) return;
      if (attempts++ >= 40) { window.clearInterval(timer); setPollingPaused(true); return; }
      void refresh(true);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [open, needsPolling, refresh]);

  async function create() {
    if (running.current || !catalog?.enabled || (!intent && (!amount || catalog.orders.some(active)))) return;
    const epoch = generation.current;
    let reloadAmounts = false;
    const next = intent ?? { amount_micros: amount!, idempotency_key: crypto.randomUUID() };
    try { saveIntent(userId, next); } catch { setError('recharge.storageError'); return; }
    setIntent(next); running.current = true; setBusy(true); setError('');
    try {
      const order = checkedOrder(await request<Order>('', next));
      if (!mounted.current || epoch !== generation.current) return;
      setCatalog(previous => previous && ({ ...previous, orders: [order, ...previous.orders.filter(item => item.id !== order.id)] }));
      if (!uncertain(order)) {
        // An acknowledged invoice is recoverable from the account's durable order history.
        try { saveIntent(userId, null); } catch { /* A leftover key safely recovers this same invoice. */ }
        setIntent(null);
      }
      receive([order]);
    } catch (failure) {
      if (mounted.current && epoch === generation.current) {
        if (failure instanceof RechargeRequestError && failure.code === 'recharge_amount_unavailable') {
          // The service guarantees this code only when this key has never created an order.
          try { saveIntent(userId, null); setIntent(null); reloadAmounts = true; }
          catch { setError('recharge.storageError'); }
        } else setError('recharge.createError');
      }
    } finally {
      if (epoch === generation.current) {
        running.current = false;
        if (mounted.current) { setBusy(false); if (reloadAmounts) void refresh(false, 'recharge.amountChanged'); }
      }
    }
  }

  const money = (micros: number) => new Intl.NumberFormat(locale, { style: 'currency', currency: 'USD' }).format(micros / 1_000_000);
  const dated = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  };
  const hasHistory = Boolean(catalog?.orders.length);
  return <div className="account-recharge">
    {!catalog && !error && <small>{t('recharge.loading')}</small>}
    {catalog && !catalog.enabled && <small>{t('account.noRecharge')}</small>}
    {(catalog?.enabled || hasHistory) && <button type="button" className="quiet recharge-toggle" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {t(catalog?.enabled ? 'recharge.title' : 'recharge.history')}
    </button>}
    {open && catalog && <div className="recharge-content">
      {catalog.enabled && <>
        <p className="recharge-caption">{t('recharge.description')}</p>
        {!intent && !catalog.orders.some(active) && <div className="recharge-amounts" role="group" aria-label={t('recharge.amount')}>
          {catalog.amounts_micros.map(value => <button type="button" key={value} aria-pressed={amount === value} disabled={busy} onClick={() => setAmount(value)}>{money(value)}</button>)}
        </div>}
        {intent && <p className="recharge-caption">{t('recharge.recover', { amount: money(intent.amount_micros) })}</p>}
        {(intent || !catalog.orders.some(active)) && <button type="button" className="quiet recharge-create" disabled={busy || (!intent && !amount)} onClick={() => void create()}>
          {t(busy ? 'recharge.checking' : intent ? 'recharge.retry' : 'recharge.continue')}
        </button>}
      </>}
      {hasHistory && <ul className="recharge-orders">{catalog.orders.map(order => {
        const url = catalog.enabled ? checkoutUrl(order) : null;
        return <li key={order.id}>
          <div className="recharge-order-head"><strong>{money(order.amount_micros)}</strong><span>{t(statusKeys[order.status])}</span></div>
          <time dateTime={order.created_at}>{dated(order.created_at)}</time>
          {(order.status === 'confirming' || uncertain(order) || order.status === 'review') && <p>{t(order.status === 'review' ? 'recharge.reviewHelp' : uncertain(order) ? 'recharge.unknownHelp' : 'recharge.confirmingHelp')}</p>}
          {url && <a href={url} target="_blank" rel="noopener noreferrer">{t('recharge.checkout')}</a>}
        </li>;
      })}</ul>}
      {hasHistory && <button type="button" className="quiet" disabled={busy} onClick={() => void refresh(true)}>{t(busy ? 'recharge.checking' : 'recharge.refresh')}</button>}
      {pollingPaused && <small>{t('recharge.pollingPaused')}</small>}
    </div>}
    {error && <div className="recharge-error" role="alert"><p>{t(error as Key)}</p>{error === 'recharge.loadError' && <button type="button" className="quiet" disabled={busy} onClick={() => void refresh(Boolean(catalog))}>{t('recharge.refresh')}</button>}</div>}
  </div>;
}
