// The host owns this bridge; generated content remains inside an opaque-origin iframe.
export function widgetDocument(source: string, locale: string, instance: string, theme?: { ink: string; surface: string; scheme: string }, transparent = false, surface = false): string {
  const language = locale.replace(/[^a-zA-Z-]/g, '');
  const color = /^#[\da-f]{6}$/i.test(theme?.ink || '') ? theme!.ink : '#51465c';
  const background = transparent ? 'transparent' : /^#[\da-f]{6}$/i.test(theme?.surface || '') ? theme!.surface : '#fdfbfe';
  const scheme = theme?.scheme === 'dark' ? 'dark' : 'light';
  const bridge = `(() => {
    const instance = ${JSON.stringify(instance)};
    let failure = null, previous = '', layout = null;
    let healthTimer = 0, healthDue = 0, hasVisibleContent = false, settled = false;
    let theme = {tokens:{'--ink':${JSON.stringify(color)},'--surface':${JSON.stringify(background)}},font_family:'system-ui',scheme:${JSON.stringify(scheme)}};
    const observers = new Set();
    let regions = [], interact = null, targets = [];
    const send = data => parent.postMessage({...data, instance}, '*');
    let sequence = 0;
    const requests = new Map(), subscribers = new Set();
    let lastFocused = null;
    document.addEventListener('focusin', event => { if (event.target instanceof HTMLElement) lastFocused = event.target; });
    const request = (method,args={}) => new Promise((resolve,reject) => {
      if (requests.size >= 32) { reject(new Error('surface_busy')); return; }
      const id = ++sequence;
      const timeout = setTimeout(() => { requests.delete(id); reject(new Error(method === 'request' ? 'delivery_unknown' : 'surface_timeout')); },30000);
      requests.set(id,{resolve,reject,timeout});
      send({type:'athena:surface-request',id,method,args});
    });
    const fields = () => [...document.querySelectorAll('input,textarea,select')].slice(0,128);
    const retainable = field => !['password','file','hidden','button','submit','reset','image'].includes(field.type) && !/password|one-time-code|cc-/.test(field.autocomplete || '');
    const rememberForm = () => {
      let size = 0;
      const draft = [];
      fields().forEach((field,index) => {
        if (!retainable(field)) return;
        size += field.value.length;
        if (size > 32000) return;
        draft.push({index,value:field.value,checked:Boolean(field.checked),...(field.multiple ? {selected:[...field.options].flatMap((option,index)=>option.selected?[index]:[]).slice(0,256)} : {})});
      });
      send({type:'athena:form-state', draft});
    };
    let explicitSize = false, sizeFrame = 0, reportedHeight = 0, viewportWidth = innerWidth, viewportHeight = innerHeight;
    const measureHeight = () => {
      sizeFrame = 0;
      if (document.hidden || explicitSize || ${transparent} || observers.size || !document.body) return;
      const height = Math.max(80, Math.min(1000, Math.ceil(document.body.getBoundingClientRect().height)));
      const heightOnly = innerWidth === viewportWidth && innerHeight !== viewportHeight;
      viewportWidth = innerWidth; viewportHeight = innerHeight;
      // A 100vh document must not make the iframe grow itself in a loop.
      if (heightOnly && reportedHeight && height > reportedHeight) return;
      if (height !== reportedHeight) { reportedHeight = height; send({type:'athena:resize', height}); }
    };
    const sizeSoon = () => { if (!document.hidden && !sizeFrame) sizeFrame = requestAnimationFrame(measureHeight); };
    const hasContent = () => {
      const body = document.body;
      if (!body) return false;
      if (body.innerText.trim()) return true;
      // CSS shapes need no text, canvas, or image; observe their actual boxes too.
      // Walk in document order and stop at the first visible box. Do not build
      // a NodeList/array of the entire document merely to keep its first nodes.
      let element = body.firstElementChild, visited = 0;
      while (element && visited++ < 5000) {
        const box = element.getBoundingClientRect(), style = getComputedStyle(element);
        if (box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0') return true;
        if (element.firstElementChild) { element = element.firstElementChild; continue; }
        while (element !== body && !element.nextElementSibling) element = element.parentElement;
        element = element === body ? null : element.nextElementSibling;
      }
      return false;
    };
    const reportHealth = force => {
      const report = {type:'athena:health', state:failure ? 'failed' : 'mounted', has_content:hasVisibleContent, error_code:failure};
      const signature = JSON.stringify(report);
      if (force || previous !== signature) { previous = signature; send(report); }
    };
    const clearHealthTimer = () => { if (healthTimer) clearTimeout(healthTimer); healthTimer = 0; healthDue = 0; };
    const inspect = force => {
      clearHealthTimer();
      if (document.hidden) return;
      hasVisibleContent = hasContent();
      if (hasVisibleContent && failure === 'empty_document') failure = null;
      if (!hasVisibleContent && settled && !failure) failure = 'empty_document';
      reportHealth(force);
    };
    const healthSoon = delay => {
      if (document.hidden) return;
      const due = Date.now() + delay;
      // Keep the earliest deadline: a continuous animation cannot postpone
      // inspection forever, while text removal can advance a pending check.
      if (healthTimer && healthDue <= due) return;
      clearHealthTimer(); healthDue = due;
      healthTimer = setTimeout(() => inspect(false), delay);
    };
    const fail = code => {
      if (!failure || failure === 'empty_document') {
        failure = code;
        // Failures remain observable while hidden, without forcing layout.
        if (document.hidden) reportHealth(true); else inspect(true);
      }
    };
    window.Athena = {
      get theme() { return theme; },
      submit: text => send({type:'athena:submit', text}),
      resize: height => { explicitSize = true; send({type:'athena:resize', height}); },
      observe: callback => {
        if (typeof callback !== 'function' || observers.size >= 32) throw new TypeError('observe requires a callback; at most 32 observers');
        observers.add(callback);
        send({type:'athena:observe'});
        if (layout) callback(layout);
        return () => { observers.delete(callback); if (!observers.size) send({type:'athena:unobserve'}); };
      },
      styleText: effect => send({type:'athena:style-text', effect}),
      track: next => {
        if (next !== null && (!Array.isArray(next) || next.length > 32)) throw new TypeError('track requires at most 32 text targets');
        const copy = JSON.parse(JSON.stringify(next || []));
        if (JSON.stringify(copy) === JSON.stringify(targets)) return;
        targets = copy;
        send({type:'athena:track', targets});
      },
      interact: (next, callback) => {
        if (next !== null && (!Array.isArray(next) || next.length > 64)) throw new TypeError('interact requires at most 64 regions');
        if (callback !== undefined && typeof callback !== 'function') throw new TypeError('interact callback must be a function');
        regions = next || [];
        if (callback !== undefined) interact = callback;
        if (next === null) interact = null;
        send({type:'athena:interactions', regions});
      },
      navigate: sessionId => {
        if (navigator.userActivation && !navigator.userActivation.isActive) return false;
        if (typeof sessionId !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(sessionId)) return false;
        send({type:'athena:navigate', session_id:sessionId});
        return true;
      }
    };
    if (${surface}) window.Athena.surface = Object.freeze({
      snapshot: () => request('snapshot'),
      messages: args => request('messages',args),
      file: file_id => request('file',{file_id}),
      draft: () => request('draft'),
      writeDraft: args => request('writeDraft',args),
      request: args => request('request',args),
      subscribe: callback => {
        if (typeof callback !== 'function' || subscribers.size >= 32) throw new TypeError('invalid_subscriber');
        subscribers.add(callback); return () => subscribers.delete(callback);
      }
    });
    addEventListener('error', event => fail(event instanceof ErrorEvent ? 'script_error' : 'blocked_resource'), true);
    addEventListener('unhandledrejection', () => fail('unhandled_rejection'));
    addEventListener('securitypolicyviolation', () => fail('blocked_resource'));
    addEventListener('message', event => {
      if (event.source !== parent || event.data?.instance !== instance) return;
      if (event.data.type === 'athena:restore-focus' && document.hasFocus() && lastFocused?.isConnected) lastFocused.focus({preventScroll:true});
      if (${surface} && event.data.type === 'athena:surface-response') {
        const pending = requests.get(event.data.id);
        if (pending) { requests.delete(event.data.id); clearTimeout(pending.timeout);
          if (event.data.error) pending.reject(new Error(event.data.error)); else pending.resolve(event.data.value); }
      }
      if (${surface} && event.data.type === 'athena:surface-changed') {
        for (const callback of subscribers) { try { callback({revision:event.data.revision}); } catch { fail('script_error'); } }
      }
      if (event.data.type === 'athena:restore-form' && Array.isArray(event.data.draft)) {
        const controls = fields();
        for (const saved of event.data.draft.slice(0,128)) {
          const field = controls[saved.index];
          if (!field || !retainable(field)) continue;
          field.value = saved.value; if ('checked' in field) field.checked = saved.checked;
          if (field.multiple && saved.selected) [...field.options].forEach((option,index)=>option.selected=saved.selected.includes(index));
        }
      }
      if (event.data.type === 'athena:inspect') {
        inspect(true);
        if (observers.size) send({type:'athena:observe'});
        send({type:'athena:track', targets});
        send({type:'athena:interactions', regions});
      }
      if (event.data.type === 'athena:interaction' && interact && regions.some(region => region.id === event.data.action?.id)) {
        try { interact(event.data.action); } catch { fail('script_error'); }
      }
      if (event.data.type === 'athena:layout') {
        layout = event.data.layout;
        for (const callback of observers) {
          try { callback(layout); } catch { fail('script_error'); }
        }
      }
      if (event.data.type === 'athena:theme' && event.data.theme) {
        theme = event.data.theme;
        for (const name of ['--canvas','--surface','--wash','--tint','--ink','--muted','--accent','--on-accent']) {
          if (typeof theme.tokens?.[name] === 'string') document.documentElement.style.setProperty(name, theme.tokens[name]);
        }
        if (typeof theme.font_family === 'string') document.documentElement.style.setProperty('--font', theme.font_family);
        document.documentElement.style.colorScheme = theme.scheme === 'dark' ? 'dark' : 'light';
      }
    });
    addEventListener('DOMContentLoaded', () => {
      inspect(true);
      new ResizeObserver(() => { sizeSoon(); healthSoon(500); }).observe(document.body);
      sizeSoon();
      document.addEventListener('input', rememberForm);
      document.addEventListener('change', rememberForm);
      new MutationObserver(records => {
        // Attributes can affect visibility through arbitrary CSS selectors,
        // so retain bounded checks instead of ignoring animated styles.
        healthSoon(records.some(record => record.type !== 'attributes') ? 50 : 500);
      }).observe(document.body, {childList:true, subtree:true, characterData:true, attributes:true});
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
          clearHealthTimer();
          if (sizeFrame) cancelAnimationFrame(sizeFrame);
          sizeFrame = 0;
        } else { inspect(true); sizeSoon(); }
      });
      setTimeout(() => { settled = true; inspect(false); }, 3000);
    });
  })();`;
  return `<!doctype html><html lang="${language}" dir="${language === 'ar' ? 'rtl' : 'ltr'}"><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'"><style>:root{--canvas:${background};--surface:${background};--wash:#f5edf9;--tint:#eadef3;--ink:${color};--muted:#82738f;--accent:#9570bd;--on-accent:#fff;--font:system-ui;color-scheme:${scheme}}*{box-sizing:border-box}body{font:16px/1.5 var(--font);color:var(--ink);background:${transparent ? 'transparent' : 'var(--surface)'};margin:0;padding:${surface ? '0' : '12px'}}button,input,select,textarea{font:inherit;color:inherit}button{cursor:pointer;background:var(--wash);border:1px solid var(--tint);border-radius:10px;padding:8px 12px}input,select,textarea{background:var(--surface);border:1px solid var(--tint);border-radius:8px;padding:8px}button:hover{border-color:var(--accent)}a{color:var(--accent)}:focus-visible{outline:2px solid var(--accent);outline-offset:3px}</style><script>${bridge}</script></head><body>${source}</body></html>`;
}
