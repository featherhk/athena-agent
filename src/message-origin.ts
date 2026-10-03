export type TaskOrigin = { kind: 'task'; trigger: 'time' | 'condition'; task_id: string; title: string; occurrence: number };
export type AgentTaskOrigin = { kind: 'agent_task'; id: string; title: string; parent_session_id: string };
export type AgentResultsOrigin = { kind: 'agent_results'; wait_id: string; agents: { id: string; title: string; status: string; session_id: string; run_id: string }[] };
export type MessageOrigin = TaskOrigin | AgentTaskOrigin | AgentResultsOrigin;

// A matching phrase in ordinary chat is never evidence of an automatic trigger.
// Unknown metadata falls back to a normal message instead of hiding its content.
export function taskOrigin(message: { role: string; origin?: unknown }): TaskOrigin | undefined {
  if (message.role !== 'user' || !message.origin || typeof message.origin !== 'object') return;
  const value = message.origin as Record<string, unknown>;
  if (value.kind !== 'task' || (value.trigger !== 'time' && value.trigger !== 'condition')
      || typeof value.task_id !== 'string' || !value.task_id || typeof value.title !== 'string' || !value.title
      || typeof value.occurrence !== 'number' || !Number.isSafeInteger(value.occurrence) || value.occurrence < 1) return;
  return { kind: 'task', trigger: value.trigger, task_id: value.task_id, title: value.title, occurrence: value.occurrence };
}

export function agentOrigin(message: { role: string; origin?: unknown }): AgentTaskOrigin | AgentResultsOrigin | undefined {
  if (message.role !== 'user' || !message.origin || typeof message.origin !== 'object') return;
  const value = message.origin as Record<string, unknown>;
  const strings = (object: Record<string, unknown>, keys: string[]) => keys.every(key => typeof object[key] === 'string' && object[key]);
  if (value.kind === 'agent_task' && strings(value, ['id', 'title', 'parent_session_id'])) return value as AgentTaskOrigin;
  if (value.kind === 'agent_results' && typeof value.wait_id === 'string' && Array.isArray(value.agents)
      && value.agents.length >= 1 && value.agents.length <= 8
      && value.agents.every(job => job && typeof job === 'object' && strings(job, ['id', 'title', 'status', 'session_id', 'run_id']))) return value as AgentResultsOrigin;
}
