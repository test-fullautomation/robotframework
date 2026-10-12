// Smoke test in a real VS Code: the extension activates, both editors open on
// the demo files, and each view gets its data without an error.
//
// VS Code runs extension tests by itself (--extensionDevelopmentPath,
// --extensionTestsPath) and exits with the result, so no test runner package
// is needed. A throwaway profile keeps the user's settings and extensions out.
//
//   npm run test:vscode     (VSCODE_EXE, ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC, ROBOT_FLOW_DEMO)
//
// Debugging into Python needs VS Code's Python debugger: the newest installed
// ms-python.debugpy and ms-python.python (its dependency) are copied into the
// throwaway profile, read-only from the user's extensions folder
// (ROBOT_FLOW_NO_PYTHON=1: without them; the Python step is then skipped).
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const DEMO = process.env.ROBOT_FLOW_DEMO || path.resolve(ROOT, 'no-demo');
const EXE = process.env.VSCODE_EXE || [
  'C:/Program Files/Microsoft VS Code/Code.exe',
  path.join(os.homedir(), 'AppData/Local/Programs/Microsoft VS Code/Code.exe'),
  '/usr/share/code/code', '/usr/bin/code',
].find((p) => fs.existsSync(p));

if (!fs.existsSync(path.join(DEMO, 'bench_gui')) || !fs.existsSync(path.join(DEMO, 'climate_endurance'))) {
  console.error('The smoke test opens the demo projects: set ROBOT_FLOW_DEMO to the folder with bench_gui/ and climate_endurance/.');
  process.exit(1);
}
if (!EXE) {
  console.error('No VS Code found (set VSCODE_EXE to its executable).');
  process.exit(1);
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'robot-flow-vscode-'));
const userDir = path.join(profile, 'user');
fs.mkdirSync(path.join(userDir, 'User'), { recursive: true });
fs.writeFileSync(path.join(userDir, 'User', 'settings.json'), JSON.stringify({
  'robotFlow.python': process.env.ROBOT_FLOW_PYTHON || 'python',
  'robotFlow.robotSource': process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src'),
  'workbench.startupEditor': 'none',
  'security.workspace.trust.enabled': false,
  'update.mode': 'none',
  'telemetry.telemetryLevel': 'off',
}, null, 2));

/** The newest installed copy of an extension, or null. */
function installed(id) {
  const dir = path.join(os.homedir(), '.vscode', 'extensions');
  const version = (name) => (name.slice(id.length + 1).match(/^\d+(\.\d+)*/) || ['0'])[0].split('.').map(Number);
  const newer = (a, b) => {
    const [x, y] = [version(a), version(b)];
    for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0);
    return 0;
  };
  const found = fs.existsSync(dir) ? fs.readdirSync(dir).filter((n) => n.toLowerCase().startsWith(id + '-')) : [];
  return found.length ? path.join(dir, found.sort(newer).pop()) : null;
}

const extDir = path.join(profile, 'ext');
fs.mkdirSync(extDir, { recursive: true });
const pythonExtensions = process.env.ROBOT_FLOW_NO_PYTHON ? [] : ['ms-python.python', 'ms-python.debugpy'].map(installed);
const withPython = pythonExtensions.length === 2 && pythonExtensions.every(Boolean);
if (withPython) for (const src of pythonExtensions) fs.cpSync(src, path.join(extDir, path.basename(src)), { recursive: true });

const args = [
  DEMO,
  `--extensionDevelopmentPath=${ROOT}`,
  `--extensionTestsPath=${path.join(HERE, 'suite.js')}`,
  `--user-data-dir=${userDir}`,
  `--extensions-dir=${extDir}`,
  // Only the copied Python extensions are in that folder; without them, keep every other one off.
  ...(withPython ? [] : ['--disable-extensions']),
  '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust',
  '--new-window',
];
const env = { ...process.env, ROBOT_FLOW_DEMO: DEMO, ROBOT_FLOW_WITH_PYTHON: withPython ? '1' : '' };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(EXE, args, { env, stdio: ['ignore', 'pipe', 'pipe'] });
const timer = setTimeout(() => { console.error('VS Code smoke test: timed out after 4 min'); child.kill(); }, 240000);
child.stdout.on('data', (d) => {
  for (const line of d.toString().split(/\r?\n/)) if (/^(ok |not ok|Error|AssertionError)/.test(line)) console.log(line);
});
child.stderr.on('data', (d) => {
  const text = d.toString();
  if (/AssertionError|Error:|timed out/.test(text)) process.stderr.write(text);
});
child.on('close', (code) => {
  clearTimeout(timer);
  fs.rmSync(profile, { recursive: true, force: true });
  console.log(code === 0 ? 'VS Code smoke test passed' : `VS Code smoke test failed (exit ${code})`);
  process.exit(code === 0 ? 0 : 1);
});
