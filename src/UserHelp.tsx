import { useState } from 'react';
import type { HelpRequest } from './api';
import { useI18n } from './i18n';
import './user-help.css';

// One question uses the normal message composer. Multiple questions share one
// transient form; none of these controls exist until Athena needs an answer.
export default function UserHelp({ request, select, answer, busy }: {
  request: HelpRequest; select: (text: string) => void;
  answer: (answers: Record<string, string>) => Promise<void>; busy: boolean;
}) {
  const { t } = useI18n();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const single = request.questions.length === 1;
  return <section className="user-help" aria-label={t('help.title')}>
    <p className="help-heading">{t('help.title')}</p>
    {request.reason && <p className="help-reason" dir="auto">{request.reason}</p>}
    <form onSubmit={event => { event.preventDefault(); if (!busy) void answer(answers); }}>
      {request.questions.map(question => <fieldset key={question.id} disabled={busy}>
        <legend dir="auto">{question.question}</legend>
        {Boolean(question.options?.length) && <div className="help-choices">{question.options!.map(option => <button
          type="button" key={option.label} aria-pressed={!single && answers[question.id] === option.label}
          onClick={() => single ? select(option.label) : setAnswers(old => ({ ...old, [question.id]: option.label }))}>
          <span dir="auto">{option.label}</span>{option.description && <small dir="auto">{option.description}</small>}
        </button>)}</div>}
        {!single && <textarea dir="auto" aria-label={question.question} placeholder={t('help.placeholder')}
          maxLength={8000} value={answers[question.id] || ''} onChange={event => setAnswers(old => ({ ...old, [question.id]: event.target.value }))} />}
      </fieldset>)}
      {single ? <p className="help-hint">{t('help.hint')}</p> : <button className="primary" disabled={busy || request.questions.some(question => !answers[question.id]?.trim())}>{busy ? t('wait') : t('help.continue')}</button>}
    </form>
  </section>;
}
