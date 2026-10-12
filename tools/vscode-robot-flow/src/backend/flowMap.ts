// Lines of a flow file and the steps on them, for breakpoints and the call
// stack: a breakpoint on a line is one on the step written there, and a step
// the debugger stops at is shown on its line. Steps of a sub-flow run as
// "<sub-flow name>::<step id>" (the fork names the sub-flow's keyword after
// its "flow.name"), so a breakpoint in a sub-flow file is set by that name.
import * as fs from 'node:fs';
import * as path from 'node:path';

export const FLOW_SUFFIX = '.flow.json';

export interface Anchor { id: string; line: number; first: number; last: number }

/**
 * Where each step is written: the line of its `"id"` (1-based) and the lines
 * its object spans, as far as a breakpoint should count for it (from the line
 * that opens the object to the line before the next step's).
 */
export function anchors(text: string): Anchor[] {
  const lines = text.split(/\r?\n/);
  const out: Anchor[] = [];
  let edges = lines.length + 1;
  lines.forEach((line, i) => {
    if (/^\s*"edges"\s*:/.test(line)) edges = Math.min(edges, i + 1);
    const m = /"id"\s*:\s*"((?:[^"\\]|\\.)*)"/.exec(line);
    if (!m || i + 1 >= edges) return;
    // The object may open on a line of its own above the "id".
    let first = i + 1;
    if (!line.slice(0, m.index).includes('{')) {
      for (let j = i - 1; j >= Math.max(0, i - 3); j--) {
        if (/^\s*\{\s*$/.test(lines[j])) { first = j + 1; break; }
        if (lines[j].trim()) break;
      }
    }
    out.push({ id: JSON.parse(`"${m[1]}"`), line: i + 1, first, last: 0 });
  });
  out.forEach((a, k) => {
    const next = out[k + 1];
    a.last = next ? next.first - 1 : Math.min(edges - 1, lines.length);
  });
  return out;
}

/** The step a line belongs to, or null (a line outside the steps). */
export function stepAt(list: Anchor[], line: number): Anchor | null {
  return list.find((a) => line >= a.first && line <= a.last) || null;
}

/** The line a step is written on, or null. */
export function lineOf(list: Anchor[], id: string): number | null {
  const a = list.find((x) => x.id === id);
  return a ? a.line : null;
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    return data && typeof data === 'object' ? data : null;
  } catch {
    return null;
  }
}

/**
 * The sub-flow files a flow calls, directly or through each other: their
 * absolute path -> the name their steps run under ("flow.name").
 */
export function subflows(flowFile: string, read: (f: string) => Record<string, unknown> | null = readJson):
    Map<string, string> {
  const found = new Map<string, string>();
  const visit = (file: string) => {
    const data = read(file);
    const nodes = Array.isArray(data?.nodes) ? (data!.nodes as Array<Record<string, unknown>>) : [];
    for (const n of nodes) {
      if (n && n.kind === 'flow' && typeof n.file === 'string') {
        const sub = path.resolve(path.dirname(file), n.file);
        if (found.has(sub) || sub === path.resolve(flowFile)) continue;
        const flow = read(sub)?.flow as Record<string, unknown> | undefined;
        if (!flow || typeof flow.name !== 'string') continue;
        found.set(sub, flow.name);
        visit(sub);
      }
    }
  };
  visit(path.resolve(flowFile));
  return found;
}

/** How a step of `file` is named in a run of `target`: its id, or "<sub-flow>::<id>". */
export function runName(target: string, file: string, id: string, subs: Map<string, string>): string | null {
  if (same(target, file)) return id;
  const name = subs.get(path.resolve(file)) ?? [...subs].find(([f]) => same(f, file))?.[1];
  return name === undefined ? null : `${name}::${id}`;
}

/** The file and step of a running step's name. */
export function stepOf(target: string, node: string, subs: Map<string, string>): { file: string; id: string } | null {
  const cut = node.lastIndexOf('::');
  if (cut < 0) return { file: target, id: node };
  const name = node.slice(0, cut);
  const file = [...subs].find(([, n]) => n === name)?.[0];
  return file ? { file, id: node.slice(cut + 2) } : null;
}

export function same(a: string, b: string): boolean {
  const norm = (p: string) => path.resolve(p).replace(/\\/g, '/');
  return process.platform === 'win32' ? norm(a).toLowerCase() === norm(b).toLowerCase() : norm(a) === norm(b);
}
