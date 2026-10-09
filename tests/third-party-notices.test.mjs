import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { collectThirdPartyNotices, generateThirdPartyNotices } from '../scripts/generate-third-party-notices.mjs';

function fixture(t, options = {}) {
  const root = mkdtempSync(join(tmpdir(), 'athena-notices-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const directory = join(root, 'node_modules/example');
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(root, 'package-lock.json'), JSON.stringify({ lockfileVersion: 3, packages: {
    '': { name: 'fixture' },
    'node_modules/example': { version: '1.0.0' },
    'node_modules/dev-only': { version: '1.0.0', dev: true },
  } }));
  writeFileSync(join(directory, 'package.json'), JSON.stringify({ name: 'example', version: '1.0.0', license: 'MIT', ...options }));
  writeFileSync(join(directory, 'LICENSE'), 'Copyright Example Author\nLicense terms must survive.\n');
  writeFileSync(join(directory, 'NOTICE'), 'Additional attribution must survive.\n');
  return { root, directory };
}

test('notices retain original text and versions, skip dev packages, and are deterministic', t => {
  const { root } = fixture(t);
  const first = collectThirdPartyNotices(root);
  assert.equal(first, collectThirdPartyNotices(root));
  assert.match(first, /example@1\.0\.0/);
  assert.match(first, /Copyright Example Author\nLicense terms must survive\./);
  assert.match(first, /Additional attribution must survive\./);
  assert.doesNotMatch(first, /dev-only|athena-notices-/);
  assert.equal(readFileSync(generateThirdPartyNotices(root), 'utf8'), first);
});

test('missing license text fails and removes stale output', t => {
  const { root, directory } = fixture(t);
  const output = generateThirdPartyNotices(root);
  rmSync(join(directory, 'LICENSE'));
  assert.throws(() => generateThirdPartyNotices(root), /Missing license text/);
  assert.equal(existsSync(output), false);
});

test('wrong installed version and absent license metadata fail closed', t => {
  const mismatch = fixture(t, { version: '2.0.0' });
  assert.throws(() => collectThirdPartyNotices(mismatch.root), /differs from lock/);
  const missing = fixture(t, { license: undefined });
  assert.throws(() => collectThirdPartyNotices(missing.root), /license metadata/);
});

test('a package can retain a directory of upstream license texts', t => {
  const { root, directory } = fixture(t);
  rmSync(join(directory, 'LICENSE'));
  mkdirSync(join(directory, 'LICENSES'));
  writeFileSync(join(directory, 'LICENSES/MIT.txt'), 'Nested upstream permission text.');
  assert.match(collectThirdPartyNotices(root), /Nested upstream permission text\./);
});

test('notice links cannot read files outside installed dependencies', t => {
  const { root, directory } = fixture(t);
  const outside = join(root, 'private-fixture');
  writeFileSync(outside, 'This fixture must never be copied.');
  rmSync(join(directory, 'LICENSE'));
  symlinkSync(outside, join(directory, 'LICENSE'));
  assert.throws(() => collectThirdPartyNotices(root), /escapes node_modules/);
});
