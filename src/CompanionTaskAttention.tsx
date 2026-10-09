import type { ReactNode } from 'react';
import { useI18n } from './i18n';
import { companionAttention, type ActivityItem } from './task-continuity';
import './companion-task-attention.css';

export default function CompanionTaskAttention({ items, currentSession, busy, open, children }: {
  items: ActivityItem[]; currentSession: string | null; busy: boolean; open: () => void; children: ReactNode;
}) {
  const { t } = useI18n();
  const attention = companionAttention(items, currentSession);
  if (busy || !attention) return children;
  const label = `${t(attention.label)} · ${t('continuity.count', { count: attention.count })}`;
  return <button className="companion-status companion-task-attention" type="button" aria-label={label}
    aria-live="polite" title={label} onClick={open}>
    <span className="companion-task-dot" aria-hidden="true" />{t(attention.label)}
    {attention.count > 1 && <span aria-hidden="true"> · {attention.count}</span>}
  </button>;
}
