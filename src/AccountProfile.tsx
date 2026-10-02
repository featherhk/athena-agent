import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from './api';
import type { User } from './api';
import { errorMessage, useI18n } from './i18n';

type ProfileUser = User & { display_name?: string; avatar_url?: string | null; profile_version?: number };

export function AccountProfile({ user, onChange, passwordRequested = false, onPasswordChanged }: {
  user: ProfileUser; onChange: (user: ProfileUser) => void; passwordRequested?: number | boolean; onPasswordChanged?: () => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState(user.display_name || '');
  const [passwordOpen, setPasswordOpen] = useState(Boolean(passwordRequested));
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [credit, setCredit] = useState<{ managed: boolean; available_micros: number } | null>(null);
  useEffect(() => { let live = true; api<{ managed: boolean; available_micros: number }>('/account/credit').then(value => { if (live) setCredit(value); }).catch(() => {}); return () => { live = false; }; }, [user.id]);
  useEffect(() => { setName(user.display_name || ''); }, [user.id, user.display_name]);
  useEffect(() => { if (passwordRequested) setPasswordOpen(true); }, [passwordRequested]);

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await api<{ user: ProfileUser }>('/account/profile', { display_name: name });
      onChange(result.user); setNotice(t('account.updated'));
    } catch (failure) { setError(failure instanceof Error ? failure.message : t('error.generic')); }
    finally { setBusy(false); }
  }
  async function uploadAvatar(file: File) {
    setError(''); setNotice('');
    if (file.size > 2 * 1024 * 1024 || (file.type && !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type))) {
      setError(t('error.avatar')); return;
    }
    setBusy(true);
    try {
      const response = await fetch('/api/account/avatar', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
      const result = await response.json();
      if (!response.ok) throw new Error(errorMessage(result.code));
      onChange(result.user); setNotice(t('account.updated'));
    } catch (failure) { setError(failure instanceof Error ? failure.message : t('error.generic')); }
    finally { setBusy(false); }
  }
  async function savePassword(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(''); setNotice('');
    try {
      await api('/account/password', { current_password: currentPassword, new_password: newPassword });
      setCurrentPassword(''); setNewPassword(''); setPasswordOpen(false); setNotice(t('account.passwordChanged')); onPasswordChanged?.();
    } catch (failure) { setError(failure instanceof Error ? failure.message : t('error.generic')); }
    finally { setBusy(false); }
  }
  return <section className="account-profile">
    {credit?.managed && <div className="account-credit"><span>{t('account.credit')}</span><strong>{new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(credit.available_micros / 1_000_000)}</strong><small>{t('account.noRecharge')}</small></div>}
    <div className="account-avatar" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
      {user.avatar_url && <img src={user.avatar_url} alt={t('account.avatar')} width={52} height={52} style={{ borderRadius: '50%', objectFit: 'cover', flexShrink: 0 }} />}
      <label style={{ margin: 0 }}>{t('account.avatar')}<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={busy}
        onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void uploadAvatar(file); }} /></label>
    </div>
    <form onSubmit={saveProfile}>
      <label>{t('account.displayName')}<input value={name} maxLength={60} autoComplete="nickname" placeholder={user.username} onChange={event => setName(event.target.value)} disabled={busy} /></label>
      <button className="quiet" disabled={busy || name === (user.display_name || '')} type="submit">{t('account.save')}</button>
    </form>
    <button className="quiet" type="button" aria-expanded={passwordOpen} onClick={() => setPasswordOpen(!passwordOpen)}>{t('account.password')}</button>
    {passwordOpen && <form onSubmit={savePassword}>
      <input type="hidden" autoComplete="username" value={user.username} />
      <label>{t('account.currentPassword')}<input type="password" autoComplete="current-password" required minLength={8} maxLength={128} value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} disabled={busy} /></label>
      <label>{t('account.newPassword')}<input type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={newPassword} onChange={event => setNewPassword(event.target.value)} disabled={busy} /></label>
      <button className="quiet" type="submit" disabled={busy}>{t('account.save')}</button>
    </form>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {error && <p className="notice" role="alert">{error}</p>}
  </section>;
}
