/** The caller persists drafts before reloading. Registration changes do not reload. */
export function newerClientVersion(current: string, server: unknown): string | null {
  if (current === 'development' || !/^[a-f0-9]{16,64}$/.test(current)) return null;
  if (!server || typeof server !== 'object' || !('version' in server)) return null;
  const version = (server as { version?: unknown }).version;
  return typeof version === 'string' && /^[a-f0-9]{16,64}$/.test(version) && version !== current ? version : null;
}

export async function checkClientVersion(): Promise<string | null> {
  const current = typeof __ATHENA_CLIENT_VERSION__ === 'string' ? __ATHENA_CLIENT_VERSION__ : 'development';
  if (current === 'development') return null;
  try {
    const response = await fetch('/api/client-version', { cache: 'no-store' });
    if (!response.ok) return null;
    return newerClientVersion(current, await response.json());
  } catch { return null; }
}
