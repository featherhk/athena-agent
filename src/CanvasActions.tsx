import { useRef, type PointerEvent, type MouseEvent } from 'react';
import type { SurfaceAction, SurfaceRegion } from './surface-interactions';

/** Transparent native buttons supply touch, keyboard and VoiceOver to a free canvas. */
export default function CanvasActions({ regions, dispatch }: { regions: SurfaceRegion[]; dispatch: (action: SurfaceAction) => void }) {
  const active = useRef<{ pointer: number; id: string; x: number; y: number; moved: boolean } | null>(null);
  const suppressClick = useRef<string | null>(null);
  const lastPointer = useRef<SurfaceAction['pointer']>('mouse');
  function action(region: SurfaceRegion, kind: SurfaceAction['kind'], event: PointerEvent<HTMLButtonElement> | MouseEvent<HTMLButtonElement>) {
    const container = event.currentTarget.parentElement!.getBoundingClientRect();
    const keyboard = kind === 'activate' && event.detail === 0;
    // React's click wrapper is a MouseEvent, and older WebKit also omits the
    // pointer type from its native click. Retain it from the gesture itself.
    const pointerType = 'pointerType' in event ? event.pointerType
      : 'pointerType' in event.nativeEvent ? event.nativeEvent.pointerType : lastPointer.current;
    const pointer = pointerType === 'touch' || pointerType === 'pen' ? pointerType : 'mouse';
    if (!keyboard) lastPointer.current = pointer;
    dispatch({ id: region.id, kind,
      point: keyboard ? { x: region.rect.x + region.rect.width / 2, y: region.rect.y + region.rect.height / 2 }
        : { x: event.clientX - container.left, y: event.clientY - container.top },
      pointer: keyboard ? 'keyboard' : pointer });
  }
  return <div className="canvas-actions">{regions.map(region => <button key={region.id} type="button"
    className="canvas-action" aria-label={region.label} aria-pressed={region.pressed}
    style={{ left: region.rect.x, top: region.rect.y, width: region.rect.width, height: region.rect.height }}
    onPointerDown={event => {
      if (!event.isPrimary || event.button !== 0) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      active.current = { pointer: event.pointerId, id: region.id, x: event.clientX, y: event.clientY, moved: false };
      suppressClick.current = null; action(region, 'press', event);
    }} onPointerMove={event => {
      const held = active.current;
      if (held?.pointer !== event.pointerId || held.id !== region.id) return;
      held.moved ||= Math.hypot(event.clientX - held.x, event.clientY - held.y) > 6;
      action(region, 'move', event);
    }} onPointerUp={event => {
      const held = active.current;
      if (held?.pointer !== event.pointerId || held.id !== region.id) return;
      suppressClick.current = held.moved ? region.id : null; active.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      action(region, 'release', event);
    }} onPointerCancel={event => {
      if (active.current?.id !== region.id || active.current.pointer !== event.pointerId) return;
      active.current = null; suppressClick.current = region.id; action(region, 'cancel', event);
    }} onLostPointerCapture={event => {
      if (active.current?.id !== region.id || active.current.pointer !== event.pointerId) return;
      active.current = null; suppressClick.current = region.id; action(region, 'cancel', event);
    }} onClick={event => {
      if (event.detail !== 0 && suppressClick.current === region.id) { suppressClick.current = null; return; }
      action(region, 'activate', event);
    }} />)}</div>;
}
