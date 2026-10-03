import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

export function prohibitedPath(path) {
  const parts = path.toLowerCase().split('/');
  const base = parts.at(-1);
  return parts.some(p => ['node_modules', '.venv', '__pycache__', '.uv-cache', 'dist', '.athena', '.data', 'data', 'runtime', 'storage', 'artifacts', 'account-avatars', 'uploads', 'downloads', 'backups', 'logs', 'sessions', 'browser-profiles', 'profiles', '.auth', '.codex'].includes(p))
    || (base !== '.env.example' && /^\.env(?:\.|$)/i.test(base))
    || ['.npmrc', '.pypirc', '.envrc'].includes(base)
    || /\.(db|sqlite|sqlite3)(?:-.*)?$/i.test(base)
    || /\.(pem|key|p12|pfx|log|jsonl|har|dmg|ipa|mobileprovision)$/i.test(base)
    || parts.some(p => ['xcuserdata', 'deriveddata'].includes(p))
    || parts.some(p => ['.ssh', '.secrets'].includes(p) || /\.(app|xcarchive)$/.test(p))
    || /^(auth|credentials|secrets)\.json$/i.test(base)
    || /^(cookies.*|.*storage-state.*)\.json$/i.test(base);
}

export function containsCredential(content) {
  return /\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,})\b/.test(content)
    || /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/.test(content);
}

if (process.argv[1]?.endsWith('check-repository.mjs')) {
  const git = args => execFileSync('git', args, { maxBuffer: 32 * 1024 * 1024 }).toString();
  const staged = process.argv.includes('--staged');
  const history = process.argv.includes('--history');
  const files = git(staged ? ['diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'] : ['ls-files', '-z']).split('\0').filter(Boolean);
  const failures = new Set();
  for (const path of files) {
    if (prohibitedPath(path)) failures.add(`${path}: runtime data or private configuration`);
    try {
      const content = staged ? git(['show', `:${path}`]) : readFileSync(path, 'utf8');
      if (containsCredential(content)) failures.add(`${path}: possible credential`);
    } catch { /* Deleted tracked files are checked by Git itself. */ }
  }
  if (history) {
    const objects = git(['rev-list', '--objects', '--all']).trim().split('\n');
    for (const line of objects) {
      const space = line.indexOf(' ');
      if (space < 0) continue;
      const sha = line.slice(0, space), path = line.slice(space + 1);
      if (prohibitedPath(path)) failures.add(`${path}: private data in commit history`);
      if (git(['cat-file', '-t', sha]).trim() === 'blob' && containsCredential(git(['cat-file', 'blob', sha]))) {
        failures.add(`${path}: possible credential in commit history`);
      }
    }
  }
  if (failures.size) {
    console.error('Push/commit blocked. Keep private data outside Git:\n' + [...failures].join('\n'));
    process.exitCode = 1;
  } else console.log(`Repository data check passed (${files.length} files${history ? ', including history' : ''}).`);
}
