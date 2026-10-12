// Go to Definition: what the cursor is on (plain text logic), and where the
// helper finds it in the demo projects with a real Robot Framework.
//
//   ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC (the fork's src), ROBOT_FLOW_DEMO
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { robotCellAt, flowStringAt, existingFile, defineName } = require('../out/backend/define.js');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const DEMO = process.env.ROBOT_FLOW_DEMO || path.resolve(ROOT, 'no-demo');
const SUITE = path.join(DEMO, 'bench_gui/bench_project/testsuites/signals_smoke.robot');
const FLOW = path.join(DEMO, 'climate_endurance/endurance_project/flows/climate_profile.flow.json');
const PAIR = path.join(DEMO, 'bench_gui/bench_project/pairs/rendezvous.flow.json');

const env = (cwd) => ({ python: PYTHON, robotSource: FORK, pythonPath: [], cwd, timeoutMs: 90000,
                        helpersDir: path.join(ROOT, 'python') });
const haveDemo = [SUITE, FLOW, PAIR].every((f) => fs.existsSync(f)) && fs.existsSync(path.join(FORK, 'robot'));
const skip = haveDemo ? false : `needs the demo projects and the fork at ${FORK}`;

const at = (line, piece, offset = 1) => line.indexOf(piece) + offset;

test('the Robot cell under the cursor', () => {
  const call = '    ${value}=    Get Signal    bench.dut.temp    timeout=2s    # read it';
  assert.equal(robotCellAt(call, at(call, 'Signal')).text, 'Get Signal');
  assert.equal(robotCellAt(call, at(call, 'bench')).text, 'bench.dut.temp');
  assert.equal(robotCellAt(call, at(call, 'value')), null, 'an assignment is not a name');
  assert.equal(robotCellAt(call, at(call, 'read')), null, 'nor a comment');
  assert.equal(robotCellAt(call, 1), null, 'nor the indentation');
  const setup = 'Suite Setup       Resolve Signal Owners    prefix=bench.';
  assert.equal(robotCellAt(setup, at(setup, 'Owners')).text, 'Resolve Signal Owners');
  const tab = '\tLog\tone word';
  assert.equal(robotCellAt(tab, at(tab, 'Log')).text, 'Log');
  const pipe = '| Log | hello |';
  assert.equal(robotCellAt(pipe, at(pipe, 'Log')).text, 'Log');
  assert.equal(robotCellAt('    [Setup]    Open', 6), null, 'a [Setting] is not a keyword');
  assert.equal(robotCellAt('*** Keywords ***', 6), null);
  assert.equal(robotCellAt('    ...    Log', 5), null);
});

test('the flow string under the cursor, with its key', () => {
  const step = '    { "id": "meet", "kind": "gate", "keyword": "Signal Should Be", "args": ["bench.ready", "==", 1] },';
  assert.deepEqual(pick(flowStringAt(step, at(step, 'Should'))), { text: 'Signal Should Be', key: 'keyword' });
  assert.deepEqual(pick(flowStringAt(step, at(step, 'bench.ready'))), { text: 'bench.ready', key: null });
  assert.equal(flowStringAt(step, at(step, '"keyword"')), null, 'a key is not a value');
  const file = '      "file": "sub/supply_step.flow.json",';
  assert.deepEqual(pick(flowStringAt(file, at(file, 'supply'))), { text: 'sub/supply_step.flow.json', key: 'file' });
  const escaped = '"keyword": "Say \\"hi\\""';
  assert.equal(flowStringAt(escaped, at(escaped, 'Say')).text, 'Say "hi"');
});

function pick(s) {
  return s && { text: s.text, key: s.key };
}

test('paths written in a file', { skip }, () => {
  assert.equal(existingFile(FLOW, 'sub/supply_step.flow.json'), path.join(path.dirname(FLOW), 'sub', 'supply_step.flow.json'));
  assert.equal(existingFile(SUITE, '${CURDIR}/../resources/bench_signals.resource'),
               path.resolve(path.dirname(SUITE), '../resources/bench_signals.resource'));
  assert.equal(existingFile(SUITE, 'Collections'), null);
  assert.equal(existingFile(SUITE, 'no/such.resource'), null);
  assert.equal(existingFile(SUITE, '${OTHER}/x.resource'), null);
});

test('keywords of a suite: its resources, libraries and BuiltIn', { skip }, async () => {
  const text = fs.readFileSync(SUITE, 'utf8');
  const e = env(path.dirname(SUITE));
  const res = await defineName(e, SUITE, text, 'Signal Should Be');
  assert.equal(res.found, true);
  assert.match(res.source, /bench_signals\.resource$/);
  assert.ok(res.line > 0 && fs.readFileSync(res.source, 'utf8').split(/\r?\n/)[res.line - 1].startsWith('Signal Should Be'));
  const lib = await defineName(e, SUITE, text, 'Dictionary Should Contain Key');
  assert.match(lib.source, /Collections\.py$/);
  const builtin = await defineName(e, SUITE, text, 'log');   // Robot's matching: case and spaces do not count
  assert.match(builtin.source, /BuiltIn\.py$/);
  assert.equal((await defineName(e, SUITE, text, 'Then Set Signal')).name, 'Set Signal');
  assert.equal((await defineName(e, SUITE, text, 'No Such Keyword')).found, false);
});

test('a keyword of the file itself, from unsaved text', { skip }, async () => {
  const text = '*** Test Cases ***\nT\n    My Step\n\n*** Keywords ***\nMy Step\n    No Operation\n';
  const res = await defineName(env(path.dirname(SUITE)), SUITE, text, 'My Step');
  assert.equal(res.found, true);
  assert.equal(res.source, SUITE);
  assert.equal(res.line, 6);
});

test('keywords of a flow: through its own imports', { skip }, async () => {
  const flow = await defineName(env(path.dirname(FLOW)), FLOW, fs.readFileSync(FLOW, 'utf8'), 'Claim The Bench');
  assert.match(flow.source, /endurance\.resource$/);
  const pair = await defineName(env(path.dirname(PAIR)), PAIR, fs.readFileSync(PAIR, 'utf8'), 'Set Signal');
  assert.match(pair.source, /bench_keywords\.py$/);
  assert.ok(pair.line > 0);
});
