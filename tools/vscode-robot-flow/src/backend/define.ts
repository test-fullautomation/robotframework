// Go to Definition: what the cursor is on, and where Robot finds it.
//
// The text side is here and plain (testable without VS Code): which cell of
// a Robot line, or which string of a flow file, the cursor is in. Where a
// keyword is defined is the helper's (robot_grid.py --define): it resolves
// the name exactly as the Grid and the run do -- the file's own keywords,
// its resources and theirs, its libraries through Libdoc, BuiltIn.
import * as fs from 'node:fs';
import * as path from 'node:path';
import { RobotEnv, runHelper } from './helpers';

export interface Span { text: string; start: number; end: number }

export interface Definition { found: boolean; source?: string; line?: number | null; name?: string; owner?: string }

// Cells: two or more spaces, a tab, or " | " (pipe format) separate them.
const SEPARATOR = /( {2,}|\t+|\s+\|\s+|^\|\s+|\s+\|$)/g;
const VARIABLE_ONLY = /^[$@&%]\{[^}]*\}\s?=?$/;

/** The cell of a Robot Framework line the cursor (0-based column) is in. */
export function robotCellAt(line: string, column: number): Span | null {
  const cells: Span[] = [];
  let last = 0;
  for (const m of line.matchAll(SEPARATOR)) {
    if (m.index! > last) cells.push({ text: line.slice(last, m.index), start: last, end: m.index! });
    last = m.index! + m[0].length;
  }
  if (last < line.length) cells.push({ text: line.slice(last), start: last, end: line.length });
  const cell = cells.find((c) => column >= c.start && column <= c.end);
  if (!cell) return null;
  // A comment from here on, the continuation marker or a bare variable is not a name.
  const text = cell.text.trim();
  if (!text || text.startsWith('#') || text === '...' || VARIABLE_ONLY.test(text)) return null;
  if (/^\*/.test(line.trimStart()) || /^\[[^\]]+\]$/.test(text)) return null;   // section header, [Setting]
  return { text, start: cell.start, end: cell.end };
}

/**
 * The JSON string the cursor is in, and the key it is the value of (or null
 * for an array item): `"keyword": "Set Signal"`, `"file": "sub/x.flow.json"`.
 */
export function flowStringAt(line: string, column: number): (Span & { key: string | null }) | null {
  const re = /"((?:[^"\\]|\\.)*)"/g;
  let m: RegExpExecArray | null;
  const strings: Array<Span & { key: string | null }> = [];
  while ((m = re.exec(line))) {
    const after = line.slice(m.index + m[0].length);
    if (/^\s*:/.test(after)) continue;   // a key, not a value
    const before = line.slice(0, m.index);
    const key = /"([^"]+)"\s*:\s*$/.exec(before);
    strings.push({ text: JSON.parse(m[0]), start: m.index, end: m.index + m[0].length, key: key ? key[1] : null });
  }
  return strings.find((s) => column >= s.start && column <= s.end) || null;
}

/** A path written in a file (relative to it), when that file exists. */
export function existingFile(fromFile: string, written: string): string | null {
  const value = written.replace(/\$\{CURDIR\}/gi, path.dirname(fromFile)).trim();
  if (!value || /[$@&%]\{/.test(value) || !/[\\/.]/.test(value)) return null;
  const full = path.resolve(path.dirname(fromFile), value);   // absolute ones are only normalized
  try {
    return fs.statSync(full).isFile() ? full : null;
  } catch {
    return null;
  }
}

/** Where the keyword (or import) ``name`` used in ``filePath`` is defined. */
export async function defineName(env: RobotEnv, filePath: string, text: string, name: string): Promise<Definition> {
  const answer = await runHelper(env, 'robot_grid.py', filePath, JSON.stringify({ text, name }),
                                 ['--define'], 'the definition');
  if (!answer.ok) return { found: false };
  return answer as unknown as Definition;
}
