import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { Component, Placement, ViewChange } from './api';
import { placementOf } from './component-layout';
import { useI18n } from './i18n';

const positions: Placement[] = ['surface', 'inline', 'workspace', 'sidebar-start', 'sidebar-end', 'top-start', 'top-end', 'bottom-start', 'bottom-end', 'banner', 'overlay', 'fullscreen', 'presence'];

export default function ComponentControls({ component, change, busy = false }: { component: Component; change: (view: ViewChange) => void; busy?: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});
  const root = useRef<HTMLDivElement>(null);
  function toggle() {
    if (open) { setOpen(false); return; }
    const rect = root.current!.getBoundingClientRect();
    const top = (document.querySelector('.topbar')?.getBoundingClientRect().bottom || 64) + 8;
    const bottom = (document.querySelector('.composer-area')?.getBoundingClientRect().top || window.innerHeight) - 8;
    const height = Math.min(350, Math.max(120, bottom - top));
    setPanelStyle({ position: 'fixed', width: Math.min(250, window.innerWidth - 24), left: Math.max(12, Math.min(rect.right - 250, window.innerWidth - 262)), top: Math.max(top, Math.min(rect.bottom + 5, bottom - height)), maxHeight: height });
    setOpen(true);
  }
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', escape);
    const resize = () => setOpen(false);
    window.addEventListener('resize', resize);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); window.removeEventListener('resize', resize); };
  }, [open]);
  const placement = placementOf(component);
  const sized = !['surface', 'workspace', 'fullscreen', 'presence', 'overlay'].includes(placement);
  return <div className="component-controls" ref={root}>
    <button type="button" className="surface-icon" title={t('surface.options')} aria-label={t('surface.options')} aria-expanded={open} onClick={toggle}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M9 4v6m6 4v6" /></svg></button>
    <button type="button" className="surface-icon" title={t('surface.hide')} aria-label={t('surface.hide')} disabled={busy} onClick={() => change({ hidden: true })}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M7 17 17 7" /></svg></button>
    {open && <div className="component-options" style={panelStyle}>
      <label>{t('surface.position')}<select aria-label={t('surface.position')} value={placement} disabled={busy} onChange={event => { change({ placement: event.target.value as Placement, x: null, y: null }); setOpen(false); }}>{positions.map(position => <option key={position} value={position}>{t(`surface.${position}`)}</option>)}</select></label>
      {sized && !placement.startsWith('sidebar') && <label>{t('surface.width')}<input type="range" min="240" max="1200" step="20" defaultValue={component.view?.width || 320} aria-label={t('surface.width')} disabled={busy} onPointerUp={event => change({ width: Number(event.currentTarget.value) })} onKeyUp={event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) change({ width: Number(event.currentTarget.value) }); }} /></label>}
      {sized && <label>{t('surface.height')}<input type="range" min="160" max="900" step="20" defaultValue={component.view?.height || 300} aria-label={t('surface.height')} disabled={busy} onPointerUp={event => change({ height: Number(event.currentTarget.value) })} onKeyUp={event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) change({ height: Number(event.currentTarget.value) }); }} /></label>}
      <button className="quiet" disabled={busy} onClick={() => { change({ placement: null, x: null, y: null, width: null, height: null }); setOpen(false); }}>{t('surface.default')}</button>
    </div>}
  </div>;
}
