// Runs test/grammar.test.js with VS Code's executable as Node, so it can load
// VS Code's own TextMate engine from the install (no npm download).
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const exe = process.env.VSCODE_EXE || [
  path.join(process.env.LOCALAPPDATA || '', 'Programs/Microsoft VS Code/Code.exe'),
  'C:/Program Files/Microsoft VS Code/Code.exe',
  '/usr/share/code/code',
].find((p) => fs.existsSync(p));
if (!exe) {
  console.error('No VS Code found (set VSCODE_EXE).');
  process.exit(1);
}
// resources/app: beside the executable, or in its versioned folder.
const base = path.dirname(exe);
const app = [path.join(base, 'resources/app'),
             ...fs.readdirSync(base).map((d) => path.join(base, d, 'resources/app'))]
  .find((p) => fs.existsSync(path.join(p, 'node_modules.asar')));
const r = spawnSync(exe, [path.join(HERE, 'grammar.test.js')], {
  stdio: 'inherit', env: { ...process.env, ELECTRON_RUN_AS_NODE: '1', MB_VSCODE_APP: app },
});
process.exit(r.status ?? 1);
