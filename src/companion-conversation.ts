import type { Message, Run } from './api';

/** Presentation only: retain the latest exchange and every unfinished run.
 * Nothing is removed from the durable conversation or the model's context. */
export function companionConversationStart(messages: Message[], runs: Run[]): number {
  const lastUser = messages.findLastIndex(message => message.role === 'user' && message.origin?.kind !== 'agent_results');
  if (lastUser < 0) return 0;
  const currentRun = messages[lastUser].run_id;
  let start = currentRun ? messages.findIndex(message => message.run_id === currentRun) : lastUser;
  const unsettled = new Set(runs.filter(run => ['queued', 'running', 'waiting_user', 'waiting_agents'].includes(run.status)).map(run => run.id));
  const firstUnsettled = messages.findIndex(message => message.run_id && unsettled.has(message.run_id));
  if (firstUnsettled >= 0) start = Math.min(start, firstUnsettled);
  return start;
}
