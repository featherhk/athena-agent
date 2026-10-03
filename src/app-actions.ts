import type { AppAction, User } from './api';

export type AppReceipt = { status: 'applied' | 'failed' | 'dismissed'; error_code?: string; observed_revision?: number };
type Transport = <T>(path: string, body?: unknown) => Promise<T>;
export type AppScope = { owner: string | null; session: string | null; generation: number; control: number };
export function scopeGuard(read: () => AppScope) {
  const expected = { ...read() };
  return () => {
    const current = read();
    return current.owner === expected.owner && current.session === expected.session && current.generation === expected.generation && current.control === expected.control;
  };
}
export const interruptedReceipt = (): AppReceipt => ({ status: 'dismissed', error_code: 'interrupted' });

/** Claim first, then execute once. An uncertain acknowledgement never repeats the effect. */
export async function consumeAppAction(action: AppAction, clientId: string, transport: Transport,
  execute: (action: AppAction, isCurrent: () => boolean) => Promise<AppReceipt>, isCurrent: () => boolean, isSameAccount = isCurrent) {
  if (!isCurrent() || (action.target_client_id && action.target_client_id !== clientId)) return false;
  const base = `/sessions/${encodeURIComponent(action.session_id)}/app-actions/${encodeURIComponent(action.id)}`;
  const claim = await transport<{ accepted: boolean; request: AppAction }>(`${base}/claim`, { client_id: clientId });
  if (!claim.accepted) return false;
  let receipt: AppReceipt;
  try { receipt = isCurrent() && (!claim.request.target_client_id || claim.request.target_client_id === clientId) ? await execute(claim.request, isCurrent) : interruptedReceipt(); }
  catch { receipt = isCurrent() ? { status: 'failed', error_code: 'execution_failed' } : interruptedReceipt(); }
  // Logout is applied by the ACK itself. Never revoke a newly selected account/session.
  if (action.action === 'sign_out' && !isCurrent()) receipt = interruptedReceipt();
  const body = { client_id: clientId, ...receipt };
  try {
    const result = await transport<{ completed?: boolean }>(`${base}/ack`, body);
    return receipt.status === 'applied' && result?.completed !== false;
  } catch {
    if (action.action === 'sign_out') {
      // The server may have committed and revoked the cookie before the reply was lost.
      // Query current authentication instead of posting the logout receipt again.
      if (!isSameAccount() || receipt.status !== 'applied') return false;
      const result = await transport<{ user: User | null }>('/auth/me');
      return isSameAccount() && result.user === null;
    }
    const result = await transport<{ completed?: boolean }>(`${base}/ack`, body);
    return receipt.status === 'applied' && result?.completed !== false;
  }
}
