// Runs started from the views: one suite or flow file, or a run group (its
// members started together, sharing one signal store). Output goes to an
// output channel per run; the views follow the position and the status.
// A flow run also takes the fork's flow commands through its store: pause,
// resume (a member of a group alone too), stop at the next step with a
// checkpoint, and a new run that continues from that checkpoint.
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { ControlCommand, ControlState, activeProcesses, canPause, findCheckpoint, readControlState, sendControl } from './backend/control';
import { RobotEnv } from './backend/helpers';
import { Project, RunGroup, memberPath } from './backend/project';
import { Position, RobotRun, RunStatus, planRun } from './backend/run';
import { projectFor, resultsRoot, robotEnvFor } from './settings';

/** How long a flow may take to reach its next step after Stop before the stop file follows (as in the GUI). */
const FLOW_STOP_GRACE_MS = 60000;

export interface RunState {
  key: string;
  title: string;
  status: RunStatus;
  outDir: string;
  started: number;
  ended: number | null;
  /** Where each process is: 'main' for a file run, member ids for a group. */
  positions: Record<string, Position | null>;
  /** The run's processes: one for a file, one per member for a group. */
  members: string[];
  /** A debugged run stopped here: the flow step (or null for a keyword elsewhere), and why. */
  paused: { node: string | null; reason: string } | null;
  /** Started by the debugger. */
  debug: boolean;
  /** The suite or flow file of a file run; null for a group. */
  target: string | null;
  /** Takes flow commands (pause, resume, stop with a checkpoint): flow files only. */
  pausable: boolean;
  /** What the flows say about themselves while it runs (their store). */
  flow: ControlState | null;
  /** Started in step mode. */
  step: boolean;
  /** After a flow run that did not pass: the checkpoint a new run can continue from. */
  checkpoint: string | null;
}

/** What a debugger adds to a run: Robot options (its listener) and environment. */
export interface RunExtras {
  robotArgs?: string[];
  env?: Record<string, string>;
  variables?: Record<string, string>;
  args?: string[];
  debug?: boolean;
  /** Step mode (flow files). */
  step?: boolean;
}

export const fileKey = (file: string) => 'file:' + path.resolve(file).toLowerCase();
export const groupKey = (project: Project, group: RunGroup) => `group:${path.resolve(project.root).toLowerCase()}#${group.id}`;

function stamp(name: string): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  const safe = name.replace(/\.flow\.json$|\.robot$/i, '').replace(/[^A-Za-z0-9_.-]+/g, '_').slice(0, 40);
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${safe}`;
}

interface Active {
  state: RunState;
  processes: Map<string, RobotRun>;
  output: vscode.OutputChannel;
  /** The interpreter and Robot that send flow commands. */
  env: RobotEnv;
  /** The flows' store (their control channel); null for a suite. */
  store: string | null;
  /** The variables it was started with: a continued run starts with them too. */
  variables: Record<string, string>;
  watch?: NodeJS.Timeout;
  flowStop?: NodeJS.Timeout;
}

export class RunManager implements vscode.Disposable {
  private readonly runs = new Map<string, Active>();
  private readonly changed = new vscode.EventEmitter<string>();
  /** A run's state changed (status, position): the key. */
  readonly onDidChange = this.changed.event;

  constructor(private readonly extension: vscode.ExtensionContext) {}

  dispose(): void {
    for (const a of this.runs.values()) {
      this.clearTimers(a);
      a.processes.forEach((p) => p.stop(3000));
      a.output.dispose();
    }
    this.changed.dispose();
  }

  state(key: string): RunState | null {
    return this.runs.get(key)?.state ?? null;
  }

  isRunning(key: string): boolean {
    const s = this.state(key)?.status;
    return s === 'running' || s === 'stopping';
  }

  /** Run a suite or flow file (saved first); `extras` from the debugger. */
  async runFile(uri: vscode.Uri, extras: RunExtras = {}): Promise<RunState | null> {
    const key = fileKey(uri.fsPath);
    if (this.isRunning(key)) {
      void vscode.window.showInformationMessage(`${path.basename(uri.fsPath)} is running already.`);
      return this.state(key);
    }
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.fsPath === uri.fsPath);
    if (doc?.isDirty) await doc.save();
    const env = await robotEnvFor(uri, this.extension.extensionPath);
    const project = projectFor(uri);
    const outDir = path.join(resultsRoot(uri), stamp(path.basename(uri.fsPath)));
    let plan;
    try {
      plan = planRun({
        env, target: uri.fsPath, outDir, variables: extras.variables, step: extras.step,
        args: [...(extras.robotArgs || []), ...(project?.run.args || []), ...this.runArgs(uri), ...(extras.args || [])],
        extraEnv: { ...(project?.run.env || {}), ...(extras.env || {}) },
      });
    } catch (e) {
      void vscode.window.showErrorMessage((e as Error).message);
      return null;
    }
    return this.start(key, path.basename(uri.fsPath), outDir, new Map([['main', new RobotRun(plan)]]), {
      env, debug: !!extras.debug, step: !!extras.step, target: uri.fsPath,
      variables: extras.variables || {}, pausable: canPause(uri.fsPath),
    });
  }

  /** A new run of a stopped flow that continues from its checkpoint (finished test phases are skipped). */
  async continueRun(key: string): Promise<RunState | null> {
    const a = this.runs.get(key);
    if (!a || !a.state.target) return null;
    if (this.isRunning(key)) {
      void vscode.window.showInformationMessage(`${a.state.title} is running already.`);
      return a.state;
    }
    const checkpoint = a.state.checkpoint && fs.existsSync(a.state.checkpoint) ? a.state.checkpoint : findCheckpoint(a.state.outDir);
    if (!checkpoint) {
      void vscode.window.showInformationMessage(`${a.state.title}: the last run left nothing to continue from.`);
      return null;
    }
    return this.runFile(vscode.Uri.file(a.state.target), { variables: { ...a.variables, FLOW_CHECKPOINT: checkpoint } });
  }

  /** Run every member of a group together; they share one signal store in the group's folder. */
  async runGroup(project: Project, group: RunGroup): Promise<RunState | null> {
    const key = groupKey(project, group);
    if (this.isRunning(key)) {
      void vscode.window.showInformationMessage(`${group.title} is running already.`);
      return this.state(key);
    }
    for (const m of group.members) {
      const doc = vscode.workspace.textDocuments.find((d) => d.uri.fsPath === memberPath(project, m));
      if (doc?.isDirty) await doc.save();
    }
    const first = vscode.Uri.file(memberPath(project, group.members[0]));
    const runDir = path.join(resultsRoot(first), stamp('group-' + group.id));
    const processes = new Map<string, RobotRun>();
    for (const m of group.members) {
      const uri = vscode.Uri.file(memberPath(project, m));
      const env = await robotEnvFor(uri, this.extension.extensionPath);
      processes.set(m.id, new RobotRun(planRun({
        env, target: uri.fsPath, outDir: path.join(runDir, m.id), runDir, variables: m.variables, rig: m.id,
        args: [...project.run.args, ...this.runArgs(uri)],
        extraEnv: { ...project.run.env, ...group.env },
      })));
    }
    return this.start(key, group.title, runDir, processes, {
      env: await robotEnvFor(first, this.extension.extensionPath), debug: false, step: false, target: null,
      variables: {}, pausable: group.members.every((m) => canPause(memberPath(project, m))),
    });
  }

  /** Pause or resume a running flow, or one member of a group run. */
  async control(key: string, command: Exclude<ControlCommand, 'stop'>, member = ''): Promise<boolean> {
    const a = this.runs.get(key);
    if (!a || a.state.status !== 'running') return false;
    if (!a.state.pausable || !a.store) {
      void vscode.window.showInformationMessage('Only a flow run can be paused: a suite has no pause points.');
      return false;
    }
    if (member && !a.processes.has(member)) return false;
    const res = await sendControl(a.env, a.store, command, member);
    if (!res.ok) {
      void vscode.window.showWarningMessage(`${command === 'pause' ? 'Pause' : 'Resume'} was not sent: ${res.error}` +
        (res.missing ? ' (it needs the RobotFramework AIO fork: robotFlow.robotSource)' : ''));
      return false;
    }
    a.output.appendLine(`[${command}${member ? ' ' + member : ''}]`);
    this.readFlow(key);
    return true;
  }

  /** One process of a run ('main' for a file run). */
  process(key: string, id = 'main'): RobotRun | null {
    return this.runs.get(key)?.processes.get(id) ?? null;
  }

  /** The debugger stopped the run (node, reason) or let it go on (null). */
  setPaused(key: string, paused: RunState['paused']): void {
    const a = this.runs.get(key);
    if (!a) return;
    a.state.paused = paused;
    this.changed.fire(key);
  }

  /**
   * Stop a run. A flow (not debugged) first gets the fork's own stop: it leaves at its next
   * step, runs its teardown and writes a checkpoint to continue from; if it
   * has not ended a minute later the stop file follows. A suite, and a second
   * Stop, use the stop file (the running keyword ends, teardowns run); a third
   * ends the processes at once.
   */
  async stop(key: string): Promise<void> {
    const a = this.runs.get(key);
    if (!a || !this.isRunning(key)) return;
    const all = [...a.processes.values()].filter((p) => p.running);
    if (a.state.status === 'stopping') {
      if (all.every((p) => p.stopFileSent)) all.forEach((p) => p.kill());
      else all.forEach((p) => p.stop());
      return;
    }
    // Not under the debugger: a run it holds at a breakpoint would never reach the next step.
    if (a.state.pausable && !a.state.debug && a.store && activeProcesses(readControlState(a.store)).length) {
      const res = await sendControl(a.env, a.store, 'stop');
      if (res.ok && this.isRunning(key)) {
        a.output.appendLine('[stop: at the next step, with a checkpoint to continue from]');
        all.forEach((p) => p.stopping());
        a.flowStop = setTimeout(() => a.processes.forEach((p) => p.stop()), FLOW_STOP_GRACE_MS);
        return;
      }
      if (!res.ok) a.output.appendLine(`[the flow stop was not sent (${res.error}); stopping through the stop file]`);
    }
    all.forEach((p) => p.stop());
  }

  /** A file of a finished (or running) run: log.html, report.html, output.xml; a group's first member's. */
  artifact(key: string, name: string): string | null {
    const a = this.runs.get(key);
    if (!a) return null;
    for (const p of a.processes.values()) {
      const f = p.file(name);
      if (f) return f;
    }
    return null;
  }

  private runArgs(uri: vscode.Uri): string[] {
    return vscode.workspace.getConfiguration('robotFlow', uri).get<string[]>('runArgs', []);
  }

  private start(key: string, title: string, outDir: string, processes: Map<string, RobotRun>,
                opts: { env: RobotEnv; debug: boolean; step: boolean; target: string | null;
                        variables: Record<string, string>; pausable: boolean }): RunState {
    const old = this.runs.get(key);
    if (old) this.clearTimers(old);
    const output = old?.output ?? vscode.window.createOutputChannel(`Robot: ${title}`);
    output.clear();
    output.show(true);
    output.appendLine(`Output: ${outDir}`);
    const state: RunState = {
      key, title, status: 'running', outDir, started: Date.now(), ended: null,
      positions: Object.fromEntries([...processes.keys()].map((id) => [id, null])), members: [...processes.keys()],
      paused: null, debug: opts.debug, target: opts.target, pausable: false, flow: null, step: opts.step, checkpoint: null,
    };
    const store = [...processes.values()][0]?.plan.signalsFile ?? null;
    state.pausable = opts.pausable && !!store;
    const active: Active = { state, processes, output, env: opts.env, store, variables: opts.variables };
    this.runs.set(key, active);
    if (state.pausable) active.watch = setInterval(() => this.readFlow(key), 1000);
    const group = processes.size > 1 || !processes.has('main');
    for (const [id, run] of processes) {
      output.appendLine(`> ${run.plan.argv.join(' ')}`);
      run.on('output', (text: string) => {
        output.append(group ? text.replace(/^(?=.)/gm, `[${id}] `) : text);
      });
      run.on('position', (pos: Position) => {
        state.positions[id] = pos;
        this.changed.fire(key);
      });
      run.on('status', () => {
        state.status = 'stopping';
        this.changed.fire(key);
      });
      run.on('exit', () => this.finished(key));
      run.start();
    }
    this.changed.fire(key);
    return state;
  }

  /** What the flows say now, from their store; the views hear of a change. */
  private readFlow(key: string): void {
    const a = this.runs.get(key);
    if (!a || !a.store || !this.isRunning(key)) return;
    const flow = readControlState(a.store);
    const shape = (f: ControlState | null) => JSON.stringify([
      Object.entries(f?.processes || {}).map(([n, p]) => [n, p.state, p.phase, p.loop, p.iteration]), f?.command?.value]);
    if (shape(flow) === shape(a.state.flow)) return;
    a.state.flow = flow;
    this.changed.fire(key);
  }

  private clearTimers(a: Active): void {
    clearInterval(a.watch);
    clearTimeout(a.flowStop);
    a.watch = a.flowStop = undefined;
  }

  private finished(key: string): void {
    const a = this.runs.get(key);
    if (!a) return;
    const all = [...a.processes.values()];
    if (all.some((p) => p.running)) return;
    this.clearTimers(a);
    a.state.flow = null;
    // A file run of a flow that did not pass may continue from where it stopped.
    a.state.checkpoint = a.state.pausable && a.state.target && !all.every((p) => p.status === 'passed')
      ? findCheckpoint(a.state.outDir) : null;
    const statuses = all.map((p) => p.status);
    a.state.status = statuses.includes('error') ? 'error'
      : statuses.includes('stopped') ? 'stopped'
      : statuses.every((s) => s === 'passed') ? 'passed' : 'failed';
    a.state.ended = Date.now();
    a.state.paused = null;
    const codes = all.map((p) => p.exitCode).join(', ');
    a.output.appendLine(`\n${a.state.title}: ${a.state.status} (exit ${codes})`);
    this.changed.fire(key);
    if (a.state.debug) return;   // the debug session says how it ended
    const log = this.artifact(key, 'log.html');
    const message = `${a.state.title}: ${a.state.status}` + (a.state.checkpoint ? ' · it can continue from where it stopped' : '');
    const show = a.state.status === 'passed' ? vscode.window.showInformationMessage
                                            : vscode.window.showWarningMessage;
    void show(message, ...(a.state.checkpoint ? ['Continue'] : []), ...(log ? ['Open Log', 'Open Report'] : []), 'Show Output').then((pick) => {
      if (pick === 'Show Output') a.output.show();
      else if (pick === 'Continue') void this.continueRun(key);
      else if (pick) void this.open(key, pick === 'Open Log' ? 'log.html' : 'report.html');
    });
  }

  /** Open log.html / report.html in the browser. */
  async open(key: string, name: string): Promise<void> {
    const f = this.artifact(key, name);
    if (!f || !fs.existsSync(f)) {
      void vscode.window.showInformationMessage(`No ${name} for this run (yet).`);
      return;
    }
    await vscode.env.openExternal(vscode.Uri.file(f));
  }

  output(key: string): void {
    this.runs.get(key)?.output.show();
  }
}
