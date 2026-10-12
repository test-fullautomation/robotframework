// The fork's flow control from the extension: pause and resume a running
// flow, stop it at the next step with a checkpoint and continue a new run
// from there, step mode, and a group member's own rig name.
//
//   npm test      (ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { planRun, RobotRun } = require('../out/backend/run.js');
const { sendControl, readControlState, describeFlow, activeProcesses, findCheckpoint, canPause } = require('../out/backend/control.js');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const LAPS = path.join(ROOT, 'test/fixtures/laps.flow.json');

const env = (cwd = ROOT) => ({
  python: PYTHON, robotSource: FORK, pythonPath: [], cwd, timeoutMs: 90000, helpersDir: path.join(ROOT, 'python'),
});
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'robot-flow-control-'));
const skip = fs.existsSync(path.join(FORK, 'robot', 'flow', 'control.py')) ? false : `needs the fork with flow control at ${FORK}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function finished(run, ms = 120000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('run did not end')), ms);
    run.on('exit', (status, code) => { clearTimeout(t); resolve({ status, code }); });
  });
}

/** Wait until test(state of the store) holds. */
async function until(store, testFn, ms = 30000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const s = readControlState(store);
    if (testFn(s)) return s;
    await sleep(100);
  }
  throw new Error(`timed out; the store says ${JSON.stringify(readControlState(store))}`);
}

const laps = (run) => run.position?.counts?.lap?.pass ?? 0;

test('the plan: step mode for flows only, the rig, the store as the control channel', () => {
  const out = path.join(os.tmpdir(), 'x');
  const plan = planRun({ env: env(), target: LAPS, outDir: out, step: true, rig: 'IVI' });
  assert.ok(plan.argv.join(' ').includes('--variable FLOW_STEP:yes'));
  assert.equal(plan.env.ROBOT_FLOW_RIG, 'IVI');
  assert.equal(plan.signalsFile, path.join(out, 'signals.json'));
  const own = planRun({ env: env(), target: LAPS, outDir: out, extraEnv: { ROBOT_FLOW_SIGNALS: '${RUN_DIR}/bench.json' } });
  assert.equal(own.signalsFile, `${out}/bench.json`, 'the project\'s own store is the channel');
  assert.equal(planRun({ env: env(), target: path.join(ROOT, 'x.robot'), outDir: out }).signalsFile, null);
  assert.throws(() => planRun({ env: env(), target: path.join(ROOT, 'x.robot'), outDir: out, step: true }), /Step mode is for flow files/);
  assert.ok(canPause(LAPS) && !canPause('suite.robot'));
});

test('the store read as the GUI reads it', () => {
  const dir = tmp();
  const store = path.join(dir, 'signals.json');
  const now = Date.now() / 1000;
  fs.writeFileSync(store, JSON.stringify({
    'flow.state.IVI': { value: { state: 'paused', phase: 'Cycle', loop: 'loop', iteration: 2 }, time: now - 1 },
    'flow.state.ADAS': { value: { state: 'running', phase: 'Cycle' }, time: now - 1 },
    'flow.control.IVI': { value: 'pause', time: now - 2 },
    'flow.control': { value: 'resume', time: now - 30 },
    'bench.IVI.ready': { value: 1, time: now },
  }));
  const s = readControlState(store, now * 1000);
  assert.deepEqual(Object.keys(s.processes).sort(), ['ADAS', 'IVI']);
  assert.deepEqual(s.command, { value: 'pause', ageS: 2, member: 'IVI' }, 'the newest command');
  assert.equal(describeFlow(s).text, '1 of 2 paused');
  assert.equal(describeFlow(s, 'IVI').text, 'paused · phase Cycle, loop loop iteration 3');
  assert.deepEqual(activeProcesses(s).sort(), ['ADAS', 'IVI']);
  assert.equal(readControlState(path.join(dir, 'none.json')), null);
  assert.equal(findCheckpoint(dir), null);
  fs.writeFileSync(path.join(dir, 'laps.checkpoint.json'), '{}');
  assert.equal(findCheckpoint(dir), path.join(dir, 'laps.checkpoint.json'));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('pause holds the flow, resume lets it finish', { skip }, async () => {
  const outDir = tmp();
  const run = new RobotRun(planRun({ env: env(), target: LAPS, outDir }));
  const end = finished(run);
  run.start();
  const store = run.plan.signalsFile;
  await until(store, (s) => describeFlow(s)?.text.startsWith('running'));
  assert.equal((await sendControl(env(), store, 'pause')).ok, true);
  await until(store, (s) => describeFlow(s)?.paused === 1);
  await sleep(600);                       // a Sleep running when it paused ends first
  const held = laps(run);
  await sleep(1200);
  assert.equal(laps(run), held, 'no lap while paused');
  assert.equal((await sendControl(env(), store, 'resume')).ok, true);
  const { status } = await end;
  assert.equal(status, 'passed');
  assert.equal(laps(run), 40);
  fs.rmSync(outDir, { recursive: true, force: true });
});

test('stop leaves at the next step with a checkpoint; a new run continues from it', { skip }, async () => {
  const outDir = tmp();
  const run = new RobotRun(planRun({ env: env(), target: LAPS, outDir }));
  const end = finished(run);
  run.start();
  const store = run.plan.signalsFile;
  await until(store, () => laps(run) >= 3);    // the state is published on changes only: count laps
  assert.equal((await sendControl(env(), store, 'stop')).ok, true);
  run.stopping();
  const { status } = await end;
  assert.equal(status, 'stopped');
  const xml = fs.readFileSync(run.file('output.xml'), 'utf8');
  assert.match(xml, /teardown ran/, 'the teardown ran');
  const checkpoint = findCheckpoint(outDir);
  assert.ok(checkpoint, 'a checkpoint was written');
  const done = laps(run);
  assert.ok(done >= 3 && done < 40, `laps before the stop: ${done}`);

  const again = tmp();
  const next = new RobotRun(planRun({ env: env(), target: LAPS, outDir: again, variables: { FLOW_CHECKPOINT: checkpoint } }));
  const end2 = finished(next);
  next.start();
  assert.equal((await end2).status, 'passed');
  assert.equal(done + laps(next), 40, `the rest of the laps only (${done} + ${laps(next)})`);
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.rmSync(again, { recursive: true, force: true });
});

test('step mode: paused before every step, resume goes one step on', { skip }, async () => {
  const outDir = tmp();
  const run = new RobotRun(planRun({ env: env(), target: LAPS, outDir, step: true }));
  const end = finished(run);
  run.start();
  const store = run.plan.signalsFile;
  await until(store, (s) => describeFlow(s)?.paused === 1);
  await sleep(300);
  const first = run.position?.step ?? 0;
  assert.equal((await sendControl(env(), store, 'resume')).ok, true);
  await until(store, (s) => describeFlow(s)?.paused === 1 && (run.position?.step ?? 0) > first);
  await sleep(500);
  assert.equal(run.position.step, first + 1, 'exactly one step on');
  assert.equal((await sendControl(env(), store, 'stop')).ok, true);
  run.stopping();
  await sendControl(env(), store, 'resume');   // a paused flow leaves on its stop once it moves
  const { status } = await end;
  assert.equal(status, 'stopped');
  fs.rmSync(outDir, { recursive: true, force: true });
});
