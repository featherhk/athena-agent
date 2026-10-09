import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import { localProviderError, providerPresets } from './local-setup';
import type { LocalProvider } from './local-setup';
import './local-setup.css';

/** Fixed-shell credentials form. Never send its field values into agent messages. */
export function LocalSetup({ sessionId = null, onSaved, onCancel }: {
  sessionId?: string | null; onSaved: (result: LocalProvider) => void; onCancel?: () => void;
}) {
  const { t } = useI18n();
  const [current, setCurrent] = useState<LocalProvider | null>(null);
  const [provider, setProvider] = useState<string>(providerPresets.deepseek.provider);
  const [model, setModel] = useState<string>(providerPresets.deepseek.model);
  const [base, setBase] = useState<string>(providerPresets.deepseek.api_base);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ReturnType<typeof localProviderError> | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setError(null); setCurrent(null); setKey('');
    api<LocalProvider>('/local/provider').then(value => {
      if (!live) return;
      setCurrent(value); setProvider(value.profile.provider); setModel(value.profile.model); setBase(value.profile.api_base);
    }).catch((failure: { code?: string }) => { if (live) setError(localProviderError(failure.code)); });
    return () => { live = false; };
  }, [reload]);
  function preset(name: keyof typeof providerPresets) {
    const next = providerPresets[name];
    setProvider(next.provider); setModel(next.model); setBase(next.api_base); setKey(''); setError(null);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!current || busy) return;
    setBusy(true); setError(null);
    // The input is cleared before the request completes; failures never echo the key.
    const apiKey = key;
    setKey('');
    try {
      const result = await api<LocalProvider>('/local/provider', {
        revision: current.revision, provider: provider.trim(), model: model.trim(), api_base: base.trim(),
        ...(apiKey ? { api_key: apiKey } : {}), session_id: sessionId,
      });
      setCurrent(result); onSaved(result);
    } catch (failure) { setError(localProviderError((failure as { code?: string }).code)); }
    finally { setBusy(false); }
  }
  return <section className="local-setup" aria-labelledby="local-setup-title">
    <header><h2 id="local-setup-title">{t('localSetup.title')}</h2><p>{t('localSetup.intro')}</p></header>
    {current && <form onSubmit={submit} autoComplete="off">
      <fieldset disabled={busy}>
        <label>{t('localSetup.preset')}<select defaultValue="" onChange={event => {
          if (event.target.value) preset(event.target.value as keyof typeof providerPresets);
        }}><option value="" disabled>{t('localSetup.choose')}</option><option value="deepseek">DeepSeek</option>
          <option value="local">{t('localSetup.local')}</option><option value="custom">{t('localSetup.custom')}</option></select></label>
        <label>{t('localSetup.provider')}<input required maxLength={120} value={provider} onChange={event => setProvider(event.target.value)} /></label>
        <label>{t('localSetup.model')}<input required maxLength={120} value={model} autoCapitalize="none" spellCheck={false} onChange={event => setModel(event.target.value)} /></label>
        <label>{t('localSetup.base')}<input type="url" required maxLength={2048} value={base} autoCapitalize="none" spellCheck={false} onChange={event => setBase(event.target.value)} /></label>
        <label>{t('localSetup.key')}<input type="password" maxLength={8192} value={key} autoComplete="new-password" autoCapitalize="none" spellCheck={false} onChange={event => setKey(event.target.value)} aria-describedby="local-key-help" /></label>
        <p id="local-key-help" className="local-setup-note">{t(current.configured && base.replace(/\/$/, '') === current.profile.api_base.replace(/\/$/, '') ? 'localSetup.keepKey' : 'localSetup.keyHelp')}</p>
      </fieldset>
      <p className="local-setup-note">{t('localSetup.privacy')}</p>
      <div className="local-setup-actions"><button type="submit" disabled={busy}>{t(busy ? 'localSetup.saving' : 'localSetup.save')}</button>
        {onCancel && <button type="button" className="quiet" onClick={onCancel} disabled={busy}>{t('localSetup.cancel')}</button>}</div>
    </form>}
    {!current && !error && <p role="status">{t('localSetup.loading')}</p>}
    {error && <div role="alert"><p>{t(error)}</p>{(!current || error === 'localSetup.conflict') && <button type="button" className="quiet" onClick={() => setReload(value => value + 1)}>{t('localSetup.reload')}</button>}</div>}
  </section>;
}
