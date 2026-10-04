import test from 'node:test';
import assert from 'node:assert/strict';
import { prohibitedPath, containsCredential } from '../scripts/check-repository.mjs';

test('rejects runtime data but permits source and lockfiles', () => {
  for (const path of ['.env', '.env.local', '.npmrc', 'nested/test.sqlite3-wal', 'data/.env.example', 'data/private.txt', 'browser-profiles/a.txt', 'cookies.json', 'private.key', '.venv/bin/python', 'logs/a.json', 'recharge.json', 'nested/btcpay-api-key', 'btcpay-webhook-secret']) assert.equal(prohibitedPath(path), true, path);
  for (const path of ['.env.example', 'package-lock.json', 'uv.lock', 'backend/athena/schema.sql', 'src/App.tsx']) assert.equal(prohibitedPath(path), false, path);
});
test('detects credentials without storing real credentials', () => {
  assert.equal(containsCredential('sk-' + 'x'.repeat(32)), true);
  assert.equal(containsCredential('github_pat_' + 'x'.repeat(40)), true);
  assert.equal(containsCredential('DEEPSEEK_API_KEY='), false);
});
