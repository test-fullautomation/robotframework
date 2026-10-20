// Completion in the text of a flow file: what the cursor is in, and what
// fits there. Plain text logic, no VS Code API, tested with Node (test/).
//
// The JSON schema (registered for *.flow.json) already completes the
// structure: keys, kinds, enum values. This adds what a schema cannot know:
// the node ids of the file in edges, the edge labels a node of that kind may
// carry, the keywords of the file's imports (from robot_grid.py --catalog),
// the variables of the file and its resources, the sub-flow files.
import { RobotEnv, runHelper } from './helpers';

export interface KeywordInfo {
  name: string;
  owner: string;
  owner_type: string;
  args: Array<{ name: string; kind: string; required: boolean; default: string | null; type: string | null }>;
  shortdoc: string;
  source?: string | null;
}

export interface VariableInfo { name: string; value: string; source: string }

export interface FlowCatalog { ok: boolean; error?: string; keywords: KeywordInfo[]; variables: VariableInfo[] }

export interface FlowNode { id: string; kind: string; label: string }

/** Where the cursor is: inside the value string of `key`, with `prefix` typed so far. */
export interface Place {
  key: string;
  /** The text of the string before the cursor. */
  prefix: string;
  /** Offsets of the string's content (between the quotes). */
  start: number;
  end: number;
  /** The object the string belongs to: an edge's `from`, a node's kind. */
  from: string | null;
  kind: string | null;
  /** The string is an item of an array under `key` (args, assign). */
  inArray: boolean;
}

export interface Item {
  label: string;
  kind: 'node' | 'label' | 'keyword' | 'variable' | 'file';
  detail?: string;
  doc?: string;
  /** What to insert in place of the string's content; the label by default. */
  insert?: string;
  sort?: string;
}

// The labels an edge leaving a node of each kind may carry ('' is "then").
const LABELS_BY_KIND: Record<string, string[]> = {
  start: [''], phase: [''],
  keyword: ['', 'next', 'continue', 'abort', 'join'],
  gate: ['', 'next', 'continue', 'abort', 'join'],
  sleep: ['', 'next', 'continue', 'abort', 'join'],
  flow: ['', 'next', 'continue', 'abort', 'join'],
  decision: ['yes', 'no'],
  loop: ['body', 'done', 'next', 'continue', 'abort', 'on_failure'],
  try: ['body', 'done', 'next', 'continue', 'abort', 'on_failure'],
  parallel: ['branch', 'done', 'next', 'continue', 'abort', 'on_failure'],
};
const LABEL_DOC: Record<string, string> = {
  '': 'then: the plain next step',
  next: 'back to the enclosing loop or try, from the last node of its body',
  continue: 'back to the loop or try, from the last node of its recovery',
  abort: 'from the last node of a recovery: re-raise the failure after it',
  join: 'back to the parallel node, from the last node of a branch',
  yes: 'the branch taken when the condition holds',
  no: 'the branch taken when it does not',
  body: 'into the body of a loop or try',
  done: 'where the plan goes on after a loop, try or parallel region',
  on_failure: 'into the recovery region',
  branch: 'into a branch of a parallel region (one edge per branch)',
};
const ALL_LABELS = ['', 'body', 'next', 'on_failure', 'continue', 'abort', 'done', 'yes', 'no', 'branch', 'join'];

/** The string literal of `text` that contains `offset`, and the key it is the value (or array item) of. */
export function placeAt(text: string, offset: number): Place | null {
  // Scan the text as JSON tokens up to the cursor, remembering the last key of each nesting level.
  const keys: Array<string | null> = [];    // key per depth, for the containing object or array
  const kinds: string[] = [];               // '{' or '['
  let i = 0;
  let lastString: { start: number; end: number; value: string } | null = null;
  let pendingKey: string | null = null;     // a string followed by ':' is a key
  while (i < text.length) {
    const c = text[i];
    if (c === '"') {
      const start = i + 1;
      let j = start;
      let value = '';
      while (j < text.length && text[j] !== '"') {
        if (text[j] === '\\') { value += text[j + 1] || ''; j += 2; continue; }
        value += text[j];
        j++;
      }
      const end = j;   // offset of the closing quote (or text end when unterminated)
      if (offset >= start && offset <= end) {
        const key = kinds[kinds.length - 1] === '[' ? keys[keys.length - 2] ?? null : pendingKeyOf(text, i);
        if (key === null) return null;
        const object = objectAround(text, i);
        return {
          key, prefix: text.slice(start, offset), start, end,
          from: field(object, 'from'), kind: field(object, 'kind'),
          inArray: kinds[kinds.length - 1] === '[',
        };
      }
      lastString = { start, end, value };
      i = end + 1;
      continue;
    }
    if (c === ':') { pendingKey = lastString ? lastString.value : null; if (kinds.length) keys[kinds.length - 1] = pendingKey; }
    else if (c === '{' || c === '[') { kinds.push(c); keys.push(null); }
    else if (c === '}' || c === ']') { kinds.pop(); keys.pop(); }
    i++;
  }
  return null;
}

/** The key whose value starts at `quoteOffset` (the opening quote of the string), or null for a key itself. */
function pendingKeyOf(text: string, quoteOffset: number): string | null {
  const before = text.slice(Math.max(0, quoteOffset - 200), quoteOffset);
  const m = /"((?:[^"\\]|\\.)*)"\s*:\s*$/.exec(before);
  return m ? m[1] : null;
}

/** The innermost `{ ... }` around `offset`, as text with nested objects and arrays blanked. */
function objectAround(text: string, offset: number): string {
  let depth = 0;
  let start = -1;
  for (let i = offset; i >= 0; i--) {
    const c = text[i];
    if (c === '}') depth++;
    else if (c === '{') { if (depth === 0) { start = i; break; } depth--; }
  }
  if (start < 0) return '';
  let end = text.length;
  depth = 0;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
  }
  return blankNested(text.slice(start, end));
}

function blankNested(object: string): string {
  // Keep the outer object's own strings; blank what nested objects/arrays hold.
  let out = '';
  let depth = 0;
  let inString = false;
  for (let i = 0; i < object.length; i++) {
    const c = object[i];
    if (inString) { if (c === '\\') { out += depth <= 1 ? c + (object[i + 1] || '') : ''; i++; continue; } if (c === '"') inString = false; out += depth <= 1 ? c : ''; continue; }
    if (c === '"') { inString = true; out += depth <= 1 ? c : ''; continue; }
    if (c === '{' || c === '[') { depth++; out += depth <= 1 ? c : ''; continue; }
    if (c === '}' || c === ']') { depth--; out += depth <= 0 ? c : ''; continue; }
    out += depth <= 1 ? c : '';
  }
  return out;
}

function field(object: string, name: string): string | null {
  const m = new RegExp(`"${name}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"`).exec(object);
  return m ? m[1] : null;
}

/** The nodes of the file, read tolerantly: every object with an "id" and a "kind",
 *  whether or not the rest of the text is valid JSON yet. */
export function nodesOf(text: string): FlowNode[] {
  const nodes: FlowNode[] = [];
  for (const object of objectsIn(text)) {
    const id = field(object, 'id');
    const kind = field(object, 'kind');
    if (id !== null && kind !== null) nodes.push({ id, kind, label: field(object, 'label') || id });
  }
  return nodes;
}

/** Every object in `text` (at any depth), each with its nested parts blanked. */
function objectsIn(text: string): string[] {
  const out: string[] = [];
  const stack: number[] = [];
  let inString = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inString) { if (c === '\\') i++; else if (c === '"') inString = false; continue; }
    if (c === '"') inString = true;
    else if (c === '{') stack.push(i);
    else if (c === '}') { const s = stack.pop(); if (s !== undefined) out.push(blankNested(text.slice(s, i + 1))); }
  }
  return out;
}

/** The variables the file itself defines or assigns: "variables" keys and "assign" values. */
export function variablesOf(text: string): string[] {
  const found = new Set<string>();
  // The section's braces, with a "${NAME}" key's own braces allowed inside.
  const vars = /"variables"\s*:\s*\{((?:[^{}]|\$\{[^}]*\})*)\}/.exec(text);
  if (vars) for (const m of vars[1].matchAll(/"([^"]+)"\s*:/g)) found.add(`\${${m[1].replace(/^\$\{|\}$/g, '')}}`);
  for (const m of text.matchAll(/"assign"\s*:\s*(\[[^\]]*\]|"[^"]*")/g)) {
    for (const v of m[1].matchAll(/\$\{[^}]+\}/g)) found.add(v[0]);
  }
  return [...found];
}

/** The completion items for the place, from the file's text and the catalog (null: not read). */
export function itemsFor(text: string, place: Place, catalog: FlowCatalog | null): Item[] {
  const items: Item[] = [];
  const variable = /\$\{[^}]*$/.exec(place.prefix);
  if (variable && place.key !== 'from' && place.key !== 'to' && place.key !== 'label' && place.key !== 'kind') {
    const seen = new Set<string>();
    const add = (name: string, detail: string, doc?: string) => {
      if (seen.has(name)) return;
      seen.add(name);
      items.push({ label: name, kind: 'variable', detail, doc, insert: place.prefix.slice(0, variable.index) + name, sort: `0${name}` });
    };
    for (const v of variablesOf(text)) add(v, 'this file');
    for (const v of catalog?.variables || []) add(v.name, v.source, v.value);
    return items;
  }
  if (place.key === 'from' || place.key === 'to') {
    const nodes = nodesOf(text);
    for (const n of nodes) {
      if (place.key === 'to' && n.kind === 'start') continue;
      if (place.key === 'from' && n.kind === 'end') continue;
      items.push({ label: n.id, kind: 'node', detail: n.kind + (n.label !== n.id ? ` · ${n.label}` : ''), sort: `0${n.id}` });
    }
    return items;
  }
  if (place.key === 'label') {
    const nodes = nodesOf(text);
    const kind = place.from !== null ? nodes.find((n) => n.id === place.from)?.kind : undefined;
    const labels = kind && LABELS_BY_KIND[kind] ? LABELS_BY_KIND[kind] : ALL_LABELS;
    for (const l of labels) {
      items.push({ label: l || '(then)', insert: l, kind: 'label', detail: kind ? `from a ${kind} node` : undefined, doc: LABEL_DOC[l] });
    }
    return items;
  }
  if (place.key === 'keyword' && !place.inArray) {
    for (const k of catalog?.keywords || []) {
      const args = k.args.map((a) => (a.kind === 'VAR_POSITIONAL' ? `*${a.name}` : a.kind === 'VAR_NAMED' ? `**${a.name}` : a.required ? a.name : `${a.name}=${a.default ?? ''}`)).join(', ');
      items.push({ label: k.name, kind: 'keyword', detail: `${k.owner}${args ? ` · ${args}` : ''}`, doc: k.shortdoc,
                   sort: `${k.owner_type === 'file' ? 0 : k.owner_type === 'resource' ? 1 : k.owner === 'BuiltIn' ? 3 : 2}${k.name}` });
    }
    return items;
  }
  if (place.key === 'file' && !place.inArray) {
    items.push({ label: '', kind: 'file' });   // the provider lists the files
  }
  return items;
}

/** The keywords and variables a flow file can use (robot_grid.py --catalog). */
export async function flowCatalog(env: RobotEnv, filePath: string, text: string): Promise<FlowCatalog> {
  const answer = await runHelper(env, 'robot_grid.py', filePath, JSON.stringify({ text }), ['--catalog'], 'the keywords');
  if (!answer.ok) return { ok: false, error: answer.error, keywords: [], variables: [] };
  return { ok: true, keywords: (answer.keywords as KeywordInfo[]) || [], variables: (answer.variables as VariableInfo[]) || [] };
}

/** The part of the text the catalog depends on: a change elsewhere does not reread it. */
export function catalogKey(text: string): string {
  const m = /"imports"\s*:\s*\{[^}]*\}/.exec(text);
  return m ? m[0] : '';
}
