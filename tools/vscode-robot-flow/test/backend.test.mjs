// The Python side of the extension, end to end with a real Robot Framework:
// the helpers read a suite and a flow file, apply a Grid and a Diagram edit,
// and the probe tells a Robot with flow support from one without.
//
//   npm test
// Env: ROBOT_FLOW_PYTHON  interpreter with Robot Framework (default: python)
//      ROBOT_FLOW_SRC     the RobotFramework AIO fork's src (default: this repository's src)
//      ROBOT_FLOW_DEMO    the demo projects (no default: the tests that need them skip)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { readGrid, readFlow, editView, cleanPath } = require('../out/backend/helpers.js');
const { probeRobot, diagramUnavailable } = require('../out/backend/robot.js');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const DEMO = process.env.ROBOT_FLOW_DEMO || path.resolve(ROOT, 'no-demo');

const SUITE = path.join(DEMO, 'bench_gui/bench_project/testsuites/signals_smoke.robot');
const FLOW = path.join(DEMO, 'climate_endurance/endurance_project/flows/climate_profile.flow.json');

const env = (robotSource, cwd) => ({
  python: PYTHON, robotSource, pythonPath: [], cwd, timeoutMs: 90000, helpersDir: path.join(ROOT, 'python'),
});

const haveFiles = fs.existsSync(SUITE) && fs.existsSync(FLOW) && fs.existsSync(path.join(FORK, 'robot'));
const skip = haveFiles ? false : `needs ${SUITE}, ${FLOW} and the fork at ${FORK}`;

test('the probe sees the fork\'s flow support', { skip }, async () => {
  const info = await probeRobot(env(FORK, ROOT));
  assert.equal(info.ok, true, info.error);
  assert.equal(info.flow, true);
  assert.equal(diagramUnavailable(info), null);
  assert.ok(info.location.startsWith(FORK), `robot from ${info.location}`);
});

test('without the fork the Diagram says what is missing', { skip }, async () => {
  const info = await probeRobot(env('', ROOT));
  if (!info.ok) return;   // no installed Robot at all: nothing to compare
  if (info.flow) return;  // the installed Robot is the fork itself
  assert.match(diagramUnavailable(info), /robot\.flow/);
});

test('Grid: a suite as rows, keywords resolved', { skip }, async () => {
  const text = fs.readFileSync(SUITE, 'utf8');
  const data = await readGrid(env(FORK, path.dirname(SUITE)), SUITE, text);
  assert.equal(data.ok, true, data.error);
  for (const key of ['grid', 'keywords', 'imports', 'catalog', 'features']) assert.ok(key in data, `no ${key}`);
  assert.equal(data.features.thread, true, 'the fork has THREAD');
});

test('Grid: unsaved text is what is shown, and a broken import is reported, not fatal', { skip }, async () => {
  const text = fs.readFileSync(SUITE, 'utf8').replace('../resources/bench_signals.resource', 'nowhere.resource');
  const data = await readGrid(env(FORK, path.dirname(SUITE)), SUITE, text);
  assert.equal(data.ok, true, data.error);
  assert.match(JSON.stringify(data.imports), /nowhere\.resource/);
});

test('Grid edit: delete one step, every other line kept', { skip }, async () => {
  const text = fs.readFileSync(SUITE, 'utf8');
  const lines = text.split(/\r?\n/);
  const at = lines.findIndex((l) => l.includes('bench.dut.dac_ch0.voltage_V') && l.includes('Dictionary Should Contain Key')) + 1;
  assert.ok(at > 0);
  const answer = await editView(env(FORK, path.dirname(SUITE)), 'grid', SUITE, text, { op: 'delete', line: at });
  assert.equal(answer.ok, true, answer.error);
  const expected = lines.filter((_, i) => i !== at - 1).join(text.includes('\r\n') ? '\r\n' : '\n');
  assert.equal(answer.text, expected);
});

test('Diagram: the fork\'s structure of a flow file', { skip }, async () => {
  const text = fs.readFileSync(FLOW, 'utf8');
  const data = await readFlow(env(FORK, path.dirname(FLOW)), FLOW, text);
  assert.equal(data.ok, true, data.error);
  assert.equal(data.flow.name, 'Climate Profile');
  assert.ok(Array.isArray(data.flow.tests));
});

test('Diagram: a flow the fork refuses comes back as an error, not a crash', { skip }, async () => {
  const data = await readFlow(env(FORK, path.dirname(FLOW)), FLOW, '{ "flow": { "name": "x" }, "nodes": [ {"id": "a", "kind": "nonsense"} ] }');
  assert.equal(data.ok, false);
  assert.ok(data.error);
});

test('Diagram edit: insert a sleep after a step; the fork validates the result', { skip }, async () => {
  const text = fs.readFileSync(FLOW, 'utf8');
  const doc = JSON.parse(text);
  const step = (doc.nodes || []).find((n) => n.kind === 'keyword');
  assert.ok(step, 'a keyword step to insert after');
  const answer = await editView(env(FORK, path.dirname(FLOW)), 'diagram', FLOW, text,
                                { op: 'insert', kind: 'sleep', attrs: { duration: '1s' }, after: step.id });
  assert.equal(answer.ok, true, answer.error);
  const after = JSON.parse(answer.text);
  assert.equal(after.nodes.length, doc.nodes.length + 1);
  const added = after.nodes.find((n) => n.kind === 'sleep' && !doc.nodes.some((o) => o.id === n.id));
  assert.ok(added, 'the new sleep node');
  const reread = await readFlow(env(FORK, path.dirname(FLOW)), FLOW, answer.text);
  assert.equal(reread.ok, true, reread.error);
});

test('a missing interpreter is an error with its name, not an exception', async () => {
  const data = await readGrid({ ...env('', ROOT), python: 'no-such-python-xyz' }, SUITE, '');
  assert.equal(data.ok, false);
  assert.match(data.error, /no-such-python-xyz/);
});

test('paths pasted with quotes (Windows "Copy as path") are used without them', () => {
  assert.equal(cleanPath('"C:\\Program Files\\RobotFramework\\python3\\python.exe"'),
               'C:\\Program Files\\RobotFramework\\python3\\python.exe');
  assert.equal(cleanPath("  'D:/x/src'  "), 'D:/x/src');
  assert.equal(cleanPath('"D:/x/src\''), '"D:/x/src\'');
  assert.equal(cleanPath('python'), 'python');
  assert.equal(cleanPath('"'), '"');
  assert.equal(cleanPath(''), '');
});
