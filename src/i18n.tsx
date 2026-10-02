import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import en from './locales/en.json';
import { errorKeys, isLocale, resolveLocale } from './locale-core';
import type { Locale } from './locale-core';
export { languages } from './locale-core';
export type { Locale } from './locale-core';
const dictionaries = import.meta.glob('./locales/*.json', { eager: true, import: 'default' }) as Record<string, Record<string, string>>;
export type Key = keyof typeof en;
type Values = Record<string, string | number>;
function preference() { try { return localStorage.getItem('athena-language') || 'system'; } catch { return 'system'; } }
export function currentLocale(): Locale {
  const value = preference();
  return isLocale(value) ? value : resolveLocale(navigator.languages?.length ? navigator.languages : [navigator.language]);
}
export function translate(key: Key, values: Values = {}, locale = currentLocale()): string {
  const template = dictionaries[`./locales/${locale}.json`]?.[key] ?? en[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? `{${name}}`));
}
export function errorMessage(code?: string): string {
  const key = errorKeys[code || ''] || (code?.startsWith('provider_') ? 'error.provider' : 'error.generic');
  return translate(key as Key);
}
const Context = createContext({ locale: 'en' as Locale, choice: 'system', setChoice: (_value: string) => {}, reduced: false, setReduced: (_value: boolean) => {}, t: translate });
export function I18nProvider({ children }: { children: ReactNode }) {
  const [choice, update] = useState(preference);
  const [locale, setLocale] = useState(currentLocale);
  const [reduced, setReducedState] = useState(() => { try { return localStorage.getItem('athena-reduced-motion') === 'true'; } catch { return false; } });
  const setChoice = (value: string) => {
    if (value !== 'system' && !isLocale(value)) return;
    try { localStorage.setItem('athena-language', value); } catch { /* A private browser can still change this tab. */ }
    update(value); setLocale(isLocale(value) ? value : resolveLocale(navigator.languages));
  };
  const setReduced = (value: boolean) => {
    setReducedState(value);
    try { localStorage.setItem('athena-reduced-motion', String(value)); } catch { /* Session-only fallback. */ }
  };
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === 'ar' ? 'rtl' : 'ltr';
  }, [locale]);
  useEffect(() => { document.documentElement.dataset.reduceMotion = String(reduced); }, [reduced]);
  useEffect(() => {
    const change = () => { if (choice === 'system') setLocale(resolveLocale(navigator.languages)); };
    window.addEventListener('languagechange', change);
    return () => window.removeEventListener('languagechange', change);
  }, [choice]);
  return <Context.Provider value={{ locale, choice, setChoice, reduced, setReduced, t: (key, values) => translate(key, values, locale) }}>{children}</Context.Provider>;
}
export const useI18n = () => useContext(Context);
