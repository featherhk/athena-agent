import { errorMessage } from './i18n';
import type { FileEntry } from './FilePreview';
export type Preferences = { ui_language: string; ui_font: string; ui_palette: string; reduced_motion: string };
export const defaultPreferences: Preferences = { ui_language: 'system', ui_font: 'system', ui_palette: 'dream', reduced_motion: 'system' };
export type User = { id: string; username: string; role: string; developer_mode: boolean; display_name?: string; avatar_url?: string | null; profile_version?: number };
export type Message = { id: string; role: string; content: string; reasoning?: string; attachments?: FileEntry[]; status: string };
export type Run = { id: string; status: string; phase?: string; error?: string; error_code?: string };
export type Component = { id: string; title: string; html: string; version: number; placement?: 'inline' | 'workspace' | 'banner' };
export type Task = { id: string; title: string; due_at: string; status: string; condition_command?: string; last_error?: string; error_code?: string };
export type Snapshot = { messages: Message[]; runs: Run[]; components: Component[]; tasks: Task[]; cursor: number; preferences?: Preferences; account_action?: { seq: number; action: string } | null; files: FileEntry[] };
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
