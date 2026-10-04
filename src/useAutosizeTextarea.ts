import { useLayoutEffect, useRef, type RefObject } from 'react';

/** Grow the existing controlled input without replacing it or touching its
 * focus, selection, or composition. Streaming and reading do not resize it. */
export function useAutosizeTextarea(ref: RefObject<HTMLTextAreaElement | null>, value: string, enabled: boolean, typography: string) {
  const resize = useRef<(() => void) | null>(null);
  useLayoutEffect(() => {
    const input = ref.current;
    if (!enabled || !input) return;
    let width = input.getBoundingClientRect().width;
    const measure = () => {
      const scrollTop = input.scrollTop;
      const style = getComputedStyle(input);
      const minimum = parseFloat(style.minHeight) || 0;
      const maximum = parseFloat(style.maxHeight) || 140;
      const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
      input.style.height = 'auto';
      const content = input.value ? input.scrollHeight + border : input.getBoundingClientRect().height;
      input.style.height = `${Math.min(maximum, Math.max(minimum, content))}px`;
      input.scrollTop = input.value ? scrollTop : 0;
      width = input.getBoundingClientRect().width;
    };
    resize.current = measure;
    measure();
    const observer = new ResizeObserver(() => {
      // Our own height change must not start another resize cycle.
      if (Math.abs(input.getBoundingClientRect().width - width) > .5) measure();
    });
    observer.observe(input);
    window.addEventListener('resize', measure);
    document.fonts?.addEventListener('loadingdone', measure);
    return () => {
      resize.current = null;
      observer.disconnect();
      window.removeEventListener('resize', measure);
      document.fonts?.removeEventListener('loadingdone', measure);
    };
  }, [ref, enabled]);
  useLayoutEffect(() => { resize.current?.(); }, [value, typography]);
}
