// The Runs view's data: run folders listed from run.json and the tail of
// output.xml, checkpoints and snapshots, the age filter, the summary helper
// (python/run_summary.py), and a real run that is listed afterwards.
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
const { parseStamp, parseRobotTime, summarizeOutputText, summarizeOutput, sourceOfOutput, listCheckpoints, readRun, scanRuns, olderThan,
        maybeAlive, writeRunInfo, readRunInfo, readOutputDetail, formatDuration } = require('../out/backend/history.js');
const { writeFlowReport } = require('../out/backend/report.js');
const { planRun, RobotRun } = require('../out/backend/run.js');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const COUNTDOWN = path.join(ROOT, 'test/fixtures/countdown.flow.json');
const env = (cwd) => ({ python: PYTHON, robotSource: FORK, pythonPath: [], cwd, timeoutMs: 90000, helpersDir: path.join(ROOT, 'python') });
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'robot-flow-history-'));
const skip = fs.existsSync(path.join(FORK, 'robot')) ? false : `needs the fork at ${FORK}`;

/** A small output.xml in Robot 7's shape (or Robot 6's with starttime/endtime). */
function outputXml({ suite = 'Laps', source = 'D:\\flows\\laps.flow.json', tests = [['Warm up', 'PASS'], ['Laps', 'FAIL', 'Lap 3 took too long']],
                     status = 'FAIL', robot6 = false, truncated = false } = {}) {
  const t = (i) => robot6
    ? `starttime="20261011 14:02:0${i}.100" endtime="20261011 14:02:0${i + 1}.600"`
    : `start="2026-10-11T14:02:0${i}.100000" elapsed="1.5"`;
  const counts = { pass: 0, fail: 0, skip: 0, unknown: 0 };
  let body = '';
  tests.forEach(([name, st, msg], i) => {
    counts[st.toLowerCase()]++;
    body += `<test id="s1-t${i + 1}" name="${name}">\n<kw name="Log" owner="BuiltIn">\n<arg>x &amp; y</arg>\n<status status="PASS" ${t(i)}/>\n</kw>\n`
          + `<status status="${st}" ${t(i)}>${msg || ''}</status>\n</test>\n`;
  });
  const head = `<?xml version="1.0" encoding="UTF-8"?>\n<robot generator="Robot 7.5 (Python 3.13.2 on win32)" generated="2026-10-11T14:02:00.000000" rpa="false" schemaversion="5">\n`
             + `<suite id="s1" name="${suite}" source="${source}">\n${body}`;
  if (truncated) return head;
  const total = `<stat pass="${counts.pass}" fail="${counts.fail}" skip="${counts.skip}" unknown="${counts.unknown}">All Tests</stat>`;
  return head + `<status status="${status}" ${robot6 ? 'starttime="20261011 14:02:00.000" endtime="20261011 14:02:09.000"' : 'start="2026-10-11T14:02:00.000000" elapsed="9.0"'}/>\n</suite>\n`
       + `<statistics>\n<total>\n${total}\n</total>\n<tag>\n</tag>\n<suite>\n<stat name="${suite}" id="s1" pass="${counts.pass}" fail="${counts.fail}" skip="${counts.skip}" unknown="${counts.unknown}">${suite}</stat>\n</suite>\n</statistics>\n<errors>\n</errors>\n</robot>\n`;
}

function runFolder(root, name, { xml, info, files = {} } = {}) {
  const dir = path.join(root, name);
  fs.mkdirSync(dir, { recursive: true });
  if (xml) fs.writeFileSync(path.join(dir, 'output.xml'), xml);
  if (info) writeRunInfo(dir, info);
  for (const [f, text] of Object.entries(files)) fs.writeFileSync(path.join(dir, f), text);
  return dir;
}

test('a folder name stamped by the extension: its time and the name', () => {
  const s = parseStamp('20261011-140205-05_parallel_ramp');
  assert.equal(s.name, '05_parallel_ramp');
  const d = new Date(s.time);
  assert.deepEqual([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes(), d.getSeconds()], [2026, 9, 11, 14, 2, 5]);
  assert.equal(parseStamp('results'), null);
  assert.equal(parseStamp('2026-10-11'), null);
});

test('durations as the view shows them', () => {
  assert.deepEqual([null, 0.53, 41, 185, 7260].map(formatDuration), ['', '0.5 s', '41 s', '3 min 5 s', '2 h 1 min']);
});

test('Robot 7 and Robot 6 times', () => {
  assert.equal(new Date(parseRobotTime('2026-10-11T14:02:05.100000')).getSeconds(), 5);
  assert.equal(new Date(parseRobotTime('20261011 14:02:05.100')).getMinutes(), 2);
  assert.equal(parseRobotTime('N/A'), null);
  assert.equal(parseRobotTime(undefined), null);
});

test('the tail of an output.xml: top status, suite name, start, elapsed, counts', () => {
  const s = summarizeOutputText(outputXml());
  assert.equal(s.complete, true);
  assert.equal(s.status, 'FAIL');
  assert.equal(s.suite, 'Laps');
  assert.equal(s.elapsed, 9);
  assert.equal(new Date(s.start).getHours(), 14);
  assert.deepEqual(s.total, { pass: 1, fail: 1, skip: 0, unknown: 0 });
  const six = summarizeOutputText(outputXml({ robot6: true, status: 'PASS', tests: [['One', 'PASS']] }));
  assert.equal(six.status, 'PASS');
  assert.equal(six.elapsed, 9);
  assert.equal(six.total.pass, 1);
  const cut = summarizeOutputText(outputXml({ truncated: true }));
  assert.equal(cut.complete, false);
  assert.equal(cut.status, null);
  const amp = summarizeOutputText(outputXml({ suite: 'A &amp; B' }));
  assert.equal(amp.suite, 'A & B');
});

test('a results root: runs newest first, groups by their members, checkpoints, the root itself, junk skipped', () => {
  const root = tmp();
  try {
    runFolder(root, '20261011-140200-05_parallel_ramp', { xml: outputXml({ suite: 'Ramp', status: 'PASS', tests: [['Ramp And Power Up', 'PASS'], ['A Failed Branch Stops The Other', 'PASS']] }),
      info: { title: '05_parallel_ramp.flow.json', target: 'D:\\flows\\05_parallel_ramp.flow.json', kind: 'flow', variables: { CYCLES: '3' }, started: new Date(2026, 9, 11, 14, 2).getTime(), ended: new Date(2026, 9, 11, 14, 2, 9).getTime(), status: 'passed' },
      files: { 'log.html': '', 'report.html': '', 'flow.html': '' } });
    runFolder(root, '20261011-134000-laps', { xml: outputXml({ tests: [['Warm up', 'PASS'], ['Laps', 'UNKNOWN', 'Stopped at the checkpoint']], status: 'UNKNOWN' }),
      files: { 'laps.checkpoint.json': JSON.stringify({ flow: 'D:\\flows\\laps.flow.json', completed: [] }),
               'laps.snapshot-before-flash.json': JSON.stringify({ flow: 'D:\\flows\\laps.flow.json' }),
               'laps.snapshot-broken.json': '{ not json' } });
    runFolder(root, '20261011-131200-signals_smoke', { xml: outputXml({ suite: 'Signals Smoke', source: 'D:\\suites\\signals_smoke.robot', tests: [['A', 'PASS'], ['B', 'FAIL', 'boom'], ['C', 'PASS'], ['D', 'PASS']] }) });
    const group = runFolder(root, '20261011-125000-group-rendezvous', { info: { title: 'Rendezvous', target: null, kind: 'group', members: ['IVI', 'ADAS'], variables: {}, started: new Date(2026, 9, 11, 12, 50).getTime(), ended: new Date(2026, 9, 11, 12, 50, 12).getTime(), status: 'passed' } });
    runFolder(group, 'IVI', { xml: outputXml({ suite: 'Ivi', status: 'PASS', tests: [['Meet', 'PASS']] }) });
    runFolder(group, 'ADAS', { xml: outputXml({ suite: 'Adas', status: 'PASS', tests: [['Meet', 'PASS']] }) });
    runFolder(root, '20261011-150000-died', { xml: outputXml({ truncated: true }), info: { title: 'died.robot', target: 'D:\\died.robot', kind: 'suite', variables: {}, started: new Date(2026, 9, 11, 15).getTime(), status: 'running' } });
    runFolder(root, 'junk');
    runFolder(root, 'cli-group-without-info');
    runFolder(path.join(root, 'cli-group-without-info'), 'rig_a', { xml: outputXml({ suite: 'Rig A', status: 'PASS', tests: [['One', 'PASS']] }) });
    fs.writeFileSync(path.join(root, 'output.xml'), outputXml({ suite: 'Hello Flow', status: 'PASS', tests: [['Hello Flow', 'PASS']] }));

    const runs = scanRuns(root);
    const byName = Object.fromEntries(runs.map((r) => [r.name, r]));
    assert.ok(!byName.junk, 'a folder without results is not a run');
    assert.deepEqual(runs.filter((r) => parseStamp(r.name)).slice(0, 5).map((r) => r.name),
                     ['20261011-150000-died', '20261011-140200-05_parallel_ramp', '20261011-134000-laps', '20261011-131200-signals_smoke', '20261011-125000-group-rendezvous'],
                     'newest first');

    const ramp = byName['20261011-140200-05_parallel_ramp'];
    assert.equal(ramp.status, 'passed');
    assert.equal(ramp.kind, 'flow');
    assert.equal(ramp.title, '05_parallel_ramp.flow.json');
    assert.equal(ramp.target, 'D:\\flows\\05_parallel_ramp.flow.json');
    assert.equal(ramp.info.variables.CYCLES, '3');
    assert.ok(ramp.files.log && ramp.files.report && ramp.files.flow && ramp.files.output);
    assert.equal(ramp.elapsed, 9);

    const laps = byName['20261011-134000-laps'];
    assert.equal(laps.status, 'stopped', 'UNKNOWN at the top: a flow stop');
    assert.equal(laps.kind, 'flow', 'a checkpoint says it was a flow');
    assert.equal(laps.title, 'laps');
    assert.equal(laps.target, 'D:\\flows\\laps.flow.json', 'the flow named in the checkpoint');
    assert.deepEqual(laps.checkpoints.map((c) => [c.kind, c.name]), [['checkpoint', 'checkpoint'], ['snapshot', 'before-flash']], 'the broken one is left out');
    assert.deepEqual(laps.total, { pass: 1, fail: 0, skip: 0, unknown: 1 });

    const smoke = byName['20261011-131200-signals_smoke'];
    assert.equal(smoke.status, 'failed');
    assert.equal(smoke.kind, 'suite', 'the output names a .robot file');
    assert.equal(smoke.target, 'D:\\suites\\signals_smoke.robot', 'the file that ran, from the head of the output');
    assert.equal(sourceOfOutput(path.join(smoke.dir, 'output.xml')), 'D:\\suites\\signals_smoke.robot');
    assert.equal(smoke.total.fail, 1);
    assert.equal(new Date(smoke.started).getHours(), 14, 'started: from the output when there is no run.json');

    const rendezvous = byName['20261011-125000-group-rendezvous'];
    assert.equal(rendezvous.kind, 'group');
    assert.equal(rendezvous.status, 'passed');
    assert.deepEqual(rendezvous.members.map((m) => [m.name, m.status, m.title, m.kind]), [['IVI', 'passed', 'IVI', 'flow'], ['ADAS', 'passed', 'ADAS', 'flow']], 'members are flows: their output names a flow file');
    assert.deepEqual(rendezvous.total, { pass: 2, fail: 0, skip: 0, unknown: 0 });
    assert.equal(rendezvous.elapsed, 12);

    const died = byName['20261011-150000-died'];
    assert.equal(died.status, 'incomplete', 'run.json says running, the output has no end');

    const cli = byName['cli-group-without-info'];
    assert.equal(cli.kind, 'group');
    assert.equal(cli.members.length, 1);
    assert.equal(cli.status, 'passed');

    const self = runs.find((r) => r.dir === root);
    assert.ok(self, 'the root itself holds a command-line run');
    assert.equal(self.title, 'Hello Flow');

    // the age filter: by the end when known
    // (every synthetic output ends 14:02:09; "died" has no end: it counts by the time its output was last written,
    // and not at all while that is less than an hour ago, because a command-line run may still be writing it)
    const now = new Date(2026, 9, 12, 14, 3).getTime();
    assert.deepEqual(olderThan(runs, 24 * 3600 * 1000, now).map((r) => r.name).sort(),
                     ['20261011-125000-group-rendezvous', '20261011-131200-signals_smoke', '20261011-134000-laps',
                      '20261011-140200-05_parallel_ramp', 'cli-group-without-info', path.basename(root)].sort());
    assert.ok(died.updated !== null && Date.now() - died.updated < 60000, 'written just now');
    assert.equal(maybeAlive(died), true, 'no end, written just now: it may still run');
    assert.ok(!olderThan(runs, 0).some((r) => r.name === died.name), 'never deleted while it may run');
    // (a paused or gated flow writes nothing for a while: a day of silence means it is gone)
    assert.equal(olderThan(runs, 0, Date.now() + 2 * 3600 * 1000).some((r) => r.name === died.name), false, 'still kept two hours later');
    assert.equal(olderThan(runs, 0, Date.now() + 25 * 3600 * 1000).some((r) => r.name === died.name), true, 'stale a day later');
    assert.equal(olderThan(runs, 0, Date.now() + 25 * 3600 * 1000).length, runs.length, 'all of them then');
    // a run of the extension records its processes: gone processes mean a dead run, whatever the time
    // (the pid is trusted for a week after the start, so this run starts now, not on the fixture's date)
    writeRunInfo(died.dir, { ...died.info, started: Date.now(), pids: { main: 999999999 } });
    const [deadByPid] = scanRuns(root).filter((r) => r.name === died.name);
    assert.equal(maybeAlive(deadByPid), false, 'its process is gone');
    writeRunInfo(died.dir, { ...died.info, started: Date.now(), pids: { main: process.pid } });
    const [aliveByPid] = scanRuns(root).filter((r) => r.name === died.name);
    assert.equal(maybeAlive(aliveByPid, Date.now() + 48 * 3600 * 1000), true, 'its process lives: kept however long ago it wrote');

    assert.equal(readRunInfo(path.join(root, 'junk')), null);
    assert.equal(summarizeOutput(path.join(root, 'nothing.xml')), null);
    assert.deepEqual(listCheckpoints(path.join(root, 'nope')), []);
    assert.equal(readRun(path.join(root, 'junk')).status, 'unknown');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('run_summary.py: the tests of an output.xml, streamed; a cut file gives what it has', async () => {
  const root = tmp();
  try {
    const full = path.join(root, 'output.xml');
    fs.writeFileSync(full, outputXml());
    const d = await readOutputDetail(env(root), full);
    assert.equal(d.ok, true, d.error);
    assert.equal(d.suite.name, 'Laps');
    assert.equal(d.suite.status, 'FAIL');
    assert.equal(d.suite.elapsed, 9);
    assert.deepEqual(d.tests.map((t) => [t.name, t.status, t.elapsed, t.message]), [['Warm up', 'PASS', 1.5, ''], ['Laps', 'FAIL', 1.5, 'Lap 3 took too long']]);
    assert.deepEqual(d.total, { pass: 1, fail: 1, skip: 0, unknown: 0 });
    assert.equal(d.partial, false);

    const six = path.join(root, 'six.xml');
    fs.writeFileSync(six, outputXml({ robot6: true }));
    const d6 = await readOutputDetail(env(root), six);
    assert.equal(d6.ok, true, d6.error);
    assert.equal(d6.tests[0].elapsed, 1.5, 'Robot 6: endtime - starttime');

    const cut = path.join(root, 'cut.xml');
    fs.writeFileSync(cut, outputXml({ truncated: true }));
    const dc = await readOutputDetail(env(root), cut);
    assert.equal(dc.ok, true, dc.error);
    assert.equal(dc.partial, true);
    assert.deepEqual(dc.tests.map((t) => t.name), ['Warm up', 'Laps']);

    const none = await readOutputDetail(env(root), path.join(root, 'missing.xml'));
    assert.equal(none.ok, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a real run is listed from its folder, with its phases', { skip }, async () => {
  const root = tmp();
  try {
    const outDir = path.join(root, '20261011-160000-countdown');
    const run = new RobotRun(planRun({ env: env(ROOT), target: COUNTDOWN, outDir, variables: { FROM: '3' } }));
    writeRunInfo(outDir, { title: 'countdown.flow.json', target: COUNTDOWN, kind: 'flow', variables: { FROM: '3' }, started: Date.now(), status: 'running' });
    run.start();
    const status = await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('run did not end')), 120000);
      run.on('exit', (s) => { clearTimeout(t); resolve(s); });
    });
    assert.equal(status, 'passed');
    const [listed] = scanRuns(root);
    assert.equal(listed.name, '20261011-160000-countdown');
    assert.equal(listed.status, 'passed', 'from the output: run.json still says running');
    assert.equal(listed.kind, 'flow');
    assert.equal(listed.target, COUNTDOWN);
    assert.ok(listed.files.log && listed.files.report);
    assert.ok(listed.elapsed > 0);
    const detail = await readOutputDetail(env(ROOT), listed.files.output);
    assert.equal(detail.ok, true, detail.error);
    assert.ok(detail.tests.length >= 1, 'phases listed');
    assert.ok(detail.tests.every((t) => t.status === 'PASS'));
    assert.equal(path.basename(detail.suite.source), 'countdown.flow.json');

    // the flow report, written on demand from the output
    assert.equal(listed.files.flow, null, 'not written with the run (opt-in)');
    const report = await writeFlowReport(env(ROOT), listed.files.output);
    assert.equal(report.ok, true, report.error);
    assert.equal(report.file, path.join(outDir, 'flow.html'));
    assert.ok(fs.statSync(report.file).size > 10000, 'a page');
    assert.equal(scanRuns(root)[0].files.flow, report.file, 'listed now');
    const bad = await writeFlowReport(env(ROOT), path.join(outDir, 'missing.xml'));
    assert.equal(bad.ok, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
