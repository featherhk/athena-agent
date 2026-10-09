import { api, type Preferences, type User } from './api';
import type { LocalProvider, LocalSetupStatus } from './local-setup';

type Identity = { user: User | null; preferences?: Preferences; configured: boolean; has_users: boolean };
type Request = <T>(path: string, body?: unknown) => Promise<T>;

/** Local mode is declared by the host, never inferred from a URL or a failed login. */
export async function loadIdentity(request: Request = api, current: () => boolean = () => true) {
  let identity = await request<Identity>('/auth/me');
  if (!current()) return null;
  let setup: LocalSetupStatus;
  try { setup = await request<LocalSetupStatus>('/local/setup'); }
  catch (error) {
    if ((error as { status?: number }).status !== 404) throw error;
    setup = { mode: 'account' }; // Compatibility with an older authenticated server.
  }
  if (!current()) return null;
  if (setup.mode !== 'local') return { ...identity, local: false, needsSetup: false };
  if (!identity.user) {
    const local = await request<{ user: User; preferences: Preferences }>('/local/bootstrap', {});
    if (!current()) return null;
    identity = { ...identity, ...local, has_users: true };
  }
  const provider = await request<LocalProvider>('/local/provider');
  if (!current()) return null;
  return { ...identity, configured: provider.configured, local: true, needsSetup: !provider.configured };
}
