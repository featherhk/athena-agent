import test from 'node:test';
import assert from 'node:assert/strict';
import { newerClientVersion } from '../src/client-version.ts';

test('only a changed valid production generation asks the shell to refresh', () => {
  const current = '1234567890abcdef';
  assert.equal(newerClientVersion(current, { version: 'abcdef1234567890' }), 'abcdef1234567890');
  for (const value of [null, undefined, [], {}, { version: null }, { version: current }, { version: 'development' }, { version: '<script>' }]) {
    assert.equal(newerClientVersion(current, value), null);
  }
  assert.equal(newerClientVersion('development', { version: current }), null);
  assert.equal(newerClientVersion('unknown', { version: current }), null);
});
