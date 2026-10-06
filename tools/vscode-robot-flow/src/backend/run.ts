// Running a suite or a flow the way the Manager GUI does: python/robot_boot.py
// (python -m robot that stops gracefully when its stop file appears), with
// --parser robot.flow for flow files and flow_position.py as the listener
// that writes where the run is (MM_FLOW_POSITION) for the live Diagram.
// No VS Code API here; test/ runs it with plain Node.
import { ChildProcess, spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { RobotEnv, pythonPathFor } from './helpers';

export const FLOW_SUFFIX = '.flow.json';
export const POSITION_FILE = 'flow_position.json';
export const SIGNALS_FILE = 'signals.json';

/** Where a flow run is: flow_position.py's file, as flow-view/live.js draws it. */
export interface Position {
  seq: number;
  node: string | null;
  stack: string[];
  last: { node: string; status: string; time: number } | null;
  counts: Record<string, { pass: number; fail: number }>;
  step: number;
  trail: [number, string][];
  done: boolean;
  [key: string]: unknown;
}

export interface RunRequest {
  env: RobotEnv;
  /** A suite, a flow file or a folder. */
  target: string;
  /** This run's output folder (created). */
  outDir: string;
  variables?: Record<string, string>;
  /** More Robot options, e.g. the project's run.args. */
  args?: string[];
  /** More environment; ${RUN_DIR} becomes runDir (a group's folder) or outDir. */
  extraEnv?: Record<string, string>;
  /** A group run's folder: its members share ROBOT_FLOW_SIGNALS there. */
  runDir?: string;
  dryrun?: boolean;
  /** Step mode (flows): the run pauses before every step of its test phases; Resume goes one on. */
  step?: boolean;
  /** This process's name in the shared store (a group member): `--rig` reaches it alone. */
  rig?: string;
}

export interface RunPlan {
  argv: string[];
  env: NodeJS.ProcessEnv;
  cwd: string;
  outDir: string;
  stopFile: string;
  positionFile: string | null;
  /** The flow's signal store, also its control channel (pause, resume, stop); null for a suite. */
  signalsFile: string | null;
}

const VARIABLE_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function usesFlows(target: string): boolean {
  try {
    if (fs.statSync(target).isFile()) return target.toLowerCase().endsWith(FLOW_SUFFIX);
  } catch {
    return target.toLowerCase().endsWith(FLOW_SUFFIX);
  }
  const walk = (dir: string): boolean => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.isDirectory()) {
        if (!['results', '__pycache__', '.git', 'node_modules'].includes(e.name) && walk(path.join(dir, e.name))) return true;
      } else if (e.name.toLowerCase().endsWith(FLOW_SUFFIX)) {
        return true;
      }
    }
    return false;
  };
  return walk(target);
}

export function planRun(req: RunRequest): RunPlan {
  const flows = usesFlows(req.target);
  const argv = [req.env.python, path.join(req.env.helpersDir, 'robot_boot.py'), '--outputdir', req.outDir,
                '--consolecolors', 'off', '--consolemarkers', 'off', '--consolewidth', '100'];
  if (flows) argv.push('--parser', 'robot.flow');
  for (const [name, value] of Object.entries(req.variables || {}).sort()) {
    if (!VARIABLE_RE.test(name)) throw new Error(`Invalid variable name ${JSON.stringify(name)}: use letters, digits and '_'.`);
    argv.push('--variable', `${name}:${value}`);
  }
  if (req.dryrun) argv.push('--dryrun');
  if (req.step) {
    if (!flows) throw new Error('Step mode is for flow files.');
    // The fork's step mode: a pause before every step, resumed through the signal store.
    argv.push('--variable', 'FLOW_STEP:yes');
  }
  argv.push(...(req.args || []), req.target);

  const runDir = req.runDir || req.outDir;
  const env: NodeJS.ProcessEnv = { ...process.env };
  // A codec suffix (utf-8:surrogateescape) crashes Robot's console writer.
  delete env.PYTHONIOENCODING;
  env.PYTHONPATH = pythonPathFor(req.env);
  env.PYTHONUTF8 = '1';
  env.PYTHONUNBUFFERED = '1';
  const stopFile = path.join(req.outDir, '.stop');
  env.MM_RUN_STOP_FILE = stopFile;
  let positionFile: string | null = null;
  let signalsFile: string | null = null;
  if (flows) {
    signalsFile = path.join(runDir, SIGNALS_FILE);
    env.ROBOT_FLOW_SIGNALS = signalsFile;
    if (req.rig) env.ROBOT_FLOW_RIG = req.rig;
    if (!req.dryrun) {
      positionFile = path.join(req.outDir, POSITION_FILE);
      env.MM_FLOW_POSITION = positionFile;
    }
  }
  for (const [k, v] of Object.entries(req.extraEnv || {})) env[k] = v.replace(/\$\{RUN_DIR\}/g, runDir);
  // The project's or group's environment may name its own store: that one is the channel.
  if (signalsFile) signalsFile = env.ROBOT_FLOW_SIGNALS || signalsFile;
  return { argv, env, cwd: req.env.cwd, outDir: req.outDir, stopFile, positionFile, signalsFile };
}

export type RunStatus = 'running' | 'stopping' | 'passed' | 'failed' | 'stopped' | 'error';

/**
 * One Robot process. Events: 'output' (text), 'position' (Position, when the
 * flow moved on), 'exit' (status, exit code).
 */
export class RobotRun extends EventEmitter {
  status: RunStatus = 'running';
  exitCode: number | null = null;
  position: Position | null = null;
  readonly started = Date.now();
  private child: ChildProcess | null = null;
  private poll: NodeJS.Timeout | null = null;
  private killTimer: NodeJS.Timeout | null = null;
  private stopRequested = false;
  /** The stop file was written (a graceful stop is under way; the next one kills). */
  stopFileSent = false;

  constructor(readonly plan: RunPlan) {
    super();
  }

  start(): this {
    fs.mkdirSync(this.plan.outDir, { recursive: true });
    const [exe, ...args] = this.plan.argv;
    try {
      this.child = spawn(exe, args, { cwd: this.plan.cwd, env: this.plan.env, windowsHide: true });
    } catch (e) {
      this.finish('error', null, `Could not start ${exe}: ${(e as Error).message}`);
      return this;
    }
    this.child.stdout?.on('data', (d: Buffer) => this.emit('output', d.toString('utf8')));
    this.child.stderr?.on('data', (d: Buffer) => this.emit('output', d.toString('utf8')));
    this.child.on('error', (e) => this.finish('error', null, `Could not start ${exe}: ${e.message}`));
    this.child.on('close', (code) => {
      this.readPosition();
      const status: RunStatus = this.stopRequested ? 'stopped' : code === 0 ? 'passed' : 'failed';
      this.finish(status, code);
    });
    if (this.plan.positionFile) this.poll = setInterval(() => this.readPosition(), 250);
    return this;
  }

  /** Count the run as stopped when it ends: a flow was told to stop through its store. */
  stopping(): void {
    if (this.status !== 'running') return;
    this.stopRequested = true;
    this.status = 'stopping';
    this.emit('status', this.status);
  }

  /**
   * Stop gracefully (the running step ends, teardowns run, log and report are
   * written) through the stop file; kill after `graceMs`. Again while it
   * stops: the stop file now, if a flow stop came first.
   */
  stop(graceMs = 20000): void {
    if (!this.running || this.stopFileSent) return;
    this.stopping();
    this.stopFileSent = true;
    try { fs.writeFileSync(this.plan.stopFile, ''); } catch { /* the output folder is gone */ }
    this.killTimer = setTimeout(() => this.child?.kill(), graceMs);
  }

  /** End the process now. */
  kill(): void {
    if (!this.running) return;
    this.stopping();
    this.child?.kill();
  }

  get running(): boolean {
    return this.status === 'running' || this.status === 'stopping';
  }

  file(name: string): string | null {
    const p = path.join(this.plan.outDir, name);
    return fs.existsSync(p) ? p : null;
  }

  private readPosition(): void {
    if (!this.plan.positionFile) return;
    let pos: Position;
    try {
      pos = JSON.parse(fs.readFileSync(this.plan.positionFile, 'utf8'));
    } catch {
      return;   // not there yet, or being replaced
    }
    if (this.position && pos.seq === this.position.seq) return;
    this.position = pos;
    this.emit('position', pos);
  }

  private finish(status: RunStatus, code: number | null, message?: string): void {
    if (this.poll) clearInterval(this.poll);
    if (this.killTimer) clearTimeout(this.killTimer);
    this.poll = this.killTimer = null;
    if (message) this.emit('output', message + '\n');
    if (this.status !== 'running' && this.status !== 'stopping') return;
    this.status = status;
    this.exitCode = code;
    this.emit('exit', status, code);
  }
}
