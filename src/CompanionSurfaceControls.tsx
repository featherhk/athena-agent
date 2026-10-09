import { useI18n } from './i18n';
import './companion-surface-controls.css';

/** Recovery belongs to the trusted companion, outside a generated surface. */
export default function CompanionSurfaceControls({ back, stop, reset, resetting }: {
  back: () => void; stop: () => void; reset: () => void; resetting: boolean;
}) {
  const { t } = useI18n();
  return <nav className="companion-surface-controls" data-desktop-hit aria-label={t('chatControls')}>
    <button className="quiet" type="button" onClick={back}>{t('backToChat')}</button>
    <button className="quiet" type="button" onClick={stop}>{t('stop')}</button>
    <button className="quiet" type="button" disabled={resetting} onClick={reset}>{t('reset')}</button>
  </nav>;
}
