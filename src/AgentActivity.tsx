import { useState } from 'react';
import { api } from './api';
import type { AgentJob } from './api';
import type { AgentResultsOrigin } from './message-origin';
import { useI18n } from './i18n';
import { RunTrace } from './RunTrace';
import './agent-activity.css';

export function AgentResults({ origin }: { origin: AgentResultsOrigin }) {
  const { t } = useI18n();
  return <details className="agent-receipt"><summary>{t('agents.results', { count: origin.agents.length })}</summary>
    {origin.agents.map(job => <RunTrace key={job.id} sessionId={job.session_id} runId={job.run_id} status={job.status} label={job.title} />)}
  </details>;
}

export function AgentActivity({ sessionId, jobs, revision }: { sessionId: string; jobs: AgentJob[]; revision: number }) {
  const { t } = useI18n();
  const [error, setError] = useState('');
  const [stopping, setStopping] = useState<string[]>([]);
  const complete = jobs.filter(job => job.status === 'complete').length;
  const active = (job: AgentJob) => ['queued', 'running', 'waiting_user', 'waiting_agents'].includes(job.status);
  const unfinished = jobs.filter(job => !active(job) && job.status !== 'complete').length;
  async function cancel(job: AgentJob) {
    setStopping(old => [...old, job.id]); setError('');
    try { await api(`/sessions/${sessionId}/agents/${job.id}/cancel`, {}); }
    catch (error) { setError((error as Error).message); }
    finally { setStopping(old => old.filter(id => id !== job.id)); }
  }
  return <details className="agent-activity"><summary>{t('agents.progress', { count: jobs.length, done: complete })}
    {jobs.some(job => job.status === 'waiting_user') && <span> · {t('agents.needsHelp')}</span>}
    {unfinished > 0 && <span> · {t('agents.unfinished', { count: unfinished })}</span>}
  </summary>
    {jobs.map(job => <section className="agent-job" key={job.id}>
      <RunTrace sessionId={job.child_session_id} runId={job.child_run_id} label={job.title} revision={revision} status={job.status} phase={job.phase} />
      {active(job) && <button className="quiet agent-stop" disabled={job.cancel_requested || stopping.includes(job.id)} onClick={() => void cancel(job)}>{t(job.cancel_requested || stopping.includes(job.id) ? 'wait' : 'stop')}</button>}
    </section>)}
    {error && <p role="alert">{error}</p>}
  </details>;
}
