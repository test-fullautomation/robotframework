// The debugger end to end, without VS Code: this test speaks the Debug
// Adapter Protocol to backend/debugSession.js the way VS Code does, and the
// session runs real Robot processes (robot_boot.py + flow_position.py +
// flow_debug.py) on test/fixtures/debug.
//
//   ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC (the fork's src: flows need robot.flow)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { DebugSession } = require('../out/backend/debugSession.js');
const { planRun, RobotRun } = require('../out/backend/run.js');
const { anchors, lineOf } = require('../out/backend/flowMap.js');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const DIR = path.join(ROOT, 'test/fixtures/debug');
const MAIN = path.join(DIR, 'main.flow.json');
const SUB = path.join(DIR, 'sub/step.flow.json');
const SUITE = path.join(DIR, 'suite.robot');
const RESOURCE = path.join(DIR, 'helpers.resource');
const COUNTDOWN = path.join(ROOT, 'test/fixtures/countdown.flow.json');
const PYSUITE = path.join(DIR, 'pysuite.robot');
const MYLIB = path.join(DIR, 'mylib.py');
const skip = fs.existsSync(path.join(FORK, 'robot', 'flow')) ? false : `needs the fork at ${FORK}`;

const line = (file, id) => lineOf(anchors(fs.readFileSync(file, 'utf8')), id);

/** VS Code's side of the protocol, for one session. */
class Client {
  constructor(hooks = {}) {
    this.seq = 1;
    this.pending = new Map();
    this.events = [];
    this.waiting = [];
    this.output = '';
    const launcher = async (args, extra) => {
      const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'robot-flow-debug-'));
      const env = { python: PYTHON, robotSource: FORK, pythonPath: [], cwd: path.dirname(args.target),
                    timeoutMs: 90000, helpersDir: path.join(ROOT, 'python') };
      const plan = planRun({ env, target: args.target, outDir, variables: args.variables,
                             args: [...extra.robotArgs, ...(args.args || [])], extraEnv: extra.env });
      return new RobotRun(plan).start();
    };
    this.session = new DebugSession(launcher, path.join(ROOT, 'python'), hooks);
    this.session.on('message', (m) => this.receive(m));
  }

  receive(m) {
    if (m.type === 'response') {
      const done = this.pending.get(m.request_seq);
      this.pending.delete(m.request_seq);
      if (done) done(m);
    } else if (m.type === 'event') {
      if (m.event === 'output') this.output += m.body.output;
      this.events.push(m);
      this.waiting = this.waiting.filter((w) => !w(m));
    }
  }

  request(command, args = {}) {
    const seq = this.seq++;
    return new Promise((resolve) => {
      this.pending.set(seq, resolve);
      this.session.handleMessage({ type: 'request', seq, command, arguments: args });
    });
  }

  /** The next event `name` after the ones seen so far (`from`: an index into events). */
  event(name, timeoutMs = Number(process.env.DEBUG_TEST_WAIT_MS || 90000)) {
    const seen = this.events.length;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`no ${name} event in ${timeoutMs} ms; output:\n${this.output.slice(-3000)}`)), timeoutMs);
      this.waiting.push((m) => {
        if (m.event !== name) return false;
        clearTimeout(timer);
        resolve(m);
        return true;
      });
      void seen;
    });
  }

  async start(target, breakpoints, { filters = [], stopOnEntry = false } = {}) {
    await this.request('initialize', { adapterID: 'robotflow' });
    const launched = this.request('launch', { target, stopOnEntry });
    for (const [file, lines] of breakpoints) {
      const r = await this.request('setBreakpoints', { source: { path: file }, breakpoints: lines.map((l) => ({ line: l })) });
      assert.equal(r.success, true);
      this.verified = (this.verified || []).concat(r.body.breakpoints);
    }
    await this.request('setExceptionBreakpoints', { filters });
    const stopped = this.event('stopped');
    await this.request('configurationDone');
    const l = await launched;
    assert.equal(l.success, true, l.message);
    return stopped;
  }

  async top() {
    const r = await this.request('stackTrace', { threadId: 1 });
    assert.equal(r.success, true, r.message);
    return r.body.stackFrames;
  }

  async step(command) {
    const stopped = this.event('stopped');
    const r = await this.request(command, { threadId: 1 });
    assert.equal(r.success, true, r.message);
    return stopped;
  }

  async variables(ref) {
    const r = await this.request('variables', { variablesReference: ref });
    assert.equal(r.success, true, r.message);
    return Object.fromEntries(r.body.variables.map((v) => [v.name, v]));
  }

  /** Whatever happened: end the run and the session (a failed test must not leave a paused Robot). */
  async close() {
    await Promise.race([this.request('disconnect', { terminateDebuggee: true }), new Promise((r) => setTimeout(r, 2000))]);
    this.session.dispose();
  }

  async evaluate(expression, context = 'repl') {
    return this.request('evaluate', { expression, context, frameId: 1 });
  }
}

test('a flow: breakpoints on steps (a sub-flow\'s too), Step Into a resource keyword, Step Out, variables, the Debug Console',
     { skip, timeout: 240000 }, async (t) => {
  const c = new Client();
  t.after(() => c.close());
  let stopped = await c.start(MAIN, [[MAIN, [line(MAIN, 'greet')]], [SUB, [line(SUB, 'check')]]]);
  assert.ok(c.verified.every((b) => b.verified), JSON.stringify(c.verified));
  assert.equal(stopped.body.reason, 'breakpoint');
  let frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'main.flow.json');
  assert.equal(frames[0].line, line(MAIN, 'greet'));
  assert.match(frames[0].name, /^greet: /);
  assert.equal((await c.variables(1))['${WHO}'].value, 'bench');

  // Into the resource keyword the step calls: its first keyword, in the resource.
  await c.step('stepIn');
  frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'helpers.resource');
  assert.equal(frames[0].line, 4);
  assert.equal(frames[1].line, line(MAIN, 'greet'), 'the flow step is the caller');
  assert.equal((await c.evaluate('${who}')).body.result, 'bench');
  const kw = await c.evaluate('Set Variable    seven');
  assert.equal(kw.success, true, kw.message);
  assert.equal(kw.body.result, 'seven');
  const hover = await c.evaluate('${who}', 'hover');
  assert.equal(hover.body.result, 'bench');

  // Step Over inside the keyword, then out of it: the next step of the flow.
  await c.step('next');
  assert.equal((await c.top())[0].line, 5);
  await c.step('stepOut');
  frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'main.flow.json');
  assert.equal(frames[0].line, line(MAIN, 'call'));

  // On to the breakpoint inside the sub-flow, called with LEVEL=5.
  stopped = await c.step('continue');
  assert.equal(stopped.body.reason, 'breakpoint');
  frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'step.flow.json');
  assert.equal(frames[0].line, line(SUB, 'check'));
  assert.match(frames[0].name, /^One Step::check: /);
  assert.equal(path.basename(frames[1].source.path), 'main.flow.json');
  assert.equal((await c.evaluate('${LEVEL}')).body.result, '5');

  const exited = c.event('exited');
  const terminated = c.event('terminated');
  await c.request('continue', { threadId: 1 });
  assert.equal((await exited).body.exitCode, 0, c.output.slice(-2000));
  await terminated;
});

test('a suite: a line breakpoint, Step Over a keyword, stop where a keyword fails', { skip, timeout: 240000 }, async (t) => {
  const c = new Client();
  t.after(() => c.close());
  const stopped = await c.start(SUITE, [[SUITE, [10]]], { filters: ['failed'] });
  assert.equal(stopped.body.reason, 'breakpoint');
  let frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'suite.robot');
  assert.equal(frames[0].line, 10);
  const vars = await c.variables(1);
  assert.equal(vars['${ITEMS}'].value, 'one');
  const list = vars['@{LIST}'];
  assert.ok(list.variablesReference > 0, 'a list can be expanded');
  assert.deepEqual(Object.values(await c.variables(list.variablesReference)).map((v) => v.value), ['a', 'b', 'c']);

  await c.step('next');                       // over Greet: not into the resource
  frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'suite.robot');
  assert.equal(frames[0].line, 11);

  const failed = await c.step('continue');    // the next test fails
  assert.equal(failed.body.reason, 'exception');
  assert.match(failed.body.description, /Should Be Equal failed/);
  assert.equal((await c.top())[0].line, 15);

  const exited = c.event('exited');
  await c.request('continue', { threadId: 1 });
  assert.equal((await exited).body.exitCode, 1, 'one test failed');
});

test('Pause, then Stop while paused: the run ends gracefully', { skip, timeout: 240000 }, async (t) => {
  const c = new Client();
  t.after(() => c.close());
  const stopped = await c.start(COUNTDOWN, [], { stopOnEntry: true });
  assert.equal(stopped.body.reason, 'pause');
  const first = (await c.top())[0];
  assert.equal(path.basename(first.source.path), 'countdown.flow.json');
  assert.equal(first.line, line(COUNTDOWN, 'hello'));
  // Run on, pause again (it stops at the next step), then stop from there.
  await c.request('continue', { threadId: 1 });
  const paused = c.event('stopped');
  await c.request('pause', { threadId: 1 });
  assert.equal((await paused).body.reason, 'pause');
  const terminated = c.event('terminated');
  await c.request('disconnect', { terminateDebuggee: true });
  await terminated;
});

/**
 * VS Code's Python debugger, as far as this test needs it: a client of the
 * debugpy adapter in the Robot process (the Debug Adapter Protocol over TCP,
 * with Content-Length headers).
 */
class PythonClient {
  constructor(port) {
    this.seq = 1;
    this.pending = new Map();
    this.waiting = [];
    this.buffer = Buffer.alloc(0);
    this.socket = net.connect(port, '127.0.0.1');
    this.socket.on('data', (d) => this.data(d));
    this.socket.on('error', () => undefined);
    this.connected = new Promise((resolve) => this.socket.once('connect', resolve));
  }

  data(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    for (;;) {
      const head = this.buffer.indexOf('\r\n\r\n');
      if (head < 0) return;
      const length = Number(/Content-Length: (\d+)/i.exec(this.buffer.slice(0, head).toString())[1]);
      if (this.buffer.length < head + 4 + length) return;
      const m = JSON.parse(this.buffer.slice(head + 4, head + 4 + length).toString('utf8'));
      this.buffer = this.buffer.slice(head + 4 + length);
      if (m.type === 'response') {
        const done = this.pending.get(m.request_seq);
        this.pending.delete(m.request_seq);
        if (done) done(m);
      } else if (m.type === 'event') {
        this.waiting = this.waiting.filter((w) => !w(m));
      }
    }
  }

  request(command, args = {}) {
    const seq = this.seq++;
    const body = Buffer.from(JSON.stringify({ seq, type: 'request', command, arguments: args }), 'utf8');
    this.socket.write(`Content-Length: ${body.length}\r\n\r\n`);
    this.socket.write(body);
    return new Promise((resolve) => this.pending.set(seq, resolve));
  }

  event(name, timeoutMs = 60000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`no Python ${name} event`)), timeoutMs);
      this.waiting.push((m) => {
        if (m.event !== name) return false;
        clearTimeout(timer);
        resolve(m);
        return true;
      });
    });
  }

  /** initialize, attach, configurationDone -- what VS Code does to attach. */
  async attach() {
    await this.connected;
    await this.request('initialize', { adapterID: 'debugpy', clientID: 'test', pathFormat: 'path',
                                       linesStartAt1: true, columnsStartAt1: true });
    const initialized = this.event('initialized');
    const attached = this.request('attach', { justMyCode: true, subProcess: false,
                                              rules: [{ module: 'robot', include: false }] });
    await initialized;
    await this.request('configurationDone');
    const r = await attached;
    assert.equal(r.success, true, r.message);
  }

  close() {
    this.socket.destroy();
  }
}

test('Step Into a keyword of a Python library: stops in the function, back in Robot when it returns',
     { skip, timeout: 240000 }, async (t) => {
  let python = null;
  const c = new Client({
    attachPython: async (port) => {
      python = new PythonClient(port);
      await python.attach();
      return true;
    },
    breakInPython: async (file, line) => {
      const r = await python.request('setBreakpoints', { source: { path: file }, breakpoints: [{ line }] });
      return r.success;
    },
  });
  t.after(async () => { await c.close(); python?.close(); });

  const stopped = await c.start(PYSUITE, [[PYSUITE, [6]]]);
  assert.equal(stopped.body.reason, 'breakpoint');
  assert.ok(python, 'the Python debugger was attached before the run started');

  // Step Into "Add Numbers": the Python debugger stops in add_numbers, at its first line.
  const inPython = python.event('stopped');
  const r = await c.request('stepIn', { threadId: 1 });
  assert.equal(r.success, true, r.message);
  const pyStop = await inPython;
  const trace = await python.request('stackTrace', { threadId: pyStop.body.threadId });
  const top = trace.body.stackFrames[0];
  assert.equal(path.basename(top.source.path), 'mylib.py');
  assert.equal(top.line, 6, 'the first line of the body, after the docstring');
  assert.equal(top.name, 'add_numbers');
  const scopes = await python.request('scopes', { frameId: top.id });
  const locals = await python.request('variables', { variablesReference: scopes.body.scopes[0].variablesReference });
  const values = Object.fromEntries(locals.body.variables.map((v) => [v.name, v.value]));
  assert.equal(values.a, "'1'");
  assert.equal(values.b, "'2'");

  // The function returns: Robot stops at its next step.
  const backInRobot = c.event('stopped');
  await python.request('continue', { threadId: pyStop.body.threadId });
  await backInRobot;
  const frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'pysuite.robot');
  assert.equal(frames[0].line, 7);
  assert.equal((await c.evaluate('${sum}')).body.result, '3');

  const exited = c.event('exited');
  await c.request('continue', { threadId: 1 });
  assert.equal((await exited).body.exitCode, 0);
});

test('Step Into a Python keyword without a Python debugger: the listener\'s own stepper',
     { skip, timeout: 240000 }, async (t) => {
  const c = new Client();
  t.after(() => c.close());
  await c.start(PYSUITE, [[PYSUITE, [6]]]);
  await c.step('stepIn');
  const frames = await c.top();
  assert.equal(path.basename(frames[0].source.path), 'mylib.py');
  assert.equal(frames[0].line, 6);
  assert.equal(path.basename(frames[1].source.path), 'pysuite.robot', 'the Robot frame is the caller');
  const scopes = await c.request('scopes', { frameId: 1 });
  assert.equal(scopes.body.scopes[0].name, 'Locals');
  const locals = await c.variables(scopes.body.scopes[0].variablesReference);
  assert.equal(locals.a.value, '1');
  assert.equal((await c.evaluate('int(a) + int(b)')).body.result, '3');
  await c.step('stepOut');                    // out of the function: Robot's next step
  assert.equal((await c.top())[0].line, 7);
  const exited = c.event('exited');
  await c.request('continue', { threadId: 1 });
  assert.equal((await exited).body.exitCode, 0);
});
