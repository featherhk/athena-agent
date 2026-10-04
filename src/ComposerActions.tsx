import { useI18n } from './i18n';

/** Stopping work is independent from preparing or submitting the next message. */
export default function ComposerActions({ busy, sending, questions, text, attachmentCount, disabled, stop, interrupt }: {
  busy: boolean; sending: boolean; questions: number; text: string; attachmentCount: number; disabled: boolean;
  stop: () => void; interrupt: () => void;
}) {
  const { t } = useI18n();
  const active = busy || questions > 0, stoppable = active || sending, hasDraft = Boolean(text.trim() || attachmentCount);
  return <>
    {active && hasDraft && <button className="interrupt" type="button" disabled={disabled} onClick={interrupt}>{t('interrupt')}</button>}
    {stoppable && <button className="send stop" aria-label={t('stop')} title={t('stop')} type="button" onClick={stop}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
    </button>}
    {(!active || hasDraft) && <button className="send" aria-label={t((busy && !questions) || questions > 1 ? 'queue' : 'send')}
      disabled={!hasDraft || disabled} type="submit">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg>
    </button>}
  </>;
}
