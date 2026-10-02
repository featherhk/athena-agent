import { execFileSync } from 'node:child_process';
import { chmodSync } from 'node:fs';
if (!process.env.CI) {
  try {
    chmodSync('.githooks/pre-commit', 0o755);
    chmodSync('.githooks/pre-push', 0o755);
    execFileSync('git', ['config', '--local', 'core.hooksPath', '.githooks'], { stdio: 'ignore' });
    console.log('Athena repository checks installed.');
  } catch { console.log('Run npm run prepare inside a writable Git checkout to install hooks.'); }
}
