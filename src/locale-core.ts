export const languages = [
  { tag: 'ar', name: 'العربية' }, { tag: 'en', name: 'English' },
  { tag: 'es', name: 'Español' }, { tag: 'fr', name: 'Français' },
  { tag: 'ja', name: '日本語' }, { tag: 'ko', name: '한국어' },
  { tag: 'pt', name: 'Português' }, { tag: 'zh-Hans', name: '简体中文' },
  { tag: 'zh-Hant', name: '繁體中文' },
] as const;
export type Locale = typeof languages[number]['tag'];
export const isLocale = (value: string): value is Locale => languages.some(item => item.tag === value);

// BCP 47 tags are the stable ordering key, independent of the current display language.
export function resolveLocale(preferences: readonly string[]): Locale {
  for (const preference of preferences) {
    try {
      const locale = new Intl.Locale(preference.replaceAll('_', '-'));
      if (locale.language === 'zh') {
        const script = locale.script || locale.maximize().script;
        return script === 'Hant' ? 'zh-Hant' : 'zh-Hans';
      }
      if (isLocale(locale.language)) return locale.language;
    } catch { /* Invalid OS/browser hints must not prevent startup. */ }
  }
  return 'en';
}

export const errorKeys: Record<string, string> = {
  invalid_avatar: 'error.avatar', invalid_password: 'error.password',
  file_not_found: 'error.file_not_found', file_too_large: 'error.file_too_large', invalid_filename: 'error.invalid_filename', file_preview_unavailable: 'error.file_preview_unavailable',
  provider_not_configured: 'error.service', model_unavailable: 'error.provider',
  provider_auth_failed: 'error.provider', provider_access_denied: 'error.provider',
  provider_balance_insufficient: 'error.provider', provider_rate_limited: 'error.provider',
  login_required: 'error.login', username_taken: 'error.usernameTaken', invalid_credentials: 'error.credentials',
  login_rate_limited: 'error.attempts', session_not_found: 'error.notFound', resetting: 'error.resetting',
  empty_message: 'error.empty', fork_busy: 'error.forkBusy', context_limit: 'error.context',
  step_limit: 'error.steps', restarted: 'error.interrupted', stream_interrupted: 'error.stream',
  generation_incomplete: 'error.incomplete', invalid_input: 'error.input', developer_required: 'error.developer',
};
