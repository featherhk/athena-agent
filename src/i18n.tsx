import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import type { ReactNode } from 'react';
import type { Preferences } from './api';
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
const defaults: Preferences = { ui_language: 'system', ui_font: 'system', ui_palette: 'dream', reduced_motion: 'system' };
const Context = createContext({ preferences: defaults, applyPreferences: (_value: Partial<Preferences>) => {}, locale: 'en' as Locale, choice: 'system', setChoice: (_value: string) => {}, reduced: false, setReduced: (_value: boolean) => {}, t: translate });
export function I18nProvider({ children }: { children: ReactNode }) {
  const [preferences, setPreferences] = useState<Preferences>(() => ({ ...defaults, ui_language: preference() }));
  const [choice, update] = useState(preference);
  const [locale, setLocale] = useState(currentLocale);
  const [reduced, setReducedState] = useState(() => { try { return localStorage.getItem('athena-reduced-motion') === 'true'; } catch { return false; } });
  const applyPreferences = useCallback((value: Partial<Preferences>) => {
    setPreferences(old => ({ ...old, ...value }));
    if (value.ui_language === 'system' || isLocale(value.ui_language || '')) {
      const language = value.ui_language!;
      try { localStorage.setItem('athena-language', language); } catch { /* Session-only fallback. */ }
      update(language); setLocale(isLocale(language) ? language : resolveLocale(navigator.languages));
    }
    if (value.reduced_motion) {
      const reduce = value.reduced_motion === 'reduce';
      setReducedState(reduce);
      try { localStorage.setItem('athena-reduced-motion', String(reduce)); } catch { /* Session-only fallback. */ }
    }
  }, []);
  const setChoice = (value: string) => applyPreferences({ ui_language: value });
  const setReduced = (value: boolean) => applyPreferences({ reduced_motion: value ? 'reduce' : 'system' });
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === 'ar' ? 'rtl' : 'ltr';
  }, [locale]);
  useEffect(() => { document.documentElement.dataset.reduceMotion = String(reduced); }, [reduced]);
  useEffect(() => {
    document.documentElement.dataset.font = preferences.ui_font;
    document.documentElement.dataset.palette = preferences.ui_palette;
  }, [preferences.ui_font, preferences.ui_palette]);
  useEffect(() => {
    const change = () => { if (choice === 'system') setLocale(resolveLocale(navigator.languages)); };
    window.addEventListener('languagechange', change);
    return () => window.removeEventListener('languagechange', change);
  }, [choice]);
  return <Context.Provider value={{ preferences, applyPreferences, locale, choice, setChoice, reduced, setReduced, t: (key, values) => translate(key, values, locale) }}>{children}</Context.Provider>;
}
export const useI18n = () => useContext(Context);
