import { errorMessage } from './i18n';
import type { FileEntry } from './FilePreview';
import type { MessageOrigin } from './message-origin';
export type Preferences = { ui_language: string; ui_font: string; ui_palette: string; reduced_motion: string; presence_size: number; developer_mode: boolean };
export const defaultPreferences: Preferences = { ui_language: 'system', ui_font: 'system', ui_palette: 'dream', reduced_motion: 'system', presence_size: 84, developer_mode: false };
export type User = { id: string; username: string; role: string; developer_mode: boolean; display_name?: string; avatar_url?: string | null; profile_version?: number };
export type HelpReply = { id: string; reason: string; items: { id: string; question: string; answer: string }[] };
export type Message = { id: string; role: string; content: string; reasoning?: string; attachments?: FileEntry[]; status: string; run_id?: string; help_reply?: HelpReply; origin?: MessageOrigin };
export type Run = { id: string; status: string; phase?: string; error?: string; error_code?: string };
export type Placement = 'desktop' | 'surface' | 'inline' | 'workspace' | 'banner' | 'top-start' | 'top-end' | 'bottom-start' | 'bottom-end' | 'overlay' | 'fullscreen' | 'presence' | 'sidebar-start' | 'sidebar-end';
export type ComponentView = { hidden?: boolean; placement?: Placement; x?: number; y?: number; width?: number; height?: number; screen_id?: string };
export type ViewChange = { [K in keyof ComponentView]?: ComponentView[K] | null };
export type Component = { id: string; title: string; html: string; version: number; file_id?: string | null; file?: FileEntry | null; active?: boolean | number; placement?: Placement; view?: ComponentView; view_revision?: number; surface_selected?: boolean; view_updated_at?: string | null; created_at?: string };
export type HelpRequest = { id: string; run_id: string; session_id: string; status: string; reason?: string; delegated_title?: string; questions: { id: string; question: string; options?: { label: string; description?: string }[] }[] };
export type AgentJob = { id: string; title: string; child_session_id: string; child_run_id: string; parent_run_id: string; root_run_id: string; status: string; phase?: string; cancel_requested: boolean };
export type Task = { id: string; title: string; due_at: string; status: string; condition_command?: string; last_error?: string; error_code?: string };
export type AppAction = { id: string; session_id: string; action: string; status: string; target_client_id?: string | null; expected_revision?: number | null; payload: { target?: string; session_id?: string; file_id?: string; file?: FileEntry } };
export type Snapshot = { messages: Message[]; runs: Run[]; components: Component[]; tasks: Task[]; cursor: number; submission_revision?: number; preferences?: Preferences; account_action?: { seq: number; action: string } | null; files: FileEntry[]; help_requests?: HelpRequest[]; app_actions?: AppAction[]; agents?: AgentJob[] };
export type Session = { id: string; title: string; pending: number };
export const empty = (): Snapshot => ({ messages: [], runs: [], components: [], tasks: [], cursor: 0, files: [] });

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api' + path, body === undefined ? { signal, cache: 'no-store' } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  const value = await response.json();
  if (!response.ok) throw Object.assign(new Error(errorMessage(value.code)), { code: value.code, status: response.status });
  return value;
}
