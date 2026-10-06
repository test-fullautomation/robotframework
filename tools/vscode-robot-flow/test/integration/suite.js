// Runs inside VS Code (test/integration/run.mjs). No test framework: VS Code
// calls run(); a rejected promise fails the run (non-zero exit).
const assert = require('node:assert/strict');
const path = require('node:path');
const vscode = require('vscode');

const DEMO = process.env.ROBOT_FLOW_DEMO;
const SUITE = path.join(DEMO, 'bench_gui/bench_project/testsuites/signals_smoke.robot');
const FLOW = path.join(DEMO, 'climate_endurance/endurance_project/flows/climate_profile.flow.json');

async function until(what, fn, ms = 60000) {
  const end = Date.now() + ms;
  for (;;) {
    const value = fn();
    if (value) return value;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

async function run() {
  const ext = vscode.extensions.all.find((e) => e.packageJSON.name === 'robot-flow');
  assert.ok(ext, 'the extension is loaded');
  const api = await ext.activate();
  const commands = await vscode.commands.getCommands(true);
  for (const c of ['robotFlow.openGrid', 'robotFlow.openDiagram', 'robotFlow.showRobot']) {
    assert.ok(commands.includes(c), `command ${c}`);
  }

  for (const [file, viewType, command] of [[SUITE, 'robotFlow.grid', 'robotFlow.openGrid'],
                                           [FLOW, 'robotFlow.diagram', 'robotFlow.openDiagram']]) {
    const uri = vscode.Uri.file(file);
    await vscode.window.showTextDocument(uri);
    await vscode.commands.executeCommand(command, uri);
    const tab = await until(`a ${viewType} tab`, () => vscode.window.tabGroups.all
      .flatMap((g) => g.tabs)
      .find((t) => t.input instanceof vscode.TabInputCustom && t.input.viewType === viewType));
    assert.equal(tab.input.uri.fsPath.toLowerCase(), file.toLowerCase());
    const report = await until(`${viewType} data`, () => api.report(viewType, file), 90000);
    assert.equal(report.ok, true, `${viewType}: ${report.error}`);
    assert.equal(report.drawn, true, `${viewType} was sent data`);
    console.log(`ok ${viewType}: ${path.basename(file)} read and sent to the view`);
  }

  // The text: Robot files are the robotframework language, with its grammar.
  const suite = await vscode.workspace.openTextDocument(SUITE);
  assert.equal(suite.languageId, 'robotframework');
  console.log('ok .robot files open as Robot Framework');

  // Go to Definition, through VS Code's own command (what F12 and Ctrl+Click use).
  const where = async (doc, piece, nth) => {
    let at = -1;
    for (let i = 0; i <= nth; i++) at = doc.getText().indexOf(piece, at + 1);
    assert.ok(at >= 0, `${piece} in ${path.basename(doc.uri.fsPath)}`);
    const links = await vscode.commands.executeCommand('vscode.executeDefinitionProvider', doc.uri,
                                                       doc.positionAt(at + 1));
    const first = (links || [])[0];
    if (!first) return null;
    const uri = first.targetUri || first.uri;
    const range = first.targetRange || first.range;
    return { file: path.basename(uri.fsPath), line: range.start.line + 1,
             text: (await vscode.workspace.openTextDocument(uri)).lineAt(range.start.line).text };
  };
  const flow = await vscode.workspace.openTextDocument(FLOW);
  for (const [doc, piece, nth, file, starts] of [
    [suite, 'Signal Should Be', 1, 'bench_signals.resource', 'Signal Should Be'],   // a call, not the Documentation
    [suite, 'Resolve Signal Owners', 0, 'bench_signals.resource', 'Resolve Signal Owners'],
    [suite, '../resources/bench_signals.resource', 0, 'bench_signals.resource', null],
    [suite, 'Dictionary Should Contain Key', 0, 'Collections.py', null],
    [flow, 'Claim The Bench', 0, 'endurance.resource', 'Claim The Bench'],
    [flow, 'sub/supply_step.flow.json', 0, 'supply_step.flow.json', null],
  ]) {
    const got = await where(doc, piece, nth);
    assert.ok(got, `no definition for ${piece}`);
    assert.equal(got.file, file, `${piece} -> ${got.file}`);
    if (starts) assert.ok(got.text.startsWith(starts), `${piece} -> line ${got.line}: ${got.text}`);
    console.log(`ok definition of ${JSON.stringify(piece)} in ${path.basename(doc.uri.fsPath)}: ${got.file}:${got.line}`);
  }

  await debugAFlow(api);
  await pauseAFlow(api);
  if (process.env.ROBOT_FLOW_WITH_PYTHON) await stepIntoPython();
  else console.log('skip debug into Python: no Python debugger extension in the test profile');
}

// Step Into a keyword of a Python library: VS Code's Python debugger (a child
// session of the Robot one) stops in the function; Continue goes back to Robot.
async function stepIntoPython() {
  const dir = path.join(__dirname, '..', 'fixtures', 'debug');
  const suite = path.join(dir, 'pysuite.robot');
  const bp = new vscode.SourceBreakpoint(new vscode.Location(vscode.Uri.file(suite), new vscode.Position(5, 0)));
  vscode.debug.addBreakpoints([bp]);
  const stops = [];   // [session type, stopped body]
  const trackers = ['robotflow', 'debugpy'].map((type) => vscode.debug.registerDebugAdapterTrackerFactory(type, {
    createDebugAdapterTracker: (session) => ({
      onDidSendMessage: (m) => { if (m.type === 'event' && m.event === 'stopped') stops.push([session, m.body]); },
    }),
  }));
  let robotEnded = false;
  const ended = vscode.debug.onDidTerminateDebugSession((s) => { if (s.type === 'robotflow') robotEnded = true; });
  try {
    assert.ok(await vscode.debug.startDebugging(undefined, { type: 'robotflow', request: 'launch', name: 'py', target: suite }));
    await until('the Robot stop at Add Numbers', () => stops.find(([s]) => s.type === 'robotflow'), 120000);
    const robot = stops.find(([s]) => s.type === 'robotflow')[0];
    const child = await until('the Python child session', () =>
      vscode.debug.activeDebugSession && [robot, vscode.debug.activeDebugSession].length &&
      findChild(robot), 30000);
    assert.equal(child.type, 'debugpy');

    await robot.customRequest('stepIn', { threadId: 1 });
    const [, pyStop] = await until('the stop in Python', () => stops.find(([s]) => s.type === 'debugpy'), 60000);
    const pyFrames = (await child.customRequest('stackTrace', { threadId: pyStop.threadId })).stackFrames;
    assert.equal(path.basename(pyFrames[0].source.path), 'mylib.py');
    assert.equal(pyFrames[0].line, 6);
    console.log(`ok debug into Python: stopped in ${pyFrames[0].name} (mylib.py:${pyFrames[0].line})`);

    const before = stops.filter(([s]) => s.type === 'robotflow').length;
    await child.customRequest('continue', { threadId: pyStop.threadId });
    await until('back in Robot', () => stops.filter(([s]) => s.type === 'robotflow').length > before, 60000);
    const top = (await robot.customRequest('stackTrace', { threadId: 1 })).stackFrames[0];
    assert.equal(top.line, 7);
    console.log(`ok debug into Python: back in Robot at pysuite.robot:${top.line}`);

    await robot.customRequest('continue', { threadId: 1 });
    await until('the end of the Robot session', () => robotEnded, 90000);
  } finally {
    trackers.forEach((x) => x.dispose());
    ended.dispose();
    vscode.debug.removeBreakpoints([bp]);
  }
}

// The fork's flow control through the extension: pause and resume a running
// flow, stop it with a checkpoint, then Continue (the command) from there.
async function pauseAFlow(api) {
  const commands = await vscode.commands.getCommands(true);
  for (const c of ['robotFlow.pause', 'robotFlow.resume', 'robotFlow.continue', 'robotFlow.runStep']) {
    assert.ok(commands.includes(c), `command ${c}`);
  }
  const laps = path.join(__dirname, '..', 'fixtures', 'laps.flow.json');
  const results = path.join(path.dirname(laps), 'results');
  const flowSays = (state) => Object.values((state && state.flow && state.flow.processes) || {}).map((p) => p.state);
  try {
    await vscode.window.showTextDocument(vscode.Uri.file(laps));
    const first = await api.run(laps);
    assert.ok(first && first.pausable, 'a flow run takes flow commands');
    await until('the flow says it runs', () => flowSays(api.runState(laps)).includes('running'), 90000);
    assert.ok(await api.control(laps, 'pause'), 'pause sent');
    await until('the flow paused', () => flowSays(api.runState(laps)).includes('paused'), 30000);
    assert.match(api.runState(laps).status, /running/);
    console.log('ok flow control: paused');
    assert.ok(await vscode.commands.executeCommand('robotFlow.resume'), 'resume sent (the command, on the active file)');
    await until('the flow running again', () => flowSays(api.runState(laps)).includes('running'), 30000);
    console.log('ok flow control: resumed');

    await api.control(laps, 'stop');
    const stopped = await until('the run stopped', () => {
      const s = api.runState(laps);
      return s && s.status === 'stopped' && s;
    }, 90000);
    assert.ok(stopped.checkpoint, 'a checkpoint to continue from');
    console.log(`ok flow control: stopped with ${path.basename(stopped.checkpoint)}`);

    const again = await vscode.commands.executeCommand('robotFlow.continue', vscode.Uri.file(laps));
    assert.ok(again && again.started > stopped.started, 'a new run');
    const end = await until('the continued run ended', () => {
      const s = api.runState(laps);
      return s && s.started === again.started && ['passed', 'failed', 'error', 'stopped'].includes(s.status) && s;
    }, 90000);
    assert.equal(end.status, 'passed');
    console.log('ok flow control: continued from the checkpoint and passed');
  } finally {
    require('node:fs').rmSync(results, { recursive: true, force: true });
  }
}

const children = [];
vscode.debug.onDidStartDebugSession((s) => { if (s.type === 'debugpy') children.push(s); });
function findChild(parent) {
  return children.find((s) => s.parentSession && s.parentSession.id === parent.id);
}

// Debug through VS Code itself: a breakpoint set with its API on a step of a
// flow and one in its sub-flow, F5's launch, the stops VS Code is told about,
// its call stack, Continue, the end.
async function debugAFlow() {
  const dir = path.join(__dirname, '..', 'fixtures', 'debug');
  const main = path.join(dir, 'main.flow.json');
  const sub = path.join(dir, 'sub', 'step.flow.json');
  const lineOf = async (file, id) => {
    const doc = await vscode.workspace.openTextDocument(file);
    return doc.getText().split(/\r?\n/).findIndex((l) => l.includes(`"id": "${id}"`));
  };
  const at = async (file, id) => new vscode.SourceBreakpoint(
    new vscode.Location(vscode.Uri.file(file), new vscode.Position(await lineOf(file, id), 0)));
  const bps = [await at(main, 'greet'), await at(sub, 'check')];
  vscode.debug.addBreakpoints(bps);

  const stops = [];
  const tracker = vscode.debug.registerDebugAdapterTrackerFactory('robotflow', {
    createDebugAdapterTracker: () => ({
      onDidSendMessage: (m) => { if (m.type === 'event' && m.event === 'stopped') stops.push(m.body); },
    }),
  });
  let ended = false;
  const done = vscode.debug.onDidTerminateDebugSession(() => { ended = true; });
  try {
    const started = await vscode.debug.startDebugging(undefined, { type: 'robotflow', request: 'launch', name: 'smoke', target: main });
    assert.ok(started, 'the debug session started');
    const session = await until('a debug session', () => vscode.debug.activeDebugSession);
    const frames = async () => (await session.customRequest('stackTrace', { threadId: 1 })).stackFrames;

    await until('the stop at the flow step', () => stops.length >= 1, 90000);
    let top = (await frames())[0];
    assert.equal(stops[0].reason, 'breakpoint');
    assert.equal(path.basename(top.source.path), 'main.flow.json');
    assert.equal(top.line, (await lineOf(main, 'greet')) + 1);
    console.log(`ok debug: stopped at ${top.name} (main.flow.json:${top.line})`);

    await session.customRequest('continue', { threadId: 1 });
    await until('the stop in the sub-flow', () => stops.length >= 2, 90000);
    top = (await frames())[0];
    assert.equal(path.basename(top.source.path), 'step.flow.json');
    const level = await session.customRequest('evaluate', { expression: '${LEVEL}', context: 'repl', frameId: 1 });
    assert.equal(level.result, '5');
    console.log(`ok debug: stopped in the sub-flow at ${top.name}, \${LEVEL} = ${level.result}`);

    await session.customRequest('continue', { threadId: 1 });
    await until('the end of the debug session', () => ended, 90000);
    console.log('ok debug: the run ended');
  } finally {
    tracker.dispose();
    done.dispose();
    vscode.debug.removeBreakpoints(bps);
  }
}

module.exports = { run };
