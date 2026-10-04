/** Keep host dialogs usable above generated surfaces without remounting them. */
export function hasNativeModal(doc: Document): boolean {
  try { return Boolean(doc.querySelector('dialog:modal')); }
  catch { return Boolean(doc.querySelector('dialog[open]')); }
}

export function manageModalFocus(root: HTMLElement): () => void {
  const doc = root.ownerDocument;
  const win = doc.defaultView!;
  const selector = '[role="dialog"][aria-modal="true"]';
  const focusable = 'button,input,select,textarea,a[href],summary,iframe,[tabindex],[contenteditable="true"]';
  const ownedInert = new Set<HTMLElement>();
  const addedTabindex = new Set<HTMLElement>();
  const previous = new Map<HTMLElement, HTMLElement | null>();
  let active: HTMLElement | null = null;
  let redirecting = false;
  let lastFocus = doc.activeElement instanceof win.HTMLElement ? doc.activeElement : null;

  const visible = (element: HTMLElement) => {
    if (element.closest('[hidden]')) return false;
    const style = win.getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
  };
  const canFocus = (element: HTMLElement) => !element.matches(':disabled') && !element.closest('[inert]') && visible(element);
  const controls = (dialog: HTMLElement) => [...dialog.querySelectorAll<HTMLElement>(focusable)]
    .filter(element => element.tabIndex >= 0 && canFocus(element));
  const focus = (element: HTMLElement) => {
    if (!element.hasAttribute('tabindex') && !element.matches(focusable)) {
      element.tabIndex = -1; addedTabindex.add(element);
    }
    element.focus({ preventScroll: true });
    // An opaque iframe needs its own bridge to restore the inner control.
    // The host never reads its DOM, text, or selection.
    if (element.tagName === 'IFRAME') element.dispatchEvent(new win.Event('athena:restore-focus'));
  };
  const releaseInert = () => {
    for (const element of ownedInert) element.inert = false;
    ownedInert.clear();
  };
  const isolate = (dialog: HTMLElement) => {
    // Isolate every branch outside the top dialog, including an older dialog.
    // Pre-existing inert attributes belong to their owner and are never cleared.
    let branch: Element = dialog;
    while (branch !== root && branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (sibling !== branch && sibling instanceof win.HTMLElement && !sibling.inert) {
          sibling.inert = true; ownedInert.add(sibling);
        }
      }
      branch = branch.parentElement;
    }
  };
  const refresh = () => {
    for (const element of ownedInert) if (!element.isConnected) { element.inert = false; ownedInert.delete(element); }
    for (const element of addedTabindex) if (!element.isConnected || !element.matches(selector)) {
      if (element.getAttribute('tabindex') === '-1') element.removeAttribute('tabindex');
      addedTabindex.delete(element);
    }
    const dialogs = [...root.querySelectorAll<HTMLElement>(selector)].filter(visible);
    const next = dialogs.at(-1) || null;
    if (next === active) { if (next) isolate(next); return; }
    const old = active;
    let restore = old ? previous.get(old) : null;
    const visited = new Set<HTMLElement>();
    while (restore && !restore.isConnected && !visited.has(restore)) {
      visited.add(restore);
      const ancestor = [...previous.keys()].find(dialog => dialog !== old && dialog.contains(restore!));
      restore = ancestor ? previous.get(ancestor) : null;
    }
    releaseInert();
    active = next;
    if (next) {
      if (!previous.has(next) || !previous.get(next)?.isConnected) {
        const candidate = doc.activeElement instanceof win.HTMLElement && root.contains(doc.activeElement)
          ? doc.activeElement : lastFocus;
        previous.set(next, old && !dialogs.includes(old) ? restore || null : candidate);
      }
      isolate(next);
      // Returning from a nested dialog goes back to its invoker. A newly opened
      // dialog receives focus itself, avoiding an unsolicited mobile keyboard.
      if (restore?.isConnected && next.contains(restore) && canFocus(restore)) focus(restore);
      else if (!next.contains(doc.activeElement)) focus(next);
    } else if (restore?.isConnected && root.contains(restore) && canFocus(restore)) {
      focus(restore);
    }
    for (const dialog of previous.keys()) if (!dialogs.includes(dialog)) previous.delete(dialog);
  };
  const keydown = (event: KeyboardEvent) => {
    if (!active || hasNativeModal(doc) || event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return;
    const items = controls(active);
    const current = doc.activeElement;
    if (!items.length) { event.preventDefault(); focus(active); return; }
    if (!items.includes(current as HTMLElement) || (event.shiftKey ? current === items[0] : current === items.at(-1))) {
      event.preventDefault(); focus(event.shiftKey ? items.at(-1)! : items[0]);
    }
  };
  const focusin = (event: FocusEvent) => {
    if (hasNativeModal(doc)) return; // The browser owns a native top-layer dialog.
    const target = event.target instanceof win.HTMLElement ? event.target : null;
    const entered = target?.closest<HTMLElement>(selector);
    // React autofocus can run before the mutation observer. Keep its invoker
    // instead of treating the new dialog's input as the return destination.
    if (entered && root.contains(entered) && !previous.has(entered)) previous.set(entered, lastFocus);
    if (target && root.contains(target)) lastFocus = target;
    if (!active || redirecting || active.contains(event.target as Node)) return;
    if (entered && root.contains(entered) && visible(entered) && entered !== active) return;
    redirecting = true; focus(active); redirecting = false;
  };
  const observer = new win.MutationObserver(refresh);
  // Do not observe inert/tabindex: they are our effects, not dialog lifecycle.
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-modal', 'role', 'hidden'] });
  doc.addEventListener('keydown', keydown, true);
  doc.addEventListener('focusin', focusin, true);
  refresh();
  return () => {
    observer.disconnect(); doc.removeEventListener('keydown', keydown, true); doc.removeEventListener('focusin', focusin, true);
    releaseInert();
    for (const element of addedTabindex) if (element.getAttribute('tabindex') === '-1') element.removeAttribute('tabindex');
    previous.clear(); active = null;
  };
}
