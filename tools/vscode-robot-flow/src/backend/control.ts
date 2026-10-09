// Pause, resume and stop a running flow, and continue a stopped one: the
// fork's flow control, as the Manager GUI uses it (robot_aio.py). A flow
// run's signal store (ROBOT_FLOW_SIGNALS) is also its control channel:
// `python -m robot.flow control <store> pause|resume|stop [--rig NAME]`
// writes the command, and every flow process publishes
// `flow.state.<rig or pid>` there. A group's members share one store and run
// as rigs named after the members (ROBOT_FLOW_RIG). Stop leaves at the next
// step boundary and writes `<outdir>/<flow>.checkpoint.json`; a new run with
// `--variable FLOW_CHECKPOINT:<that file>` continues from it.
// No VS Code API here; test/ runs it with plain Node.
import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { Answer, RobotEnv, pythonPathFor } from './helpers';
import { FLOW_SUFFIX } from './run';

export type ControlCommand = 'pause' | 'resume' | 'stop';

/** What one flow process says about itself. */
export interface FlowProcess {
  /** running | paused | stopped | finished */
  state: string | null;
  phase: string | null;
  loop: string | null;
  iteration: number | null;
  /** Seconds since it last said so. */
  ageS: number;
}

export interface ControlState {
  /** By rig name (a group member) or process id. */
  processes: Record<string, FlowProcess>;
  /** The newest command in the store, and for whom ('' = every flow). */
  command: { value: string; ageS: number; member: string } | null;
}

/** Whether a run of `target` takes flow commands: a flow file (a group's members are flow files too). */
export function canPause(target: string): boolean {
  return target.toLowerCase().endsWith(FLOW_SUFFIX);
}

/** Send pause / resume / stop to every flow of the store, or to one rig. */
export function sendControl(env: RobotEnv, store: string, command: ControlCommand, rig = ''): Promise<Answer> {
  return new Promise((resolve) => {
    const args = ['-m', 'robot.flow', 'control', store, command, ...(rig ? ['--rig', rig] : [])];
    let child;
    try {
      const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: pythonPathFor(env), PYTHONUTF8: '1' };
      delete childEnv.PYTHONIOENCODING;
      child = spawn(env.python, args, { cwd: env.cwd, env: childEnv, windowsHide: true });
    } catch (e) {
      resolve({ ok: false, error: `Could not start ${env.python}: ${(e as Error).message}` });
      return;
    }
    const err: Buffer[] = [];
    let done = false;
    const finish = (a: Answer) => { if (!done) { done = true; clearTimeout(timer); resolve(a); } };
    const timer = setTimeout(() => { child.kill(); finish({ ok: false, error: `${command} took longer than 30 s.` }); }, 30000);
    child.stderr.on('data', (d: Buffer) => err.push(d));
    child.stdout.on('data', () => { /* "pause -> all flows of ..." */ });
    child.on('error', (e: Error) => finish({ ok: false, error: `Could not start ${env.python}: ${e.message}` }));
    child.on('close', (code: number | null) => {
      if (code === 0) { finish({ ok: true }); return; }
      const text = Buffer.concat(err).toString('utf8');
      const tail = text.trim().split(/\r?\n/).slice(-2).join(' / ');
      finish({ ok: false, error: tail || `exit ${code}`, missing: /No module named/.test(text) });
    });
  });
}

/** The flows' own state, read from the store; null when no flow has used it yet. */
export function readControlState(store: string, now = Date.now()): ControlState | null {
  let entries: unknown;
  try {
    entries = JSON.parse(fs.readFileSync(store, 'utf8'));
  } catch {
    return null;   // not there yet, or being replaced
  }
  if (!entries || typeof entries !== 'object' || Array.isArray(entries)) return null;
  const processes: Record<string, FlowProcess> = {};
  let command: ControlState['command'] = null;
  for (const [key, raw] of Object.entries(entries as Record<string, unknown>)) {
    if (!raw || typeof raw !== 'object') continue;
    const entry = raw as { value?: unknown; time?: number };
    const ageS = Math.round(Math.max(0, now / 1000 - Number(entry.time ?? now / 1000)) * 10) / 10;
    if (key.startsWith('flow.state.')) {
      const v = (entry.value && typeof entry.value === 'object' ? entry.value : { state: entry.value }) as Record<string, unknown>;
      processes[key.slice('flow.state.'.length)] = {
        state: v.state == null ? null : String(v.state),
        phase: v.phase == null ? null : String(v.phase),
        loop: v.loop == null ? null : String(v.loop),
        iteration: typeof v.iteration === 'number' ? v.iteration : null,
        ageS,
      };
    } else if (key === 'flow.control' || key.startsWith('flow.control.')) {
      if (!command || ageS < command.ageS) {
        command = { value: String(entry.value), ageS, member: key === 'flow.control' ? '' : key.slice('flow.control.'.length) };
      }
    }
  }
  return Object.keys(processes).length || command ? { processes, command } : null;
}

/** The processes still at work: they would take a stop. */
export function activeProcesses(state: ControlState | null): string[] {
  return Object.entries(state?.processes || {})
    .filter(([, p]) => p.state === 'running' || p.state === 'paused')
    .map(([name]) => name);
}

/** One line for the toolbar: "paused · phase Cycle, loop loop iteration 3", "1 of 2 paused". */
export function describeFlow(state: ControlState | null, member = ''): { text: string; paused: number; total: number } | null {
  const procs = state?.processes || {};
  const names = Object.keys(procs).filter((n) => !member || n === member);
  if (!names.length) return null;
  const paused = names.filter((n) => procs[n].state === 'paused').length;
  const p = procs[names[0]];
  const where: string[] = [];
  if (p.phase) where.push(`phase ${p.phase}`);
  if (p.loop) where.push(`loop ${p.loop} iteration ${(p.iteration || 0) + 1}`);
  const head = paused ? (paused === names.length ? 'paused' : `${paused} of ${names.length} paused`) : (p.state || 'running');
  return { text: head + (where.length && names.length === 1 ? ' · ' + where.join(', ') : ''), paused, total: names.length };
}

/** The checkpoint a stopped flow run left in its output folder, or null. */
export function findCheckpoint(outDir: string): string | null {
  try {
    const found = fs.readdirSync(outDir).filter((f) => f.endsWith('.checkpoint.json')).sort();
    return found.length ? path.join(outDir, found[0]) : null;
  } catch {
    return null;
  }
}
