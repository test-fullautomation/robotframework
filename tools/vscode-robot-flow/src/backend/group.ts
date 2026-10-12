// Run groups: processes started together that wait for each other through
// the bench. The Group Diagram (flow-view/group.js) draws every member's flow
// in a column and an arrow from a step of one member to the gate of another
// that waits for it. sync_links() is robot_aio.py's, ported as is.
import * as fs from 'node:fs';
import { Answer, RobotEnv, readFlow } from './helpers';
import { Project, RunGroup, memberPath } from './project';

type Step = { id: string; kind?: string; keyword?: string; args?: unknown[]; [k: string]: unknown };
type Flow = { setup?: { steps?: Step[] }; tests?: { steps?: Step[] }[]; teardown?: { steps?: Step[] };
              variables?: Record<string, unknown> };

export interface Link {
  from: { member: string; node: string };
  to: { member: string; node: string };
  label: string;
}

/** Robot's name matching: case, spaces and underscores do not count. */
const norm = (name: string) => String(name).replace(/[\s_]+/g, '').toLowerCase();

function resolve(value: unknown, values: Record<string, string>): string {
  const table = new Map(Object.entries(values).map(([k, v]) => [norm(k), v]));
  return String(value).replace(/\$\{([^}]+)\}/g, (m, name) => (table.has(norm(name)) ? String(table.get(norm(name))) : m));
}

function sameValue(a: string, b: string): boolean {
  const x = Number(a), y = Number(b);
  if (a.trim() !== '' && b.trim() !== '' && !Number.isNaN(x) && !Number.isNaN(y)) return x === y;
  return a.trim() === b.trim();
}

function* walk(steps: Step[] | undefined): Generator<Step> {
  for (const step of steps || []) {
    yield step;
    for (const key of ['body', 'recovery', 'yes', 'no']) yield* walk(step[key] as Step[] | undefined);
  }
}

function* flowSteps(flow: Flow): Generator<Step> {
  for (const phase of [flow.setup, ...(flow.tests || []), flow.teardown]) {
    if (phase) yield* walk(phase.steps);
  }
}

/** Where members meet: a Set Signal of one member that a gate of another waits for. */
export function syncLinks(members: { id: string; flow: Flow; values: Record<string, string> }[]): Link[] {
  const writes: [string, string, string, string][] = [];
  const waits: [string, string, string, string | null][] = [];
  for (const m of members) {
    for (const step of flowSteps(m.flow)) {
      const args = (step.args || []).map((a) => resolve(a, m.values));
      if (step.kind === 'keyword' && norm(step.keyword || '') === 'setsignal' && args.length >= 2) {
        writes.push([m.id, step.id, args[0], args[1]]);
      } else if (step.kind === 'gate' && args.length) {
        waits.push([m.id, step.id, args[0], args.length >= 3 && args[1] === '==' ? args[2] : null]);
      }
    }
  }
  const links: Link[] = [];
  for (const [wMember, wNode, name, value] of writes) {
    for (const [gMember, gNode, gName, gValue] of waits) {
      if (gMember === wMember || gName !== name) continue;
      if (gValue !== null && !sameValue(gValue, value)) continue;
      links.push({ from: { member: wMember, node: wNode }, to: { member: gMember, node: gNode }, label: `${name} = ${value}` });
    }
  }
  return links;
}

/**
 * The Group Diagram's data: every member's flow (read by the fork, the text of
 * an open editor if `textOf` has it) and the links. Like robot_aio.inspect_group.
 */
export async function inspectGroup(env: RobotEnv, project: Project, group: RunGroup,
                                   textOf: (file: string) => string | undefined = () => undefined): Promise<Answer> {
  const flows = new Map<string, Answer>();
  const members = [];
  for (const m of group.members) {
    const file = memberPath(project, m);
    if (!flows.has(file)) {
      let text = textOf(file);
      if (text === undefined) {
        try { text = fs.readFileSync(file, 'utf8').replace(/^﻿/, ''); } catch (e) {
          return { ok: false, member: m.id, error: `${m.id} (${m.target}): ${(e as Error).message}` };
        }
      }
      flows.set(file, await readFlow({ ...env, cwd: project.root }, file, text));
    }
    const data = flows.get(file)!;
    if (!data.ok) return { ok: false, member: m.id, node: data.node, line: data.line, error: `${m.id} (${m.target}): ${data.error}` };
    const flow = data.flow as Flow;
    const values: Record<string, string> = Object.fromEntries(
      Object.entries(flow.variables || {}).map(([k, v]) => [k, String(v)]));
    Object.assign(values, m.variables);
    members.push({ id: m.id, target: m.target, variables: m.variables, values, flow });
  }
  const links = syncLinks(members);
  return { ok: true, members: members.map(({ values, ...m }) => m), links };
}
