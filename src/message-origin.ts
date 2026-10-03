export type TaskOrigin = { kind: 'task'; trigger: 'time' | 'condition'; task_id: string; title: string; occurrence: number };

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
