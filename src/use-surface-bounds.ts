import { useEffect, useState } from 'react';
import type { SurfaceBounds } from './component-layout';

export function useSurfaceBounds(enabled = true): SurfaceBounds {
  const [bounds, setBounds] = useState<SurfaceBounds>({ x: 12, y: 76, width: window.innerWidth - 24, height: Math.max(0, window.innerHeight - 220) });
  useEffect(() => {
    if (!enabled) return;
    const topbar = document.querySelector('.topbar'), composer = document.querySelector('.composer-area');
    let frame = 0;
    const measure = () => {
      frame = 0;
      const top = topbar?.getBoundingClientRect().bottom || 64;
      const bottom = Math.min(composer?.getBoundingClientRect().top || window.innerHeight - 120, window.visualViewport?.height || window.innerHeight);
      const content = document.querySelector('.workspace-panel') || document.querySelector('.conversation');
      const reading = content?.getBoundingClientRect();
      const next: SurfaceBounds = { x: 12, y: top + 12, width: Math.max(0, window.innerWidth - 24), height: Math.max(0, bottom - top - 24) };
      if (reading?.width && content) {
        const style = getComputedStyle(content);
        const left = parseFloat(style.paddingLeft) || 0, right = parseFloat(style.paddingRight) || 0;
        next.reading = { x: reading.x + left, y: next.y, width: reading.width - left - right, height: next.height };
      }
      setBounds(old => JSON.stringify(old) === JSON.stringify(next) ? old : next);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const observer = new ResizeObserver(schedule);
    const app = document.querySelector('.app');
    const changes = new MutationObserver(schedule);
    if (app) changes.observe(app, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
    if (composer) observer.observe(composer);
    if (topbar) observer.observe(topbar);
    window.addEventListener('resize', schedule); window.visualViewport?.addEventListener('resize', schedule);
    measure();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); changes.disconnect(); window.removeEventListener('resize', schedule); window.visualViewport?.removeEventListener('resize', schedule); };
  }, [enabled]);
  return bounds;
}
