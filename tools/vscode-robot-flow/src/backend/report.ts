// The flow report of a finished run, written on demand: the fork's
// `python -m robot.flow report <output.xml> --output flow.html --log log.html`
// (what `robot --flowreport` writes with the run when robotFlow.flowReport
// is on). No VS Code API here; test/ runs it with plain Node.
import { spawn } from 'node:child_process';
import * as path from 'node:path';
import { Answer, RobotEnv, pythonPathFor } from './helpers';

export const FLOW_REPORT_FILE = 'flow.html';

/** Write `<dir of output>/flow.html` from an output.xml; the answer carries `file`. */
export function writeFlowReport(env: RobotEnv, output: string, timeoutMs = 120000): Promise<Answer & { file?: string }> {
  const dir = path.dirname(output);
  const file = path.join(dir, FLOW_REPORT_FILE);
  return new Promise((resolve) => {
    const args = ['-m', 'robot.flow', 'report', output, '--output', file, '--log', 'log.html'];
    let child;
    try {
      const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: pythonPathFor(env), PYTHONUTF8: '1' };
      delete childEnv.PYTHONIOENCODING;
      child = spawn(env.python, args, { cwd: dir, env: childEnv, windowsHide: true });
    } catch (e) {
      resolve({ ok: false, error: `Could not start ${env.python}: ${(e as Error).message}` });
      return;
    }
    const err: Buffer[] = [];
    let done = false;
    const finish = (a: Answer & { file?: string }) => { if (!done) { done = true; clearTimeout(timer); resolve(a); } };
    const timer = setTimeout(() => { child.kill(); finish({ ok: false, error: `The flow report took longer than ${Math.round(timeoutMs / 1000)} s.` }); }, timeoutMs);
    child.stderr.on('data', (d: Buffer) => err.push(d));
    child.stdout.on('data', () => { /* "Flow report: ... (1 flow)" */ });
    child.on('error', (e: Error) => finish({ ok: false, error: `Could not start ${env.python}: ${e.message}` }));
    child.on('close', (code: number | null) => {
      if (code === 0) { finish({ ok: true, file }); return; }
      const text = Buffer.concat(err).toString('utf8');
      const tail = text.trim().split(/\r?\n/).slice(-2).join(' / ');
      finish({ ok: false, error: tail || `exit ${code}`, missing: /No module named/.test(text) });
    });
  });
}
