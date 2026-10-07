// Runs and run groups with a real Robot Framework (the fork): the run plan,
// a flow run with its live position, a graceful stop, the group links, and
// a two-member group run that meets through the shared signal store.
//
//   npm test      (ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC, ROBOT_FLOW_DEMO)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { planRun, RobotRun } = require('../out/backend/run.js');
const { readProject, memberPath } = require('../out/backend/project.js');
const { syncLinks, inspectGroup } = require('../out/backend/group.js');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const DEMO = process.env.ROBOT_FLOW_DEMO || path.resolve(ROOT, 'no-demo');
const BENCH = path.join(DEMO, 'bench_gui/bench_project/testproject.json');
const COUNTDOWN = path.join(ROOT, 'test/fixtures/countdown.flow.json');
const SLOW = path.join(ROOT, 'test/fixtures/slow.flow.json');

const env = (cwd, robotSource = FORK, pythonPath = []) => ({
  python: PYTHON, robotSource, pythonPath, cwd, timeoutMs: 90000, helpersDir: path.join(ROOT, 'python'),
});
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'robot-flow-run-'));
const skip = fs.existsSync(path.join(FORK, 'robot')) ? false : `needs the fork at ${FORK}`;

function finished(run, ms = 120000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('run did not end')), ms);
    run.on('exit', (status, code) => { clearTimeout(t); resolve({ status, code }); });
  });
}

test('the plan: flow parser, live position and a clean console encoding for flow files only', () => {
  process.env.PYTHONIOENCODING = 'utf-8:surrogateescape';
  const out = path.join(os.tmpdir(), 'x');
  const flow = planRun({ env: env(ROOT), target: COUNTDOWN, outDir: out, variables: { FROM: '5' } });
  assert.ok(flow.argv.includes('--parser') && flow.argv.includes('robot.flow'));
  assert.ok(!flow.argv.includes('--flowreport'), 'the flow report is opt-in');
  const withReport = planRun({ env: env(ROOT), target: COUNTDOWN, outDir: out, flowReport: true });
  assert.ok(withReport.argv.join(' ').includes('--flowreport flow.html'));
  assert.ok(!planRun({ env: env(ROOT), target: path.join(ROOT, 'x.robot'), outDir: out, flowReport: true }).argv.includes('--flowreport'), 'only a flow has a flow report');
  assert.ok(flow.argv.join(' ').includes('--variable FROM:5'));
  assert.equal(flow.env.PYTHONIOENCODING, undefined, 'a codec suffix crashes Robot\'s console writer');
  assert.equal(flow.env.MM_FLOW_POSITION, path.join(out, 'flow_position.json'));
  assert.equal(flow.env.ROBOT_FLOW_SIGNALS, path.join(out, 'signals.json'));
  const suite = planRun({ env: env(ROOT), target: path.join(ROOT, 'x.robot'), outDir: out });
  assert.ok(!suite.argv.includes('--parser') && !suite.argv.includes('--flowreport') && !suite.env.MM_FLOW_POSITION);
  assert.throws(() => planRun({ env: env(ROOT), target: COUNTDOWN, outDir: out, variables: { 'a b': '1' } }), /Invalid variable/);
  delete process.env.PYTHONIOENCODING;
});

test('a flow run: passes, and its position is followed to the end', { skip }, async () => {
  const outDir = tmp();
  const run = new RobotRun(planRun({ env: env(ROOT), target: COUNTDOWN, outDir, flowReport: true }));
  const seen = [];
  run.on('position', (p) => seen.push(p));
  run.start();
  const { status, code } = await finished(run);
  assert.equal(status, 'passed', `exit ${code}`);
  assert.ok(seen.length >= 1, 'positions were read while it ran');
  const last = run.position;
  assert.equal(last.done, true);
  assert.deepEqual(last.counts.tick, { pass: 3, fail: 0 });
  assert.ok(run.file('log.html') && run.file('output.xml'));
  assert.ok(run.file('flow.html'), 'the flow report was written with the run');
  fs.rmSync(outDir, { recursive: true, force: true });
});

test('stop: the running step ends, the teardown runs, log and report are written', { skip }, async () => {
  const outDir = tmp();
  const run = new RobotRun(planRun({ env: env(ROOT), target: SLOW, outDir }));
  run.start();
  await new Promise((resolve) => run.on('position', (p) => { if (p.node === 'wait') resolve(); }));
  run.stop();
  const { status } = await finished(run, 60000);
  assert.equal(status, 'stopped');
  assert.ok(run.file('log.html'), 'log.html written');
  const xml = fs.readFileSync(run.file('output.xml'), 'utf8');
  assert.match(xml, /teardown ran/, 'the teardown ran');
  fs.rmSync(outDir, { recursive: true, force: true });
});

test('group links: each member\'s Set Signal meets the other\'s gate', { skip: fs.existsSync(BENCH) ? skip : 'no bench demo' }, async () => {
  const project = readProject(BENCH);
  const group = project.groups.find((g) => g.id === 'rendezvous');
  const answer = await inspectGroup(env(project.root, '', project.run.pythonpath), project, group);
  assert.equal(answer.ok, true, answer.error);
  assert.deepEqual(answer.members.map((m) => m.id), ['IVI', 'ADAS']);
  const links = answer.links.map((l) => `${l.from.member}.${l.from.node} -> ${l.to.member}.${l.to.node} (${l.label})`).sort();
  assert.deepEqual(links, ['ADAS.announce -> IVI.meet (bench.ADAS.ready = 1)',
                           'IVI.announce -> ADAS.meet (bench.IVI.ready = 1)']);
  // the port matches robot_aio.sync_links on plain data too
  assert.deepEqual(syncLinks([{ id: 'a', values: {}, flow: { tests: [{ steps: [{ id: 's', kind: 'keyword', keyword: 'set_signal', args: ['x', '1.0'] }] }] } },
                              { id: 'b', values: {}, flow: { tests: [{ steps: [{ id: 'g', kind: 'gate', args: ['x', '==', '1'] }] }] } }]).length, 1);
});

test('a group run: both members start together and meet through the shared signal store', { skip: fs.existsSync(BENCH) ? skip : 'no bench demo' }, async () => {
  const project = readProject(BENCH);
  const group = project.groups.find((g) => g.id === 'rendezvous');
  const runDir = tmp();
  const runs = group.members.map((m) => new RobotRun(planRun({
    env: env(project.root, '', project.run.pythonpath), target: memberPath(project, m),
    outDir: path.join(runDir, m.id), runDir, variables: m.variables,
    args: project.run.args, extraEnv: { ...project.run.env, ...group.env },
  })));
  const ends = runs.map((r) => finished(r, 120000));
  runs.forEach((r) => r.start());
  const results = await Promise.all(ends);
  assert.deepEqual(results.map((r) => r.status), ['passed', 'passed'], JSON.stringify(results));
  for (const r of runs) assert.equal(r.position.counts.meet.pass, 1, 'each gate saw the other member');
  fs.rmSync(runDir, { recursive: true, force: true });
});
