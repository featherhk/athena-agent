import { useEffect, useRef, useState } from 'react';
import type { Component } from './api';
import { useI18n } from './i18n';

export default function Widget({ component, submit }: { component: Component; submit: (text: string) => void }) {
  const { locale } = useI18n();
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(240);
  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === 'athena:resize' && Number.isFinite(event.data.height)) setHeight(Math.max(80, Math.min(1000, event.data.height)));
      if (event.data?.type === 'athena:submit' && typeof event.data.text === 'string') submit(event.data.text.slice(0, 30000));
    };
    window.addEventListener('message', listener);
    return () => window.removeEventListener('message', listener);
  }, [submit]);
  const html = `<!doctype html><html lang="${locale}" dir="${locale === 'ar' ? 'rtl' : 'ltr'}"><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'"><style>body{font:16px system-ui;color:#51465c;padding:12px}button,input,select{font:inherit}button{cursor:pointer}</style><script>window.Athena={submit:text=>parent.postMessage({type:'athena:submit',text},'*'),resize:height=>parent.postMessage({type:'athena:resize',height},'*')};</script></head><body>${component.html}</body></html>`;
  return <section className="widget"><div className="section-label">{component.title}</div><iframe ref={frame} title={component.title} sandbox="allow-scripts" srcDoc={html} style={{ height }} /></section>;
}
