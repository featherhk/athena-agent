import { useEffect, useRef, useState } from 'react';
import { FilePreview, type FileEntry } from './FilePreview';
import type { PreviewObservation } from './frontend-observation';
import { useI18n } from './i18n';
import { desktopPost } from './desktop-bridge';
import './desktop-artifact.css';

/** A real file on the desktop, independent of the companion conversation. */
export default function DesktopArtifact({ file, onObservation, layoutFailed = false }: {
  file: FileEntry; onObservation: (value: PreviewObservation) => void; layoutFailed?: boolean;
}) {
  const { t } = useI18n();
  const root = useRef<HTMLElement>(null);
  const [maximized, setMaximized] = useState(() => document.documentElement.dataset.surfaceMaximized === 'true');
  useEffect(() => {
    const update = (event: Event) => {
      const state = (event as CustomEvent).detail;
      if (typeof state?.maximized === 'boolean') setMaximized(state.maximized);
    };
    window.addEventListener('athena:surface', update);
    return () => window.removeEventListener('athena:surface', update);
  }, []);
  useEffect(() => {
    const report = () => {
      const element = root.current;
      if (!element) return;
      const rectangle = (node: Element) => {
        const box = node.getBoundingClientRect();
        return { x: box.x, y: box.y, width: box.width, height: box.height };
      };
      desktopPost({ action: 'hitRegions', regions: [rectangle(element)] });
      desktopPost({ action: 'surfaceDragRegions', regions: [...element.querySelectorAll('[data-desktop-drag]')].map(rectangle) });
    };
    const observer = new ResizeObserver(report);
    if (root.current) {
      observer.observe(root.current);
      for (const node of root.current.querySelectorAll('[data-desktop-drag]')) observer.observe(node);
    }
    window.addEventListener('resize', report); report();
    return () => { observer.disconnect(); window.removeEventListener('resize', report); };
  }, []);
  const post = (action: string) => desktopPost({ action });
  return <section className="desktop-artifact" ref={root} aria-label={file.name} data-file-id={file.id}>
    <header className="desktop-artifact-header">
      <div className="desktop-artifact-title" data-desktop-drag title={file.name}>
        <span className="desktop-artifact-kind" aria-hidden="true">{file.name.split('.').pop()?.slice(0, 6).toUpperCase()}</span>
        <strong>{file.name}</strong>
      </div>
      <nav aria-label={t('surface.options')}>
        <button type="button" onClick={() => post('downloadFile')} title={t('files.download')} aria-label={t('files.download')}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v9m-3-3 3 3 3-3M4 13v3h12v-3" /></svg>
        </button>
        <button type="button" onClick={() => post('maximizeWidget')} title={t(maximized ? 'desktop.restoreSize' : 'desktop.maximize')} aria-label={t(maximized ? 'desktop.restoreSize' : 'desktop.maximize')}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d={maximized ? 'M7 7h10v10H7zM3 13V3h10' : 'M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4'} /></svg>
        </button>
        <button type="button" onClick={() => post('closeWidget')} title={t('files.close')} aria-label={t('files.close')}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M5 15 15 5" /></svg>
        </button>
      </nav>
    </header>
    {layoutFailed && <p className="desktop-artifact-error" role="status">{t('componentUnavailable')}</p>}
    <div className="desktop-artifact-content"><FilePreview file={file} onObservation={onObservation} /></div>
    <span className="desktop-artifact-resize" aria-hidden="true"><svg viewBox="0 0 18 18"><path d="m6 14 8-8m-4 8 4-4" /></svg></span>
  </section>;
}
