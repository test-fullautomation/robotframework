// The Python side, shared with the Manager GUI: python/robot_grid.py,
// python/flow_inspect.py (+ flow_edit.py). Each is run with the user's
// interpreter and Robot Framework, gets the file's text on stdin -- so
// unsaved changes are shown -- and answers JSON on stdout. No VS Code API
// here, so this file is tested with plain Node (test/).
import { spawn } from 'node:child_process';
import * as path from 'node:path';

export interface RobotEnv {
  /** Interpreter that has Robot Framework (or can import it from robotSource). */
  python: string;
  /** A Robot Framework source tree (its `src`), first on PYTHONPATH; empty: the installed one. */
  robotSource: string;
  /** More PYTHONPATH entries: libraries the suites import. */
  pythonPath: string[];
  /** Working folder: the workspace folder, so relative imports resolve as in a run. */
  cwd: string;
  timeoutMs: number;
  /** Folder with robot_grid.py, flow_inspect.py, flow_edit.py. */
  helpersDir: string;
}

export interface Answer {
  ok: boolean;
  error?: string;
  [key: string]: unknown;
}

/**
 * A path as typed in a setting: trimmed, without the quotes Windows'
 * "Copy as path" puts around it ("C:\Program Files\...\python.exe").
 */
export function cleanPath(value: string): string {
  const v = String(value || '').trim();
  return v.length >= 2 && /^(["']).*\1$/.test(v) ? v.slice(1, -1).trim() : v;
}

/** PYTHONPATH for a run: the Robot source, the extra folders, then what was there. */
export function pythonPathFor(env: RobotEnv, inherited = process.env.PYTHONPATH || ''): string {
  const parts = [env.robotSource, ...env.pythonPath, ...inherited.split(path.delimiter)]
    .map((p) => (p || '').trim())
    .filter(Boolean);
  return [...new Set(parts)].join(path.delimiter);
}

/** Run one helper; its JSON answer, or { ok: false, error } saying what went wrong. */
export function runHelper(env: RobotEnv, script: string, filePath: string, stdin: string,
                          flags: string[] = [], what = 'the file'): Promise<Answer> {
  return new Promise((resolve) => {
    const args = [path.join(env.helpersDir, script), ...flags, filePath];
    let child;
    try {
      child = spawn(env.python, args, {
        cwd: env.cwd,
        env: { ...process.env, PYTHONPATH: pythonPathFor(env), PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,
      });
    } catch (e) {
      resolve({ ok: false, error: `Could not start ${env.python}: ${(e as Error).message}` });
      return;
    }
    const out: Buffer[] = [];
    const err: Buffer[] = [];
    let done = false;
    const finish = (answer: Answer) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(answer);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ ok: false, error: `Reading ${what} took longer than ${Math.round(env.timeoutMs / 1000)} s.` });
    }, env.timeoutMs);
    child.stdout.on('data', (d: Buffer) => out.push(d));
    child.stderr.on('data', (d: Buffer) => err.push(d));
    child.on('error', (e: Error) => finish({ ok: false, error: `Could not start ${env.python}: ${e.message}` }));
    child.on('close', (code: number | null) => {
      const text = Buffer.concat(out).toString('utf8');
      let data: unknown = null;
      try { data = JSON.parse(text || 'null'); } catch { data = null; }
      if (data && typeof data === 'object' && !Array.isArray(data)) {
        finish(data as Answer);
        return;
      }
      const tail = Buffer.concat(err).toString('utf8').trim().split(/\r?\n/).slice(-3).join(' / ');
      finish({ ok: false, error: `${what[0].toUpperCase()}${what.slice(1)} could not be read: ${tail || `exit ${code}`}` });
    });
    child.stdin.on('error', () => { /* the helper exited before reading: reported on close */ });
    child.stdin.end(stdin, 'utf8');
  });
}

// ---- the two views ------------------------------------------------------------

const GRID_KEYS = ['grid', 'keywords', 'imports', 'catalog', 'catalog_list', 'variables', 'features'];

/** The Grid's data for a suite or resource (robot_grid.py: Robot's parser and Libdoc). */
export async function readGrid(env: RobotEnv, filePath: string, text: string): Promise<Answer> {
  const data = await runHelper(env, 'robot_grid.py', filePath, text, [], 'the file');
  if (!data.ok) return pick(data, ['ok', 'error', 'line', 'missing']);
  return { ...pick(data, GRID_KEYS), ok: true };
}

/** The Diagram's data for a flow file (flow_inspect.py: the fork's own structure). */
export async function readFlow(env: RobotEnv, filePath: string, text: string): Promise<Answer> {
  const data = await runHelper(env, 'flow_inspect.py', filePath, text, [], 'the flow');
  if (!data.ok) return pick(data, ['ok', 'error', 'node', 'line', 'missing']);
  return { ok: true, flow: data.flow, robot: data.robot };
}

/** A change made in a view: the helper applies it to the text; the answer carries the new text. */
export async function editView(env: RobotEnv, view: 'grid' | 'diagram', filePath: string,
                               text: string, edit: unknown): Promise<Answer> {
  const payload = JSON.stringify({ text, edit });
  if (view === 'grid') {
    const data = await runHelper(env, 'robot_grid.py', filePath, payload, ['--edit'], 'the edit');
    return pick(data, ['ok', 'text', 'line', 'error', 'missing']);
  }
  const data = await runHelper(env, 'flow_inspect.py', filePath, payload, ['--edit'], 'the edit');
  return pick(data, ['ok', 'text', 'node', 'error', 'missing']);
}

function pick(data: Answer, keys: string[]): Answer {
  const out: Answer = { ok: !!data.ok };
  for (const k of keys) if (k in data) out[k] = data[k];
  return out;
}
