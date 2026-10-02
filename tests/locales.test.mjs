import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { languages, resolveLocale, errorKeys } from '../src/locale-core.ts';
const catalog = locale => JSON.parse(readFileSync(new URL(`../src/locales/${locale}.json`, import.meta.url), 'utf8'));
const placeholders = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();

test('language negotiation respects scripts, regional preferences and English fallback', () => {
  for (const [input, expected] of [
    [['zh-TW'], 'zh-Hant'], [['zh-HK'], 'zh-Hant'], [['zh-MO'], 'zh-Hant'], [['zh-CN'], 'zh-Hans'],
    [['zh-SG'], 'zh-Hans'], [['zh-Hant-CN'], 'zh-Hant'], [['zh-Hans-HK'], 'zh-Hans'],
    [['en-GB'], 'en'], [['en-IE'], 'en'], [['pt-BR'], 'pt'], [['pt-PT'], 'pt'],
    [['ja-JP'], 'ja'], [['ko-KR'], 'ko'], [['ar-EG'], 'ar'], [['fr-CA'], 'fr'], [['es-MX'], 'es'],
    [['de-DE', 'fr-FR'], 'fr'], [['de-DE'], 'en'], [[], 'en'], [['???'], 'en'], [['zh_TW'], 'zh-Hant'],
  ]) assert.equal(resolveLocale(input), expected, String(input));
  const tags = languages.map(item => item.tag);
  assert.deepEqual(tags, [...tags].sort());
});

test('all nine catalogs have complete keys, matching placeholders and no flag emoji', () => {
  const en = catalog('en');
  assert.equal(languages.length, 9);
  for (const { tag } of languages) {
    const translated = catalog(tag);
    assert.deepEqual(Object.keys(translated).sort(), Object.keys(en).sort(), tag);
    for (const [key, value] of Object.entries(translated)) {
      assert.equal(typeof value, 'string', `${tag}:${key}`);
      assert.ok(value.trim(), `${tag}:${key}`);
      assert.deepEqual(placeholders(value), placeholders(en[key]), `${tag}:${key}`);
      assert.equal(/\p{Regional_Indicator}/u.test(value), false, `${tag}:${key}`);
    }
  }
  for (const key of Object.values(errorKeys)) assert.ok(en[key], key);
  for (const filename of readdirSync(new URL('../src/', import.meta.url)).filter(file => file.endsWith('.tsx'))) {
    const source = readFileSync(new URL(`../src/${filename}`, import.meta.url), 'utf8');
    for (const match of source.matchAll(/\bt\('([^']+)'/g)) assert.ok(en[match[1]], `${filename}:${match[1]}`);
  }
});
