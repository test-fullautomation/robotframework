// A Manager GUI test project: testproject.json at its root holds the run
// settings (interpreter, PYTHONPATH, Robot arguments, environment) and the
// run groups. A file in such a project is read and run with those settings,
// so the extension and the Manager GUI run it the same way.
import * as fs from 'node:fs';
import * as path from 'node:path';

export const PROJECT_FILE = 'testproject.json';

export interface GroupMember {
  id: string;
  /** Flow file, relative to the project root, '/'-separated. */
  target: string;
  variables: Record<string, string>;
}

export interface RunGroup {
  id: string;
  title: string;
  members: GroupMember[];
  /** Extra environment of every member; ${RUN_DIR} is the group run's folder. */
  env: Record<string, string>;
}

export interface Project {
  root: string;
  name: string;
  run: { python: string; pythonpath: string[]; args: string[]; env: Record<string, string> };
  groups: RunGroup[];
}

/** The project a file belongs to: the nearest testproject.json above it, or null. */
export function projectOf(file: string, stopAt?: string): Project | null {
  let dir = path.dirname(path.resolve(file));
  const limit = stopAt ? path.resolve(stopAt) : null;
  for (;;) {
    const candidate = path.join(dir, PROJECT_FILE);
    if (fs.existsSync(candidate)) return readProject(candidate);
    const up = path.dirname(dir);
    if (up === dir || (limit && dir.toLowerCase() === limit.toLowerCase())) return null;
    dir = up;
  }
}

export function readProject(projectFile: string): Project | null {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(fs.readFileSync(projectFile, 'utf8'));
  } catch {
    return null;
  }
  const root = path.dirname(projectFile);
  const run = (data.run || {}) as Record<string, unknown>;
  const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
  const record = (v: unknown): Record<string, string> =>
    Object.fromEntries(Object.entries((v && typeof v === 'object' ? v : {}) as Record<string, unknown>)
      .map(([k, x]) => [k, String(x)]));
  const groups = (Array.isArray(data.groups) ? data.groups : []).map((g: Record<string, unknown>) => ({
    id: String(g.id || ''),
    title: String(g.title || g.id || ''),
    members: (Array.isArray(g.members) ? g.members : []).map((m: Record<string, unknown>) => ({
      id: String(m.id || ''),
      target: String(m.target || ''),
      variables: record(m.variables),
    })).filter((m: GroupMember) => m.id && m.target),
    env: record(g.env),
  })).filter((g: RunGroup) => g.id && g.members.length);
  return {
    root,
    name: String(data.name || path.basename(root)),
    run: {
      python: String(run.python || ''),
      // relative entries are relative to the project root, as in the Manager GUI
      pythonpath: strings(run.pythonpath).filter((p) => p.trim())
        .map((p) => (path.isAbsolute(p) ? p : path.normalize(path.join(root, p)))),
      args: strings(run.args),
      env: record(run.env),
    },
    groups,
  };
}

/** A member's file on disk. */
export function memberPath(project: Project, member: GroupMember): string {
  return path.join(project.root, ...member.target.split('/'));
}
