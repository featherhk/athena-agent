import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

function sourceVersion() {
  const root = import.meta.dirname;
  const hash = createHash('sha256');
  function add(path: string) { hash.update(path); hash.update('\0'); hash.update(readFileSync(resolve(root, path))); hash.update('\0'); }
  function walk(path: string) {
    for (const entry of readdirSync(resolve(root, path), { withFileTypes: true }).sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
      const child = `${path}/${entry.name}`;
      if (entry.isDirectory()) walk(child);
      else if (entry.isFile()) add(child);
    }
  }
  walk('src');
  for (const path of ['index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts']) add(path);
  return hash.digest('hex').slice(0, 24);
}

export default defineConfig(({ command }) => {
  const version = command === 'serve' ? 'development' : sourceVersion();
  return {
    plugins: [react(), { name: 'athena-client-version', generateBundle(_options, bundle) {
      const entry = Object.values(bundle).find(item => item.type === 'chunk' && item.isEntry);
      this.emitFile({ type: 'asset', fileName: 'client-version.json', source: JSON.stringify({ version, entry: entry?.fileName }) });
    } }],
    define: { __ATHENA_CLIENT_VERSION__: JSON.stringify(version) },
    server: { port: 5173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:4317' } },
  };
});
