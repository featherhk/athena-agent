import { api } from './api';
import type { Preferences, User } from './api';

export type LocalSetupStatus = { mode: 'local' | 'account' | 'hosted'; initialized?: boolean };
export type LocalProvider = {
  revision: number;
  configured: boolean;
  profile: { id: string; provider: string; model: string; api_base: string };
};
export const localSetupStatus = () => api<LocalSetupStatus>('/local/setup');
export const bootstrapLocal = () => api<{ user: User; preferences: Preferences }>('/local/bootstrap', {});

export const providerPresets = {
  deepseek: { provider: 'DeepSeek', model: 'deepseek-flash', api_base: 'https://api.deepseek.com' },
  local: { provider: 'Local', model: '', api_base: 'http://127.0.0.1:11434/v1' },
  custom: { provider: '', model: '', api_base: '' },
} as const;

export function localProviderError(code?: string) {
  if (code === 'local_model_busy') return 'localSetup.busy';
  if (code === 'local_setup_conflict') return 'localSetup.conflict';
  if (code === 'local_provider_key_required') return 'localSetup.keyRequired';
  if (code === 'local_setup_unavailable' || code === 'local_existing_accounts' || code === 'login_required') return 'localSetup.unavailable';
  return 'localSetup.failed';
}
