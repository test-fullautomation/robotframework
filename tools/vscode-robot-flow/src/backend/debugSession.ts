// The debugger of a Robot Framework run, speaking the Debug Adapter Protocol
// to VS Code and JSON lines to python/flow_debug.py in the Robot process.
// No VS Code API: VS Code runs it inline (debug.ts), test/ drives it with
// plain Node.
//
//   VS Code ──DAP──► DebugSession ──launch()──► the run (RunManager / RobotRun)
//                         ▲  │
//                 stopped │  │ breakpoints, continue, step, variables, evaluate
//                         │  ▼
//                  127.0.0.1:<port>  ◄── flow_debug.py (a Robot listener)
import { EventEmitter } from 'node:events';
import * as fs from 'node:fs';
import * as net from 'node:net';
import * as path from 'node:path';
import { FLOW_SUFFIX, anchors, lineOf, runName, stepAt, stepOf, subflows } from './flowMap';

export interface LaunchArgs {
  target: string;
  variables?: Record<string, string>;
  args?: string[];
  stopOnEntry?: boolean;
  noDebug?: boolean;
  /** Also debug the Python libraries' code (debugpy in the Robot process); default true. */
  python?: boolean;
  /** Python: step only through the user's code, not site-packages; default true. */
  justMyCode?: boolean;
  [key: string]: unknown;
}

/** The run a launch started: its console output, its end, a way to stop it. */
export interface RunHandle {
  on(event: 'output', fn: (text: string) => void): unknown;
  on(event: 'exit', fn: (status: string, code: number | null) => void): unknown;
  stop(): void;
}

export interface Launcher {
  /** Start the run of `args.target` with these extra Robot options and environment. */
  (args: LaunchArgs, extra: { robotArgs: string[]; env: Record<string, string> }): Promise<RunHandle>;
}

export interface DebugHooks {
  /** Where the run stopped (a flow step's run name or null), or null when it goes on. */
  paused?(target: string, node: string | null, reason: string | null): void;
  /** Read a file's current text (an editor's unsaved one, else the disk's). */
  text?(file: string): string | null;
  /** Where a debugpy without the interpreter's own can be imported from. */
  debugpyPath?: string;
  /** Attach a Python debugger to debugpy on `port`; true when it is attached. */
  attachPython?(port: number, opts: { justMyCode: boolean }): Promise<boolean>;
  /** Stop the Python debugger once at `file`:`line` (a function's first line); true when set. */
  breakInPython?(file: string, line: number): Promise<boolean>;
  /** The Robot side stopped: a one-time Python breakpoint not reached is dropped. */
  robotStopped?(): void;
}

interface Frame {
  name: string; node: string | null; source: string; lineno: number;
  /** A keyword of the user's Python library: its function's first line. */
  py?: { file: string; line: number; function: string };
  /** A Python frame of the listener's own stepper, and the reference of its locals. */
  python?: boolean;
  ref?: number;
}
type Message = Record<string, any>;   // eslint-disable-line @typescript-eslint/no-explicit-any

const THREAD = 1;
export const FAILED_FILTER = 'failed';

export class DebugSession extends EventEmitter {
  private seq = 1;
  private server: net.Server | null = null;
  private socket: net.Socket | null = null;
  private buffer = '';
  private queue: Message[] = [];           // for the listener, until it connects
  private replies = new Map<number, (m: Message) => void>();
  private nextId = 1;
  private run: RunHandle | null = null;
  private target = '';
  private subs = new Map<string, string>();
  private stopOnEntry = false;
  private configured = false;
  private connected = false;
  private frames: Frame[] = [];
  private ended = false;
  /** Per source file: the lines asked for, and what they became. */
  private readonly breakpoints = new Map<string, { lines: number[]; nodes: (string | null)[] }>();
  private filters: string[] = [];
  private helloDone = false;              // the listener said hello and the Python debugger is attached (or not)
  private configSent = false;
  private python = false;                 // a Python debugger is attached
  private justMyCode = true;

  constructor(private readonly launcher: Launcher, readonly helpersDir: string, private readonly hooks: DebugHooks = {}) {
    super();
  }

  /** One message from VS Code. */
  handleMessage(msg: Message): void {
    if (msg.type !== 'request') return;
    void this.dispatch(msg).catch((e: Error) => this.respond(msg, false, {}, e.message));
  }

  private send(m: Message): void {
    this.emit('message', { seq: this.seq++, ...m });
  }

  private respond(req: Message, success: boolean, body: Message = {}, message?: string): void {
    this.send({ type: 'response', request_seq: req.seq, command: req.command, success, body, ...(message ? { message } : {}) });
  }

  private event(event: string, body: Message = {}): void {
    this.send({ type: 'event', event, body });
  }

  private output(text: string, category = 'stdout'): void {
    this.event('output', { category, output: text });
  }

  private async dispatch(req: Message): Promise<void> {
    const a = req.arguments || {};
    switch (req.command) {
      case 'initialize':
        this.respond(req, true, {
          supportsConfigurationDoneRequest: true,
          supportsEvaluateForHovers: true,
          supportsTerminateRequest: true,
          exceptionBreakpointFilters: [{ filter: FAILED_FILTER, label: 'Failed keyword', default: false,
                                         description: 'Stop where a keyword fails, before its callers report it.' }],
        });
        this.event('initialized');
        return;
      case 'launch':
        await this.launch(req, a as LaunchArgs);
        return;
      case 'setBreakpoints':
        this.respond(req, true, { breakpoints: this.setBreakpoints(a.source?.path || '', (a.breakpoints || []).map((b: Message) => b.line)) });
        return;
      case 'setExceptionBreakpoints':
        this.filters = a.filters || [];
        this.toListener({ cmd: 'exceptions', filters: this.filters });
        this.respond(req, true, {});
        return;
      case 'configurationDone':
        this.configured = true;
        this.sendConfigurationDone();
        this.respond(req, true);
        return;
      case 'threads':
        this.respond(req, true, { threads: [{ id: THREAD, name: 'Robot' }] });
        return;
      case 'stackTrace':
        this.respond(req, true, this.stackTrace());
        return;
      case 'scopes': {
        const frame = this.frames[(a.frameId || 1) - 1];
        if (frame?.python && frame.ref) {
          this.respond(req, true, { scopes: [{ name: 'Locals', variablesReference: frame.ref, expensive: false, presentationHint: 'locals' }] });
          return;
        }
        this.respond(req, true, { scopes: [
          { name: 'Variables', variablesReference: 1, expensive: false, presentationHint: 'locals' },
          { name: 'Arguments', variablesReference: 2, expensive: false, presentationHint: 'arguments' },
        ] });
        return;
      }
      case 'variables': {
        const r = await this.ask({ cmd: 'variables', ref: a.variablesReference });
        this.respond(req, r.ok, { variables: r.ok ? r.body : [] }, r.error);
        return;
      }
      case 'evaluate': {
        const expr = String(a.expression || '');
        if (a.context === 'hover' && !/^[$@&%]\{.+\}(\[.*\])?$/.test(expr.trim())) {
          this.respond(req, false, {}, 'not a variable');
          return;
        }
        if (!this.frames.length) {
          this.respond(req, false, {}, 'The run is not stopped.');
          return;
        }
        const r = await this.ask({ cmd: 'evaluate', expression: expr });
        this.respond(req, r.ok, r.ok ? r.body : {}, r.error);
        return;
      }
      case 'stepIn': {
        // Into a keyword of the user's Python library: break in its function,
        // and let Robot step over it -- back to Robot when the function returns.
        const py = this.frames[0]?.py;
        const inPython = !!py && this.python && !!this.hooks.breakInPython &&
                         await this.hooks.breakInPython(py.file, py.line);
        this.resumed();
        // Without a Python debugger attached, the listener's own stepper goes into the function.
        this.toListener(inPython ? { cmd: 'next' } : py ? { cmd: 'stepIn', trace: true, py } : { cmd: 'stepIn' });
        this.respond(req, true);
        return;
      }
      case 'continue':
      case 'next':
      case 'stepOut':
        this.resumed();
        this.toListener({ cmd: req.command });
        this.respond(req, true, req.command === 'continue' ? { allThreadsContinued: true } : {});
        return;
      case 'pause':
        this.toListener({ cmd: 'pause' });
        this.respond(req, true);
        return;
      case 'terminate':
      case 'disconnect':
        this.toListener({ cmd: 'terminate' });
        this.resumed();
        if (!this.ended) this.run?.stop();
        this.respond(req, true);
        if (req.command === 'disconnect') this.close();
        return;
      default:
        this.respond(req, false, {}, `${req.command} is not supported.`);
    }
  }

  private async launch(req: Message, args: LaunchArgs): Promise<void> {
    if (!args.target) throw new Error('No target: set "target" to a suite, a flow file or a folder.');
    this.target = path.resolve(args.target);
    this.stopOnEntry = !!args.stopOnEntry;
    this.justMyCode = args.justMyCode !== false;
    this.subs = this.target.toLowerCase().endsWith(FLOW_SUFFIX) ? subflows(this.target, (f) => this.readJson(f)) : new Map();
    const robotArgs: string[] = [];
    const env: Record<string, string> = {};
    if (!args.noDebug) {
      const port = await this.listen();
      robotArgs.push('--listener', path.join(this.helpersDir, 'flow_debug.py'));
      env.MM_DEBUG_PORT = String(port);
      if (args.python !== false && this.hooks.attachPython) {
        env.MM_DEBUGPY = '1';
        if (this.hooks.debugpyPath) env.MM_DEBUGPY_PATH = this.hooks.debugpyPath;
        if (!this.justMyCode) env.MM_PY_ALL = '1';
      }
      // The breakpoints set before the launch, now that the target and its sub-flows are known.
      for (const [file, bp] of this.breakpoints) this.setBreakpoints(file, bp.lines);
    }
    const run = await this.launcher(args, { robotArgs, env });
    this.run = run;
    run.on('output', (text) => this.output(text));
    run.on('exit', (status, code) => {
      this.ended = true;
      this.resumed();
      this.output(`\n${path.basename(this.target)}: ${status} (exit ${code})\n`, 'console');
      this.event('exited', { exitCode: code ?? 1 });
      this.event('terminated');
      this.close();
    });
    this.respond(req, true);
  }

  private listen(): Promise<number> {
    return new Promise((resolve, reject) => {
      const server = net.createServer((socket) => {
        if (this.socket) { socket.destroy(); return; }   // one Robot process per session
        this.socket = socket;
        socket.setEncoding('utf8');
        socket.on('data', (d: string) => this.onData(d));
        socket.on('close', () => { this.socket = null; this.connected = false; });
        socket.on('error', () => { /* the run ended */ });
      });
      server.on('error', reject);
      server.listen(0, '127.0.0.1', () => resolve((server.address() as net.AddressInfo).port));
      this.server = server;
    });
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let cut: number;
    while ((cut = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, cut).trim();
      this.buffer = this.buffer.slice(cut + 1);
      if (!line) continue;
      let m: Message;
      try { m = JSON.parse(line); } catch { continue; }
      this.fromListener(m);
    }
  }

  private fromListener(m: Message): void {
    if (m.id !== undefined && this.replies.has(m.id)) {
      const done = this.replies.get(m.id)!;
      this.replies.delete(m.id);
      done(m);
      return;
    }
    if (m.event === 'hello') {
      this.connected = true;
      this.write({ cmd: 'breakpoints', ...this.listenerBreakpoints() });
      this.write({ cmd: 'exceptions', filters: this.filters });
      for (const q of this.queue.splice(0)) this.write(q);
      if (m.debugpyError) this.output(`Python code is not debugged: ${m.debugpyError}\n`, 'console');
      const attach = m.debugpy && this.hooks.attachPython
        ? this.hooks.attachPython(Number(m.debugpy), { justMyCode: this.justMyCode }).catch(() => false)
        : Promise.resolve(false);
      void attach.then((ok) => {
        this.python = ok;
        this.helloDone = true;
        this.sendConfigurationDone();
      });
    } else if (m.event === 'stopped') {
      this.frames = m.frames || [];
      this.hooks.robotStopped?.();
      const top = this.frames[0];
      const reason = m.reason === 'step' ? 'step' : m.reason;
      this.hooks.paused?.(this.target, top?.node ?? null, reason);
      this.event('stopped', { reason, threadId: THREAD, allThreadsStopped: true,
                              description: m.description || undefined,
                              text: m.description || undefined });
    } else if (m.event === 'continued') {
      this.resumed();
    }
  }

  /** The run starts once VS Code is configured and the listener (and Python debugger) are ready. */
  private sendConfigurationDone(): void {
    if (!this.configured || !this.helloDone || this.configSent) return;
    this.configSent = true;
    this.write({ cmd: 'configurationDone', stopOnEntry: this.stopOnEntry, python: this.python });
  }

  /** The run goes on: no frames, the views no longer show it paused. */
  private resumed(): void {
    if (!this.frames.length) return;
    this.frames = [];
    this.hooks.paused?.(this.target, null, null);
  }

  private write(m: Message): void {
    this.socket?.write(JSON.stringify(m) + '\n');
  }

  /** A message for the listener: now, or when it connects. Config ones replace their older self. */
  private toListener(m: Message): void {
    if (this.connected) { this.write(m); return; }
    if (m.cmd === 'configurationDone') return;          // see sendConfigurationDone
    this.queue = this.queue.filter((q) => q.cmd !== m.cmd);
    if (m.cmd !== 'breakpoints' && m.cmd !== 'exceptions') this.queue.push(m);
  }

  private ask(m: Message): Promise<{ ok: boolean; body?: any; error?: string }> {   // eslint-disable-line @typescript-eslint/no-explicit-any
    if (!this.connected) return Promise.resolve({ ok: false, error: 'The run is not connected.' });
    const id = this.nextId++;
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.replies.delete(id); resolve({ ok: false, error: 'No answer from the run.' }); }, 30000);
      this.replies.set(id, (r) => { clearTimeout(timer); resolve({ ok: !!r.ok, body: r.body, error: r.error }); });
      this.write({ ...m, id });
    });
  }

  // ---- breakpoints ----------------------------------------------------------

  private readJson(file: string): Record<string, unknown> | null {
    try {
      return JSON.parse(this.readText(file) ?? '');
    } catch {
      return null;
    }
  }

  private readText(file: string): string | null {
    const text = this.hooks.text?.(file);
    if (text != null) return text;
    try { return fs.readFileSync(file, 'utf8'); } catch { return null; }
  }

  /** VS Code's breakpoints of one file: flow lines become steps, Robot lines stay lines. */
  private setBreakpoints(file: string, lines: number[]): Message[] {
    const result: Message[] = [];
    const nodes: (string | null)[] = [];
    if (file.toLowerCase().endsWith(FLOW_SUFFIX)) {
      const list = anchors(this.readText(file) || '');
      for (const line of lines) {
        const step = stepAt(list, line);
        const name = step && this.target ? runName(this.target, file, step.id, this.subs) : step ? step.id : null;
        nodes.push(name);
        result.push(step
          ? { verified: !this.target || name !== null, line: step.line,
              message: !this.target || name !== null ? undefined : `${path.basename(file)} is not a sub-flow of this run.` }
          : { verified: false, line, message: 'Not on a step of the flow.' });
      }
    } else {
      for (const line of lines) {
        nodes.push(null);
        result.push({ verified: true, line });
      }
    }
    this.breakpoints.set(file, { lines, nodes });
    if (this.target) this.toListener({ cmd: 'breakpoints', ...this.listenerBreakpoints() });
    return result;
  }

  private listenerBreakpoints(): { nodes: string[]; lines: [string, number][] } {
    const nodes: string[] = [];
    const lines: [string, number][] = [];
    for (const [file, bp] of this.breakpoints) {
      if (file.toLowerCase().endsWith(FLOW_SUFFIX)) bp.nodes.forEach((n) => { if (n) nodes.push(n); });
      else bp.lines.forEach((l) => lines.push([file, l]));
    }
    return { nodes, lines };
  }

  // ---- where it stopped -----------------------------------------------------------

  private stackTrace(): Message {
    const stackFrames = this.frames.map((f, i) => {
      let file = f.source;
      let line = f.lineno || 1;
      let name = f.name;
      if (f.node) {
        const step = stepOf(this.target, f.node, this.subs);
        if (step) {
          file = step.file;
          line = lineOf(anchors(this.readText(step.file) || ''), step.id) || 1;
          name = `${f.node}: ${f.name}`;
        }
      }
      // A keyword the fork generated around a flow's phase or sub-flow has no line of its own.
      const generated = !f.node && file.toLowerCase().endsWith(FLOW_SUFFIX);
      const known = !generated && file && fs.existsSync(file);
      return { id: i + 1, name, line: known ? line : 0, column: known ? 1 : 0,
               source: known ? { name: path.basename(file), path: file } : undefined,
               presentationHint: known ? 'normal' : 'subtle' };
    });
    return { stackFrames, totalFrames: stackFrames.length };
  }

  private close(): void {
    for (const done of this.replies.values()) done({ ok: false, error: 'The run ended.' });
    this.replies.clear();
    this.socket?.destroy();
    this.server?.close();
    this.socket = null;
    this.server = null;
  }

  dispose(): void {
    this.close();
    this.emit('dispose');
    this.removeAllListeners();
  }
}

