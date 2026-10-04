import { useEffect, useRef, type KeyboardEvent, type PointerEvent, type TouchEvent, type UIEvent, type WheelEvent } from 'react';
import { ConversationScroll, nestedScrollConsumes, scrollKeyDirection } from './conversation-scroll';

export function useConversationScroll(setReading: (reading: boolean) => void) {
  const ref = useRef<HTMLElement>(null);
  const policy = useRef(new ConversationScroll()).current;
  const touchY = useRef<number | null>(null);
  useEffect(() => {
    const end = () => policy.endDrag();
    window.addEventListener('pointerup', end); window.addEventListener('pointercancel', end); window.addEventListener('blur', end);
    return () => { window.removeEventListener('pointerup', end); window.removeEventListener('pointercancel', end); window.removeEventListener('blur', end); };
  }, [policy]);
  function intent(direction: number, target: EventTarget | null, container: HTMLElement) {
    if (direction && !nestedScrollConsumes(target, container, direction)) policy.intent(direction, container);
  }
  const handlers = {
    onWheel(event: WheelEvent<HTMLElement>) { intent(Math.sign(event.deltaY), event.target, event.currentTarget); },
    onTouchStart(event: TouchEvent<HTMLElement>) { touchY.current = event.touches.length === 1 ? event.touches[0].clientY : null; },
    onTouchMove(event: TouchEvent<HTMLElement>) {
      if (event.touches.length !== 1) { touchY.current = null; return; }
      const next = event.touches[0].clientY;
      if (touchY.current !== null) intent(Math.sign(touchY.current - next), event.target, event.currentTarget);
      touchY.current = next;
    },
    onTouchEnd() { touchY.current = null; },
    onTouchCancel() { touchY.current = null; },
    onPointerDown(event: PointerEvent<HTMLElement>) {
      if (event.button !== 0 || event.pointerType === 'touch' || event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      // Includes macOS overlay scrollbars, which do not consume clientWidth.
      if (event.clientX >= bounds.right - 20 || event.clientX <= bounds.left + 20) policy.startDrag(event.currentTarget);
    },
    onKeyDown(event: KeyboardEvent<HTMLElement>) {
      intent(scrollKeyDirection({ ...event, isComposing: event.nativeEvent.isComposing }, event.target), event.target, event.currentTarget);
    },
    onScroll(event: UIEvent<HTMLElement>) {
      const container = event.currentTarget;
      policy.observe(container);
      if (container.scrollTop > 80) setReading(true);
      else if (container.scrollTop <= 0 && policy.recentlyInteracted()) setReading(false);
    },
  };
  return { ref, policy, handlers, follow: () => { if (ref.current) policy.follow(ref.current); } };
}
