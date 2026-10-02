import { errorMessage } from './i18n';
import type { FileEntry } from './FilePreview';
export type Preferences = { ui_language: string; ui_font: string; ui_palette: string; reduced_motion: string; presence_size: number; developer_mode: boolean };
export const defaultPreferences: Preferences = { ui_language: 'system', ui_font: 'system', ui_palette: 'dream', reduced_motion: 'system', presence_size: 84, developer_mode: false };
export type User = { id: string; username: string; role: string; developer_mode: boolean; display_name?: string; avatar_url?: string | null; profile_version?: number };
export type Message = { id: string; role: string; content: string; reasoning?: string; attachments?: FileEntry[]; status: string; run_id?: string };
export type Run = { id: string; status: string; phase?: string; error?: string; error_code?: string };
export type Component = { id: string; title: string; html: string; version: number; placement?: 'inline' | 'workspace' | 'banner' | 'top-start' | 'top-end' | 'bottom-start' | 'bottom-end' | 'overlay' | 'fullscreen' | 'presence' | 'sidebar-start' | 'sidebar-end' };
export type HelpRequest = { id: string; run_id: string; session_id: string; status: string; reason?: string; questions: { id: string; question: string; options?: { label: string; description?: string }[] }[] };
export type Task = { id: string; title: string; due_at: string; status: string; condition_command?: string; last_error?: string; error_code?: string };
export type AppAction = { id: string; session_id: string; action: string; status: string; payload: { target?: string; session_id?: string; file_id?: string; file?: FileEntry } };
export type Snapshot = { messages: Message[]; runs: Run[]; components: Component[]; tasks: Task[]; cursor: number; preferences?: Preferences; account_action?: { seq: number; action: string } | null; files: FileEntry[]; help_requests?: HelpRequest[]; app_actions?: AppAction[] };
export type Session = { id: string; title: string; pending: number };
export const empty = (): Snapshot => ({ messages: [], runs: [], components: [], tasks: [], cursor: 0, files: [] });

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(errorMessage(value.code));
  return value;
}
