// The webview side outside VS Code: the real host page (media/host) and the
// shared views (media/vendor), fed with what the helpers answer for the demo
// files, in headless Edge/Chromium (VS Code's webviews are Chromium too). A
// stand-in for acquireVsCodeApi records what the view sends back. Writes
// test/webview/out/<view>.png, and <view>-dark.png in a dark theme, and fails
// when a view throws or draws nothing.
//
//   npm run test:webview        (ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC, ROBOT_FLOW_DEMO, EDGE)
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { readGrid, readFlow } = require('../../out/backend/helpers.js');
const { readProject } = require('../../out/backend/project.js');
const { inspectGroup } = require('../../out/backend/group.js');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const OUT = path.join(HERE, 'out');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const DEMO = process.env.ROBOT_FLOW_DEMO || path.resolve(ROOT, 'no-demo');
const EDGE = process.env.EDGE || [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/chromium', '/usr/bin/google-chrome',
].find((p) => fs.existsSync(p));

const env = (cwd) => ({ python: PYTHON, robotSource: FORK, pythonPath: [], cwd, timeoutMs: 90000,
                        helpersDir: path.join(ROOT, 'python') });

const COUNTDOWN = path.join(ROOT, 'test/fixtures/countdown.flow.json');
// A run in the middle of the countdown's loop, as flow_position.py writes it.
const LIVE = { seq: 7, node: 'tick', stack: ['loop', 'tick'], since: 0, last: { node: 'pause', status: 'PASS', time: 0 },
               counts: { hello: { pass: 1, fail: 0 }, tick: { pass: 1, fail: 0 }, pause: { pass: 1, fail: 0 } },
               step: 4, trail: [[1, 'hello'], [2, 'loop'], [3, 'tick'], [4, 'pause']], done: false };
const BENCH = path.join(DEMO, 'bench_gui/bench_project/testproject.json');
const TOOLBAR = { canRun: true, runTitle: 'Run', status: 'running', text: 'Running · 3 s', live: true,
                  zoom: 'fit', motion: 'tail', hasLog: false };

const CASES = [
  { id: 'grid', entry: 'robot-grid/grid.js', read: readGrid,
    file: path.join(DEMO, 'bench_gui/bench_project/testsuites/signals_smoke.robot'), expect: ['Signal Should Be'] },
  { id: 'diagram', entry: 'flow-view/view.js', read: readFlow,
    file: path.join(DEMO, 'climate_endurance/endurance_project/flows/climate_profile.flow.json'), expect: ['<svg'] },
  { id: 'diagram-live', entry: 'flow-view/view.js', read: readFlow, file: COUNTDOWN,
    extra: { live: LIVE, zoom: 'fit', motion: 'off' }, toolbar: TOOLBAR,
    // the running step pulses, every step that ran has its count, the toolbar shows the run
    expect: ['fv-now', '✓1', 'tb-run', 'Running · 3 s'] },
  { id: 'diagram-debug', entry: 'flow-view/view.js', read: readFlow, file: COUNTDOWN,
    extra: { live: LIVE, zoom: 'fit', motion: 'off', breakpoints: ['tick', 'bye'], paused: 'tick' },
    toolbar: { ...TOOLBAR, canDebug: true, text: 'Paused (breakpoint) at tick · 3 s' },
    // a dot on every step, red where a breakpoint is; the paused step marked; Debug in the toolbar
    expect: ['class="fv-bp"', 'fv-has-bp', 'fv-paused', 'data-act="debug"', 'Paused (breakpoint) at tick'] },
  { id: 'diagram-paused', entry: 'flow-view/view.js', read: readFlow, file: COUNTDOWN,
    extra: { live: LIVE, zoom: 'fit', motion: 'off' },
    toolbar: { ...TOOLBAR, canStep: true, pausable: true, flowPaused: true, step: true, flowStop: true,
               text: 'Step mode: paused · phase Countdown, loop loop iteration 2 · 3 s' },
    // a flow paused in step mode: Next step instead of Resume, Stop with a checkpoint
    expect: ['data-act="resume"', 'Next step', 'data-act="step"', 'checkpoint is written', 'Step mode: paused'] },
  { id: 'group', entry: 'flow-view/group.js',
    read: async () => {
      const project = readProject(BENCH);
      const group = project.groups.find((g) => g.id === 'rendezvous');
      return inspectGroup(env(project.root), project, group);
    },
    file: BENCH, select: (d) => ({ members: d.members, links: d.links }), expect: ['data-member="IVI"', 'data-member="ADAS"', '2 meeting points'] },
  { id: 'group-control', entry: 'flow-view/group.js',
    read: async () => {
      const project = readProject(BENCH);
      const group = project.groups.find((g) => g.id === 'rendezvous');
      return inspectGroup(env(project.root), project, group);
    },
    file: BENCH, select: (d) => ({ members: d.members, links: d.links }),
    toolbar: { ...TOOLBAR, live: false, pausable: true, flowPaused: false, flowStop: true, text: 'Running · 12 s',
               members: [{ id: 'IVI', text: 'paused · phase Meet', paused: true }, { id: 'ADAS', text: 'running', paused: false }] },
    // a group run: Pause for all, and each member paused or resumed on its own
    expect: ['data-act="pause"', 'data-act="resume:IVI"', 'data-act="pause:ADAS"', 'tb-member-paused'] },
];

// VS Code's Dark Modern editor colours, as the workbench sets them on <html>.
const DARK = `html { --vscode-editor-background: #1f1f1f; --vscode-editor-foreground: #cccccc;
  --vscode-foreground: #cccccc; --vscode-button-background: #0078d4; --vscode-button-foreground: #ffffff;
  --vscode-button-secondaryBackground: #313131; --vscode-button-secondaryForeground: #cccccc;
  --vscode-badge-background: #616161; --vscode-badge-foreground: #f8f8f8; --vscode-descriptionForeground: #9d9d9d;
  --vscode-dropdown-background: #313131; --vscode-dropdown-foreground: #cccccc; --vscode-dropdown-border: #3c3c3c; }`;

function page(entry, selection, toolbar, dark) {
  const media = pathToFileURL(path.join(ROOT, 'media')).href;
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="${media}/host/host.css">
<style>body { width: 1200px; }${dark ? DARK : ''}</style></head><body${dark ? ' class="vscode-dark"' : ''}>
<div id="toolbar" class="toolbar" hidden></div><div id="status" class="status" hidden></div><div id="view"></div>
<script>
  window.__sent = [];
  window.acquireVsCodeApi = () => ({ postMessage: (m) => window.__sent.push(m), getState() {}, setState() {} });
  window.addEventListener('error', (e) => { document.title = 'ERROR ' + e.message; });
</script>
<script type="module">
  import { start } from '${media}/host/host.js';
  import { mount } from '${media}/vendor/${entry}';
  start(mount);
  window.postMessage({ type: 'selection', selection: ${JSON.stringify(selection)}, status: null }, '*');
  ${toolbar ? `window.postMessage({ type: 'toolbar', toolbar: ${JSON.stringify(toolbar)} }, '*');` : ''}
  setTimeout(() => {
    const view = document.getElementById('view');
    const ready = window.__sent.some((m) => m.type === 'ready');
    if (!document.title.startsWith('ERROR')) document.title = 'DONE ' + (ready ? 'ready ' : 'no-ready ') + view.innerHTML.length;
    const probe = document.createElement('template');
    probe.id = 'probe';
    probe.setAttribute('data-html', document.getElementById('toolbar').innerHTML + view.innerHTML);
    document.body.appendChild(probe);
  }, 800);
</script></body></html>`;
}

if (!EDGE) {
  console.error('No Edge/Chromium found (set EDGE).');
  process.exit(1);
}
fs.mkdirSync(OUT, { recursive: true });
let failed = 0;
const RUNS = CASES.flatMap((c) => [c, { ...c, id: `${c.id}-dark`, dark: true }]);
for (const c of RUNS) {
  const text = fs.readFileSync(c.file, 'utf8');
  const data = await c.read(env(path.dirname(c.file)), c.file, text);
  if (!data.ok) { console.log(`FAIL ${c.id}: helper: ${data.error}`); failed++; continue; }
  const selection = c.select ? c.select(data)
    : { ...data, ok: undefined, robot: undefined, editable: true, undo: false, ...(c.extra || {}) };
  const html = path.join(OUT, `${c.id}.html`);
  fs.writeFileSync(html, page(c.entry, selection, c.toolbar, c.dark));
  // A throwaway profile: no sign-in, sync or extensions of the user's browser.
  const profile = fs.mkdtempSync(path.join(OUT, 'profile-'));
  const args = ['--headless=new', '--disable-gpu', '--allow-file-access-from-files', '--hide-scrollbars',
                '--no-first-run', '--disable-sync', '--disable-extensions', `--user-data-dir=${profile}`,
                '--window-size=1240,1400', '--virtual-time-budget=3000'];
  const quiet = { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] };
  const dom = execFileSync(EDGE, [...args, '--dump-dom', pathToFileURL(html).href], quiet);
  execFileSync(EDGE, [...args, `--screenshot=${path.join(OUT, `${c.id}.png`)}`, pathToFileURL(html).href], quiet);
  fs.rmSync(profile, { recursive: true, force: true });
  const title = (/<title>([^<]*)<\/title>/.exec(dom) || [])[1] || '';
  const missing = c.expect.filter((s) => !dom.includes(s.replace(/"/g, '&quot;')) && !dom.includes(s));
  const ok = title.startsWith('DONE ready') && !missing.length;
  if (!ok) failed++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${c.id}: ${title || 'no title'}; ${missing.length ? `not found: ${missing.join(', ')}` : 'drawn'} -> test/webview/out/${c.id}.png`);
}
process.exit(failed ? 1 : 0);
