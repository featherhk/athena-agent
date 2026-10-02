import { errorMessage } from './i18n';
export type User = { id: string; username: string; role: string; developer_mode: boolean };
export type Message = { id: string; role: string; content: string; status: string };
export type Run = { id: string; status: string; error?: string; error_code?: string };
export type Component = { id: string; title: string; html: string; version: number };
export type Task = { id: string; title: string; due_at: string; status: string; condition_command?: string; last_error?: string; error_code?: string };
export type Snapshot = { messages: Message[]; runs: Run[]; components: Component[]; tasks: Task[]; cursor: number };
export type Session = { id: string; title: string; pending: number };
export const empty = (): Snapshot => ({ messages: [], runs: [], components: [], tasks: [], cursor: 0 });

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(errorMessage(value.code));
  return value;
}
