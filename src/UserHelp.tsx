import type { HelpRequest, HelpReply } from './api';
import { useI18n } from './i18n';
import './user-help.css';

export function HelpReceipt({ reply }: { reply: HelpReply }) {
  const { t } = useI18n();
  return <details className="help-receipt">
    <summary><span className="help-receipt-mark" aria-hidden="true">✓</span><span>{t('help.answered')}</span><span className="help-receipt-preview" dir="auto">{reply.items.map(item => item.answer).filter(Boolean).join(' · ') || t('help.filesProvided')}</span></summary>
    <dl>{reply.items.map(item => <div key={item.id}><dt dir="auto">{item.question}</dt><dd dir="auto">{item.answer || t('help.filesProvided')}</dd></div>)}</dl>
  </details>;
}

// One question uses the normal composer; multi-question drafts belong to the
// conversation so navigating away never discards a partly completed answer.
export default function UserHelp({ request, select, answer, answers, change, busy }: {
  request: HelpRequest; select: (text: string) => void;
  answers: Record<string, string>; change: (answers: Record<string, string>) => void;
  answer: (answers: Record<string, string>) => Promise<void>; busy: boolean;
}) {
  const { t } = useI18n();
  // A saved spoken answer also needs a visible review/retry path after reload;
  // it must not replace an unrelated message already in the normal composer.
  const single = request.questions.length === 1 && !Object.hasOwn(answers, request.questions[0].id);
  return <section className="user-help" aria-label={t('help.title')}>
    <p className="help-heading">{t('help.title')}</p>
    {request.delegated_title && <p className="help-reason" dir="auto">{request.delegated_title}</p>}
    {request.reason && <p className="help-reason" dir="auto">{request.reason}</p>}
    <form onSubmit={event => { event.preventDefault(); if (!busy) void answer(answers); }}>
      {request.questions.map(question => <fieldset key={question.id} disabled={busy}>
        <legend dir="auto">{question.question}</legend>
        {Boolean(question.options?.length) && <div className="help-choices">{question.options!.map(option => <button
          type="button" key={option.label} aria-pressed={!single && answers[question.id] === option.label}
          onClick={() => single ? select(option.label) : change({ ...answers, [question.id]: option.label })}>
          <span dir="auto">{option.label}</span>{option.description && <small dir="auto">{option.description}</small>}
        </button>)}</div>}
        {!single && <textarea dir="auto" aria-label={question.question} placeholder={t('help.placeholder')}
          maxLength={8000} value={answers[question.id] || ''} onChange={event => change({ ...answers, [question.id]: event.target.value })} />}
      </fieldset>)}
      {single ? <p className="help-hint">{t('help.hint')}</p> : <button className="primary" disabled={busy || request.questions.some(question => !answers[question.id]?.trim())}>{busy ? t('wait') : t('help.continue')}</button>}
    </form>
  </section>;
}
