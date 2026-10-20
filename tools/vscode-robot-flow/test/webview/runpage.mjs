// The Run page outside VS Code: its HTML (src/backend/runPageHtml.ts) fed
// with the data the extension posts, in headless Edge/Chromium, light and
// dark; the flow report of the demo's run in its frame. Writes
// test/webview/out/runpage*.png and fails when the page throws or a piece
// is missing.
//
//   node test/webview/runpage.mjs      (ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC, EDGE)
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { runPageHtml, reportForFrame } = require('../../out/backend/runPageHtml.js');
const { writeFlowReport } = require('../../out/backend/report.js');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const OUT = path.join(HERE, 'out');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const DEMO_OUTPUT = path.resolve(FORK, '../demo/flow/results/output.xml');
const EDGE = process.env.EDGE || [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/chromium', '/usr/bin/google-chrome',
].find((p) => fs.existsSync(p));

const DARK = `html { --vscode-editor-background: #1f1f1f; --vscode-foreground: #cccccc; --vscode-descriptionForeground: #9d9d9d;
  --vscode-button-background: #0078d4; --vscode-button-foreground: #ffffff; --vscode-button-secondaryBackground: #313131;
  --vscode-button-secondaryForeground: #cccccc; --vscode-panel-border: #2b2b2b; --vscode-textLink-foreground: #4daafc;
  --vscode-textBlockQuote-background: #2b2b2b; --vscode-panelTitle-activeBorder: #0078d4; } body { background: #1f1f1f; }`;
const LIGHT = `html { --vscode-font-family: "Segoe UI", sans-serif; --vscode-font-size: 13px; --vscode-foreground: #3b3b3b;
  --vscode-descriptionForeground: #717171; --vscode-button-background: #005fb8; --vscode-button-foreground: #fff;
  --vscode-button-secondaryBackground: #e5e5e5; --vscode-button-secondaryForeground: #3b3b3b; --vscode-panel-border: #e5e5e5;
  --vscode-textLink-foreground: #005fb8; --vscode-textBlockQuote-background: #f8f8f8; --vscode-panelTitle-activeBorder: #005fb8;
  --vscode-testing-iconPassed: #388a34; --vscode-testing-iconFailed: #e51400; --vscode-testing-iconQueued: #cca700;
  --vscode-testing-iconUnset: #848484; --vscode-testing-iconSkipped: #848484; --vscode-charts-orange: #d18616;
  --vscode-editor-font-family: Consolas, monospace; }`;

const tests = (names) => names.map(([name, status, elapsed, message], i) => ({ id: `s1-t${i + 1}`, name, status, start: null, elapsed, message: message || '' }));

function member(over = {}) {
  return { id: 'main', title: 'laps.flow.json', status: 'failed', duration: '41 s', total: { pass: 2, fail: 1, skip: 0, unknown: 0 },
           tests: tests([['Warm up', 'PASS', 3.2], ['Laps', 'FAIL', 35.5, 'Lap 3 took 12.4 s, limit 10 s\nat tick: Should Be True'], ['Cool down', 'PASS', 2.1]]),
           note: null, reportKey: null, canReport: true, flow: true, ...over };
}

const CASES = [
  { id: 'runpage', data: {
      title: 'laps.flow.json', status: 'failed', when: '11.10.2026, 13:40:12', duration: '41 s', target: 'D:\\bench\\flows\\laps.flow.json',
      variables: [['CYCLES', '3'], ['MAX_TIME', '2h']], dir: 'D:\\bench\\results\\20261011-134012-laps', live: null, running: false, pausable: false, paused: false,
      files: { log: true, report: true, output: true },
      checkpoints: [{ name: 'laps.checkpoint.json', file: 'D:\\x\\laps.checkpoint.json', kind: 'checkpoint' }, { name: 'laps.snapshot-before-flash.json', file: 'D:\\x\\s.json', kind: 'snapshot "before-flash"' }],
      group: false, members: [member()] },
    expect: ['laps.flow.json', 'badge failed', 'Lap 3 took 12.4 s', 'Continue from checkpoint', 'Continue from snapshot', 'Write the flow report', 'CYCLES=3', 'class="bar"'] },
  { id: 'runpage-report', report: true, data: {
      title: '01_hello.flow.json', status: 'passed', when: '11.10.2026, 09:46:16', duration: '0.6 s', target: null, variables: [], dir: 'D:\\demo\\flow\\results',
      live: null, running: false, pausable: false, paused: false, files: { log: true, report: true, output: false }, checkpoints: [], group: false,
      members: [member({ title: '01_hello.flow.json', status: 'passed', duration: '0.6 s', total: { pass: 1, fail: 0, skip: 0, unknown: 0 }, tests: tests([['Hello Flow', 'PASS', 0.53]]), canReport: false, reportKey: 'REPORT' })] },
    expect: ['badge passed', 'Hello Flow', '<iframe', 'open in the browser'], shown: true, expectReport: true },
  { id: 'runpage-live', data: {
      title: 'climate_profile.flow.json', status: 'running', when: '11.10.2026, 14:02:00', duration: '12 min 4 s', target: 'D:\\bench\\flows\\climate_profile.flow.json', variables: [],
      dir: 'D:\\bench\\results\\20261011-140200-climate_profile', live: 'running · phase Soak, loop cycles iteration 7', running: true, pausable: true, paused: false,
      files: { log: false, report: false, output: true }, checkpoints: [], group: false,
      members: [member({ title: 'climate_profile.flow.json', status: 'running', duration: '', total: null, tests: tests([['Claim The Bench', 'PASS', 4.1], ['Ramp', 'PASS', 65]]), canReport: false })] },
    expect: ['badge running', 'phase Soak', 'data-control="pause"', 'data-control="stop"', 'Snapshot', 'Written when the run ends'] },
  { id: 'runpage-group', data: {
      title: 'Rendezvous', status: 'passed', when: '11.10.2026, 12:50:00', duration: '12 s', target: null, variables: [], dir: 'D:\\bench\\results\\20261011-125000-group-rendezvous',
      live: null, running: false, pausable: false, paused: false, files: { log: false, report: false, output: true }, checkpoints: [], group: true,
      members: [member({ id: 'IVI', title: 'IVI', status: 'passed', duration: '12 s', total: { pass: 1, fail: 0, skip: 0, unknown: 0 }, tests: tests([['Meet', 'PASS', 11.2]]), canReport: true }),
                member({ id: 'ADAS', title: 'ADAS', status: 'passed', duration: '11 s', total: { pass: 1, fail: 0, skip: 0, unknown: 0 }, tests: tests([['Meet', 'PASS', 10.8]]), canReport: true })] },
    expect: ['data-tab="IVI"', 'data-tab="ADAS"', 'class="tabs"', 'Meet'] },
];

if (!EDGE) {
  console.error('No Edge/Chromium found (set EDGE).');
  process.exit(1);
}
fs.mkdirSync(OUT, { recursive: true });

// The flow report of the demo's run, for the frame.
let reportHtml = null;
if (fs.existsSync(DEMO_OUTPUT)) {
  const copy = path.join(OUT, 'runpage-demo');
  fs.mkdirSync(copy, { recursive: true });
  fs.copyFileSync(DEMO_OUTPUT, path.join(copy, 'output.xml'));
  const answer = await writeFlowReport({ python: PYTHON, robotSource: FORK, pythonPath: [], cwd: copy, timeoutMs: 60000, helpersDir: path.join(ROOT, 'python') }, path.join(copy, 'output.xml'));
  if (answer.ok) reportHtml = reportForFrame(fs.readFileSync(answer.file, 'utf8'), 't');
  else console.log(`(no flow report for the frame: ${answer.error})`);
}

let failed = 0;
for (const c of CASES.flatMap((x) => [x, { ...x, id: `${x.id}-dark`, dark: true }])) {
  const data = JSON.parse(JSON.stringify(c.data));
  for (const m of data.members) if (m.reportKey === 'REPORT') { m.reportKey = reportHtml ? 'demo' : null; m.reportHtml = reportHtml || undefined; }
  const html = runPageHtml("default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-t'; frame-src https://example.invalid about:;", 't')
    .replace('<title>Run</title>', `<title>Run</title><style>${c.dark ? DARK : LIGHT}</style>
<script nonce="t">
  window.__sent = [];
  window.acquireVsCodeApi = () => ({ postMessage: (m) => window.__sent.push(m), getState() {}, setState() {} });
  window.addEventListener('error', (e) => { document.title = 'ERROR ' + e.message; });
  // The sandboxed report frame loads in its own process, outside the page's
  // virtual time: the page is marked done when the frame has reported, or
  // after a while when no report is expected.
  const done = () => {
    const ready = window.__sent.some((m) => m.type === 'ready');
    const shown = window.__sent.find((m) => m.type === 'reportShown');
    if (!document.title.startsWith('ERROR')) document.title = 'DONE ' + (ready ? 'ready ' : 'no-ready ') + document.getElementById('page').innerHTML.length + ' report ' + (shown ? shown.length : 0);
  };
  window.addEventListener('message', (e) => { if (e.data && e.data.type === 'reportShown') setTimeout(done, 50); });
  window.addEventListener('load', () => {
    window.postMessage({ type: 'data', data: ${JSON.stringify(data).replace(/<\//g, '<\\/')} }, '*');
    setTimeout(done, ${c.expectReport ? 6000 : 1500});
  });
</script>`);
  const file = path.join(OUT, `${c.id}.html`);
  fs.writeFileSync(file, html);
  const profile = fs.mkdtempSync(path.join(OUT, 'profile-'));
  // Site isolation off: the sandboxed report frame then shares the page's
  // process and its virtual time, so it has loaded when the DOM is dumped.
  const args = ['--headless=new', '--disable-gpu', '--allow-file-access-from-files', '--hide-scrollbars',
                '--no-first-run', '--disable-sync', '--disable-extensions', `--user-data-dir=${profile}`,
                '--disable-site-isolation-trials', '--disable-features=IsolateOrigins,site-per-process,IsolateSandboxedIframes',
                '--window-size=1100,1300', '--virtual-time-budget=8000'];
  const quiet = { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] };
  const dom = execFileSync(EDGE, [...args, '--dump-dom', pathToFileURL(file).href], quiet);
  execFileSync(EDGE, [...args, `--screenshot=${path.join(OUT, `${c.id}.png`)}`, pathToFileURL(file).href], quiet);
  fs.rmSync(profile, { recursive: true, force: true });
  const title = (/<title>([^<]*)<\/title>/.exec(dom) || [])[1] || '';
  const missing = c.expect.filter((s) => !dom.includes(s.replace(/"/g, '&quot;')) && !dom.includes(s));
  const shownLength = Number((/ report (-?\d+)/.exec(title) || [])[1] || 0);
  if (c.shown && reportHtml && shownLength < 1000) missing.push(`the report in the frame (${shownLength} chars: its script was blocked, or the frame)`);
  const ok = title.startsWith('DONE ready') && !missing.length;
  if (!ok) failed++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${c.id}: ${title || 'no title'}; ${missing.length ? `not found: ${missing.join(', ')}` : 'drawn'} -> test/webview/out/${c.id}.png`);
}
process.exit(failed ? 1 : 0);
