// Which Robot Framework the views talk to, and what it can do.
//
// Today: Robot Framework 6.1 / RobotFramework AIO. The Diagram needs the
// fork's `robot.flow` (flow files); the Grid works with any Robot that has
// the parsing API and Libdoc. Tomorrow: Robot Framework 7.5, once the AIO
// features are ported there. The views do not hard-code a version: they ask
// what the configured Robot has, and say what is missing.
import { spawn } from 'node:child_process';
import { RobotEnv, pythonPathFor } from './helpers';

export interface RobotInfo {
  ok: boolean;
  error?: string;
  /** e.g. "6.1", "7.5". */
  version?: string;
  major?: number;
  /** Where `robot` was imported from. */
  location?: string;
  /** RobotFramework AIO flow files (robot.flow): needed by the Diagram. */
  flow?: boolean;
  /** RobotFramework AIO THREAD blocks: offered by the Grid when present. */
  thread?: boolean;
  python?: string;
}

const PROBE = [
  'import json, sys, importlib.util as u',
  'try:',
  '    import robot, robot.version as v',
  '    t = False',
  '    try:  # as robot_grid.has_threads()',
  '        from robot.parsing.model import blocks as b, statements as s',
  '        t = hasattr(b, "Thread") and hasattr(s, "ThreadHeader")',
  '    except Exception:',
  '        pass',
  '    print(json.dumps({"ok": True, "version": v.VERSION, "location": robot.__file__,',
  '                      "flow": u.find_spec("robot.flow") is not None, "thread": t,',
  '                      "python": sys.version.split()[0]}))',
  'except Exception as e:',
  '    print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}))',
].join('\n');

export function probeRobot(env: RobotEnv): Promise<RobotInfo> {
  return new Promise((resolve) => {
    let out = '';
    const child = spawn(env.python, ['-c', PROBE], {
      cwd: env.cwd,
      env: { ...process.env, PYTHONPATH: pythonPathFor(env) },
      windowsHide: true,
    });
    const timer = setTimeout(() => { child.kill(); resolve({ ok: false, error: 'Python did not answer.' }); }, 20000);
    child.stdout.on('data', (d: Buffer) => { out += d.toString('utf8'); });
    child.on('error', (e) => { clearTimeout(timer); resolve({ ok: false, error: `Could not start ${env.python}: ${e.message}` }); });
    child.on('close', () => {
      clearTimeout(timer);
      try {
        const info = JSON.parse(out) as RobotInfo;
        if (info.version) info.major = parseInt(info.version, 10);
        resolve(info);
      } catch {
        resolve({ ok: false, error: `${env.python} could not report its Robot Framework.` });
      }
    });
  });
}

/** Why the Diagram cannot be drawn with this Robot, or null when it can. */
export function diagramUnavailable(info: RobotInfo): string | null {
  if (!info.ok) return info.error || 'Robot Framework was not found.';
  if (info.flow) return null;
  const base = `Robot Framework ${info.version} (${info.location}) has no flow support (robot.flow).`;
  return (info.major && info.major >= 7)
    ? `${base} Flow files come from RobotFramework AIO and are not in the ${info.version} port yet.`
    : `${base} Set "robotFlow.robotSource" to the RobotFramework AIO fork's src folder.`;
}
