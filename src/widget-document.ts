// The host owns this bridge; generated content remains inside an opaque-origin iframe.
export function widgetDocument(source: string, locale: string, instance: string, theme?: { ink: string; surface: string; scheme: string }, transparent = false): string {
  const language = locale.replace(/[^a-zA-Z-]/g, '');
  const color = /^#[\da-f]{6}$/i.test(theme?.ink || '') ? theme!.ink : '#51465c';
  const background = transparent ? 'transparent' : /^#[\da-f]{6}$/i.test(theme?.surface || '') ? theme!.surface : '#fdfbfe';
  const scheme = theme?.scheme === 'dark' ? 'dark' : 'light';
  const bridge = `(() => {
    const instance = ${JSON.stringify(instance)};
    let failure = null, previous = '', scheduled = false, layout = null;
    let theme = {tokens:{'--ink':${JSON.stringify(color)},'--surface':${JSON.stringify(background)}},font_family:'system-ui',scheme:${JSON.stringify(scheme)}};
    const observers = new Set();
    let regions = [], interact = null, targets = [];
    const send = data => parent.postMessage({...data, instance}, '*');
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
      if (explicitSize || ${transparent} || observers.size || !document.body) return;
      const height = Math.max(80, Math.min(1000, Math.ceil(document.body.getBoundingClientRect().height)));
      const heightOnly = innerWidth === viewportWidth && innerHeight !== viewportHeight;
      viewportWidth = innerWidth; viewportHeight = innerHeight;
      // A 100vh document must not make the iframe grow itself in a loop.
      if (heightOnly && reportedHeight && height > reportedHeight) return;
      if (height !== reportedHeight) { reportedHeight = height; send({type:'athena:resize', height}); }
    };
    const sizeSoon = () => { if (!sizeFrame) sizeFrame = requestAnimationFrame(measureHeight); };
    const hasContent = () => {
      const body = document.body;
      if (!body) return false;
      if (body.innerText.trim()) return true;
      // CSS shapes need no text, canvas, or image; observe their actual boxes too.
      return [...body.querySelectorAll('*')].slice(0,5000)
        .some(element => {
          const box = element.getBoundingClientRect(), style = getComputedStyle(element);
          return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
        });
    };
    const inspect = force => {
      scheduled = false;
      const report = {type:'athena:health', state:failure ? 'failed' : 'mounted', has_content:hasContent(), error_code:failure};
      const signature = JSON.stringify(report);
      if (force || previous !== signature) { previous = signature; send(report); }
    };
    const fail = code => { if (!failure || failure === 'empty_document') { failure = code; inspect(true); } };
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
    addEventListener('error', event => fail(event instanceof ErrorEvent ? 'script_error' : 'blocked_resource'), true);
    addEventListener('unhandledrejection', () => fail('unhandled_rejection'));
    addEventListener('securitypolicyviolation', () => fail('blocked_resource'));
    addEventListener('message', event => {
      if (event.source !== parent || event.data?.instance !== instance) return;
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
      new ResizeObserver(sizeSoon).observe(document.body);
      sizeSoon();
      document.addEventListener('input', rememberForm);
      document.addEventListener('change', rememberForm);
      new MutationObserver(() => {
        if (!scheduled) {
          scheduled = true;
          setTimeout(() => { if (failure === 'empty_document' && hasContent()) failure = null; inspect(false); }, 50);
        }
      }).observe(document.body, {childList:true, subtree:true, characterData:true, attributes:true});
      setTimeout(() => { if (!hasContent()) fail('empty_document'); }, 3000);
    });
  })();`;
  return `<!doctype html><html lang="${language}" dir="${language === 'ar' ? 'rtl' : 'ltr'}"><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'"><style>:root{--canvas:${background};--surface:${background};--wash:#f5edf9;--tint:#eadef3;--ink:${color};--muted:#82738f;--accent:#9570bd;--on-accent:#fff;--font:system-ui;color-scheme:${scheme}}*{box-sizing:border-box}body{font:16px/1.5 var(--font);color:var(--ink);background:${transparent ? 'transparent' : 'var(--surface)'};margin:0;padding:12px}button,input,select,textarea{font:inherit;color:inherit}button{cursor:pointer;background:var(--wash);border:1px solid var(--tint);border-radius:10px;padding:8px 12px}input,select,textarea{background:var(--surface);border:1px solid var(--tint);border-radius:8px;padding:8px}button:hover{border-color:var(--accent)}a{color:var(--accent)}:focus-visible{outline:2px solid var(--accent);outline-offset:3px}</style><script>${bridge}</script></head><body>${source}</body></html>`;
}
