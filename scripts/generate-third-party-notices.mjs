import { readFileSync, readdirSync, realpathSync, mkdirSync, writeFileSync, renameSync, rmSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const noticeName = /^(?:licen[cs]es?|copying|copyright|notice|authors)(?:[._-].*)?$/i;
const licenseName = /^(?:licen[cs]es?|copying)(?:[._-].*)?$/i;

function confined(path, root) {
  const actual = realpathSync(path);
  const rel = relative(root, actual);
  if (rel === '..' || rel.startsWith('../') || isAbsolute(rel)) throw new Error('Dependency notice path escapes node_modules');
  return actual;
}

function noticeFiles(directory, dependencyRoot) {
  const result = [];
  for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name, 'en'))) {
    if (!noticeName.test(entry.name)) continue;
    const path = confined(join(directory, entry.name), dependencyRoot);
    if (entry.isDirectory()) {
      const walk = folder => {
        for (const child of readdirSync(folder, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name, 'en'))) {
          const childPath = confined(join(folder, child.name), dependencyRoot);
          if (child.isDirectory()) walk(childPath);
          else if (child.isFile()) result.push(childPath);
          else throw new Error('Dependency notice directories must not contain links');
        }
      };
      walk(path);
    } else if (entry.isFile()) result.push(path);
    else throw new Error('Dependency notice files must not be links');
  }
  return result;
}

export function collectThirdPartyNotices(root) {
  const lockText = readFileSync(join(root, 'package-lock.json'), 'utf8');
  const lock = JSON.parse(lockText);
  const lockHash = createHash('sha256').update(lockText).digest('hex');
  if (!lock.packages || lock.lockfileVersion < 2) throw new Error('A package lock with installed package entries is required');
  const dependencyRoot = realpathSync(join(root, 'node_modules'));
  const sections = [];
  for (const [path, locked] of Object.entries(lock.packages).sort(([a], [b]) => a.localeCompare(b, 'en'))) {
    if (!path || locked.dev === true) continue;
    if (!path.startsWith('node_modules/') || path.split('/').includes('..') || locked.link) {
      throw new Error('Unsupported production dependency path in package lock');
    }
    const directory = confined(join(root, path), dependencyRoot);
    const metadataPath = confined(join(directory, 'package.json'), dependencyRoot);
    const installed = JSON.parse(readFileSync(metadataPath, 'utf8'));
    if (typeof locked.version !== 'string' || installed.version !== locked.version) {
      throw new Error(`Production dependency version differs from lock: ${path}; run npm ci`);
    }
    if (typeof installed.name !== 'string' || typeof installed.license !== 'string' || !installed.license.trim()
        || installed.license === 'UNLICENSED') throw new Error(`Missing redistributable license metadata: ${path}`);
    const files = noticeFiles(directory, dependencyRoot);
    if (!files.some(file => relative(directory, file).split('/').some(name => licenseName.test(name)))) {
      throw new Error(`Missing license text: ${path}`);
    }
    const texts = files.map(file => {
      const content = readFileSync(file, 'utf8');
      if (!content.trim()) throw new Error(`Empty dependency notice: ${path}/${relative(directory, file)}`);
      return `--- ${relative(directory, file)} ---\n${content.trimEnd()}\n`;
    });
    sections.push(`${installed.name}@${installed.version}\nPackage: ${path}\nDeclared license: ${installed.license}\n\n${texts.join('\n')}`);
  }
  if (!sections.length) throw new Error('No production dependency notices were collected');
  return `Athena — third-party JavaScript package notices\nPackage-lock SHA-256: ${lockHash}\n\nGenerated from package-lock.json and installed production package metadata.\nIncludes a conservative superset of production packages, including packages\nthat may be removed by bundling. Original license and copyright texts follow.\n\nThis file does not inventory Python, native library, or transitive Rust\ndependencies embedded in separately distributed binaries. Their notices\nmust be verified and distributed separately before a binary release.\n\n${sections.join('\n' + '='.repeat(72) + '\n\n')}`;
}

export function generateThirdPartyNotices(root) {
  const destination = join(root, 'dist', 'THIRD_PARTY_NOTICES.txt');
  const temporary = `${destination}.tmp`;
  try {
    const content = collectThirdPartyNotices(root);
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(temporary, content);
    renameSync(temporary, destination);
    return destination;
  } catch (error) {
    rmSync(temporary, { force: true });
    rmSync(destination, { force: true });
    throw error;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  try {
    generateThirdPartyNotices(root);
    console.log('Production dependency notices written to dist/THIRD_PARTY_NOTICES.txt');
  } catch (error) {
    console.error(`Third-party notice generation failed: ${error.message}`);
    process.exitCode = 1;
  }
}
