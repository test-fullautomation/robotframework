// What the results folders hold: one folder per run below a results root,
// named <yyyymmdd-hhmmss>-<name> by the extension (a group run holds one
// folder per member). A run is listed from its run.json (written by the
// extension's runs) and the tail of its output.xml, where the top suite's
// status and the statistics are -- the file of a long run can be hundreds
// of megabytes, the tail is a few kilobytes. The tests or phases come from
// python/run_summary.py when a run is expanded.
// No VS Code API here; test/ runs it with plain Node.
import * as fs from 'node:fs';
import * as path from 'node:path';
import { Answer, RobotEnv, runHelper } from './helpers';

export const RUN_INFO_FILE = 'run.json';
export const OUTPUT_FILE = 'output.xml';
/** The newest folders listed per root; older ones are not read. */
export const LIST_LIMIT = 300;

export type RunKind = 'suite' | 'flow' | 'group';
export type HistoryStatus = 'running' | 'stopping' | 'passed' | 'failed' | 'stopped' | 'error' | 'incomplete' | 'unknown';

/** run.json: what the extension knows about a run it started. */
export interface RunInfo {
  title: string;
  /** The suite or flow file; null for a group. */
  target: string | null;
  kind: RunKind;
  /** A group's member ids (its subfolders). */
  members?: string[];
  variables: Record<string, string>;
  started: number;
  ended?: number | null;
  status?: string | null;
  exitCodes?: Record<string, number | null>;
}

export interface Counts {
  pass: number;
  fail: number;
  skip: number;
  unknown: number;
}

/** The tail of an output.xml: the top suite's status and the statistics. */
export interface OutputSummary {
  /** The top suite's status: PASS, FAIL, SKIP, UNKNOWN; null when the file has no end. */
  status: string | null;
  suite: string | null;
  /** Start of the top suite (ms) and its elapsed seconds. */
  start: number | null;
  elapsed: number | null;
  total: Counts | null;
  /** The statistics were found: the run wrote its end. */
  complete: boolean;
}

export interface Checkpoint {
  file: string;
  /** 'checkpoint', or the snapshot's name. */
  name: string;
  kind: 'checkpoint' | 'snapshot';
  /** The flow file the checkpoint names, when it does. */
  flow: string | null;
}

export interface RunRecord {
  dir: string;
  /** The folder's name. */
  name: string;
  /** The target's name (run.json), the name in the folder's stamp, or the suite's name. */
  title: string;
  kind: RunKind;
  status: HistoryStatus;
  /** When it started (ms): run.json, the output, or the folder's stamp. */
  started: number | null;
  /** When its output.xml (else the folder) was last written (ms): a run with no end still writing it is alive. */
  updated: number | null;
  elapsed: number | null;
  total: Counts | null;
  info: RunInfo | null;
  target: string | null;
  /** A group's members, one per subfolder with an output.xml. */
  members: RunRecord[];
  files: { log: string | null; report: string | null; flow: string | null; output: string | null };
  checkpoints: Checkpoint[];
}

export interface TestSummary {
  id: string;
  name: string;
  status: string;
  start: string | null;
  elapsed: number | null;
  message: string;
}

/** The answer of run_summary.py. */
export interface OutputDetail {
  ok: boolean;
  error?: string;
  suite?: { name: string; source: string; status: string | null; start: string | null; elapsed: number | null };
  tests?: TestSummary[];
  total?: Counts;
  /** The file has no end (the run is on, or died): what was read so far. */
  partial?: boolean;
}

const STAMP_RE = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-(.*)$/;

/** The time (ms) and name in a folder name the extension stamped, or null. */
export function parseStamp(name: string): { time: number; name: string } | null {
  const m = STAMP_RE.exec(name);
  if (!m) return null;
  const [, y, mo, d, h, mi, s, rest] = m;
  const time = new Date(+y, +mo - 1, +d, +h, +mi, +s).getTime();
  return Number.isFinite(time) ? { time, name: rest } : null;
}

export function readRunInfo(dir: string): RunInfo | null {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(dir, RUN_INFO_FILE), 'utf8'));
    if (!data || typeof data !== 'object' || typeof data.started !== 'number') return null;
    return {
      title: String(data.title || ''),
      target: data.target ? String(data.target) : null,
      kind: data.kind === 'group' || data.kind === 'flow' ? data.kind : 'suite',
      members: Array.isArray(data.members) ? data.members.map(String) : undefined,
      variables: data.variables && typeof data.variables === 'object' ? data.variables : {},
      started: data.started,
      ended: typeof data.ended === 'number' ? data.ended : null,
      status: data.status ? String(data.status) : null,
      exitCodes: data.exitCodes && typeof data.exitCodes === 'object' ? data.exitCodes : undefined,
    };
  } catch {
    return null;
  }
}

export function writeRunInfo(dir: string, info: RunInfo): void {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, RUN_INFO_FILE), JSON.stringify(info, null, 1));
}

const ENTITIES: Record<string, string> = { '&lt;': '<', '&gt;': '>', '&amp;': '&', '&quot;': '"', '&apos;': "'" };

function unescape(text: string): string {
  return text.replace(/&(lt|gt|amp|quot|apos);|&#(\d+);|&#x([0-9a-fA-F]+);/g, (m, name, dec, hex) =>
    name ? ENTITIES[m] : String.fromCodePoint(parseInt(dec || hex, dec ? 10 : 16)));
}

function attrs(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of text.matchAll(/([A-Za-z_][\w.-]*)="([^"]*)"/g)) out[m[1]] = unescape(m[2]);
  return out;
}

/** Robot 7's "2026-10-11T09:46:16.370394" or Robot 6's "20261011 09:46:16.370", as ms; null for N/A. */
export function parseRobotTime(text: string | undefined): number | null {
  if (!text || text === 'N/A') return null;
  let m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$/.exec(text);
  if (!m) m = /^(\d{4})(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$/.exec(text);
  if (!m) {
    const t = Date.parse(text);
    return Number.isFinite(t) ? t : null;
  }
  const ms = m[7] ? Math.round(parseFloat('0.' + m[7]) * 1000) : 0;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms).getTime();
}

/** The status element's time: { start, elapsed } from Robot 7's start/elapsed or Robot 6's starttime/endtime. */
export function statusTimes(a: Record<string, string>): { start: number | null; elapsed: number | null } {
  if (a.start !== undefined || a.elapsed !== undefined) {
    const elapsed = a.elapsed !== undefined ? parseFloat(a.elapsed) : NaN;
    return { start: parseRobotTime(a.start), elapsed: Number.isFinite(elapsed) ? elapsed : null };
  }
  const start = parseRobotTime(a.starttime);
  const end = parseRobotTime(a.endtime);
  return { start, elapsed: start !== null && end !== null ? Math.max(0, (end - start) / 1000) : null };
}

function counts(a: Record<string, string>): Counts {
  const n = (k: string) => { const v = parseInt(a[k] || '0', 10); return Number.isFinite(v) ? v : 0; };
  return { pass: n('pass'), fail: n('fail'), skip: n('skip'), unknown: n('unknown') };
}

/** The last `bytes` of a file as text (the whole file when it is smaller). */
function tailOf(file: string, bytes: number): string | null {
  let fd: number | null = null;
  try {
    const size = fs.statSync(file).size;
    fd = fs.openSync(file, 'r');
    const length = Math.min(size, bytes);
    const buffer = Buffer.alloc(length);
    fs.readSync(fd, buffer, 0, length, size - length);
    return buffer.toString('utf8');
  } catch {
    return null;
  } finally {
    if (fd !== null) fs.closeSync(fd);
  }
}

/** The top suite's source in the head of an output.xml (the file that ran), or null. */
export function sourceOfOutput(file: string): string | null {
  let fd: number | null = null;
  try {
    fd = fs.openSync(file, 'r');
    const buffer = Buffer.alloc(4096);
    const n = fs.readSync(fd, buffer, 0, buffer.length, 0);
    const m = /<suite\s[^>]*?source="([^"]*)"/.exec(buffer.toString('utf8', 0, n));
    return m ? unescape(m[1]) : null;
  } catch {
    return null;
  } finally {
    if (fd !== null) fs.closeSync(fd);
  }
}

/** The summary in the tail of an output.xml; null when the file cannot be read. */
export function summarizeOutput(file: string): OutputSummary | null {
  let text = tailOf(file, 64 * 1024);
  if (text === null) return null;
  // A long errors section after the statistics: look further back, once.
  if (!text.includes('<statistics>')) text = tailOf(file, 1024 * 1024) ?? text;
  return summarizeOutputText(text);
}

/** The same on text (the tail of the file). */
export function summarizeOutputText(text: string): OutputSummary {
  const at = text.lastIndexOf('<statistics>');
  const out: OutputSummary = { status: null, suite: null, start: null, elapsed: null, total: null, complete: at >= 0 };
  if (at < 0) return out;
  const before = text.slice(0, at);
  const after = text.slice(at);
  const total = /<total>\s*<stat\s([^>]*)>/.exec(after);
  if (total) out.total = counts(attrs(total[1]));
  const suite = /<\/total>[\s\S]*?<suite>\s*<stat\s([^>]*)>([^<]*)<\/stat>/.exec(after);
  if (suite) out.suite = unescape(suite[2]) || attrs(suite[1]).name || null;
  const status = /<status\s([^>]*?)\/?>(?:[^<]*<\/status>)?\s*<\/suite>\s*$/.exec(before);
  if (status) {
    const a = attrs(status[1]);
    out.status = a.status || null;
    Object.assign(out, statusTimes(a));
  }
  return out;
}

/** The checkpoint and the snapshots a flow run left in a folder. */
export function listCheckpoints(dir: string): Checkpoint[] {
  let names: string[];
  try {
    names = fs.readdirSync(dir);
  } catch {
    return [];
  }
  const out: Checkpoint[] = [];
  for (const name of names.sort()) {
    const m = /^(.*)\.(checkpoint|snapshot-(.+))\.json$/i.exec(name);
    if (!m) continue;
    const file = path.join(dir, name);
    let flow: string | null = null;
    try {
      const data = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (data && typeof data.flow === 'string') flow = data.flow;
    } catch {
      continue;   // being written, or not a checkpoint
    }
    out.push({ file, name: m[3] ?? 'checkpoint', kind: m[3] !== undefined ? 'snapshot' : 'checkpoint', flow });
  }
  return out;
}

function mtimeOf(file: string | null): number | null {
  if (!file) return null;
  try {
    return fs.statSync(file).mtimeMs;
  } catch {
    return null;
  }
}

function fileIn(dir: string, name: string): string | null {
  const f = path.join(dir, name);
  return fs.existsSync(f) ? f : null;
}

function subfolders(dir: string): string[] {
  try {
    return fs.readdirSync(dir, { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name);
  } catch {
    return [];
  }
}

const FINAL: Record<string, HistoryStatus> = { passed: 'passed', failed: 'failed', stopped: 'stopped', error: 'error' };

/**
 * One run folder (or a member's folder), as it is on disk. `name` is shown
 * when the folder is not stamped; a member is shown by its id (the folder).
 */
export function readRun(dir: string, name = path.basename(dir), member = false): RunRecord {
  const info = readRunInfo(dir);
  const stamp = parseStamp(name);
  const output = fileIn(dir, OUTPUT_FILE);
  const summary = output ? summarizeOutput(output) : null;
  const memberDirs = info?.kind === 'group' && info.members ? info.members.filter((m) => fileIn(path.join(dir, m), OUTPUT_FILE))
    : output ? [] : subfolders(dir).filter((m) => fileIn(path.join(dir, m), OUTPUT_FILE));
  const members = memberDirs.map((m) => readRun(path.join(dir, m), m, true));
  const checkpoints = listCheckpoints(dir);
  // A command-line run (or a member) has no run.json: the output names the file that ran; a checkpoint names the flow.
  const source = output && !info ? sourceOfOutput(output) : null;
  const kind: RunKind = info?.kind ?? (members.length ? 'group'
    : checkpoints.length || /\.flow\.json$/i.test(source || '') ? 'flow' : 'suite');
  const target = info?.target ?? checkpoints.find((c) => c.flow)?.flow ?? source ?? null;
  const title = member ? name : info?.title || (stamp?.name ? stamp.name : summary?.suite || name);
  const record: RunRecord = {
    dir, name, title, kind, status: 'unknown', started: info?.started ?? summary?.start ?? stamp?.time ?? null,
    updated: mtimeOf(output) ?? mtimeOf(dir),
    elapsed: summary?.elapsed ?? (info?.ended && info.started ? (info.ended - info.started) / 1000 : null),
    total: summary?.total ?? null, info, target, members,
    files: { log: fileIn(dir, 'log.html'), report: fileIn(dir, 'report.html'), flow: fileIn(dir, 'flow.html'), output },
    checkpoints,
  };
  if (members.length) {
    record.updated = members.reduce<number | null>((acc, m) => m.updated === null ? acc : Math.max(acc ?? 0, m.updated), record.updated);
    record.total = members.reduce<Counts | null>((acc, m) => m.total ? {
      pass: (acc?.pass || 0) + m.total.pass, fail: (acc?.fail || 0) + m.total.fail,
      skip: (acc?.skip || 0) + m.total.skip, unknown: (acc?.unknown || 0) + m.total.unknown } : acc, null);
    if (record.elapsed === null) record.elapsed = members.reduce<number | null>((acc, m) => m.elapsed === null ? acc : Math.max(acc || 0, m.elapsed), null);
    if (record.started === null) record.started = members.reduce<number | null>((acc, m) => m.started === null ? acc : Math.min(acc ?? m.started, m.started), null);
  }
  record.status = statusOf(record, summary);
  return record;
}

function statusOf(record: RunRecord, summary: OutputSummary | null): HistoryStatus {
  const info = record.info;
  if (info?.ended && info.status && FINAL[info.status]) return FINAL[info.status];
  if (record.members.length) {
    const all = record.members.map((m) => m.status);
    if (all.includes('incomplete')) return 'incomplete';
    if (all.includes('error')) return 'error';
    if (all.includes('stopped')) return 'stopped';
    if (all.includes('failed')) return 'failed';
    return all.every((s) => s === 'passed') ? 'passed' : 'unknown';
  }
  if (!summary) return record.info ? 'incomplete' : 'unknown';
  if (!summary.complete) return 'incomplete';
  switch (summary.status) {
    case 'PASS': return 'passed';
    case 'FAIL': return 'failed';
    case 'UNKNOWN': return 'stopped';   // a flow stop ends the running phase with UNKNOWN
    case 'SKIP': return 'passed';
    default: return 'unknown';
  }
}

/** The run folders below a results root, newest first. The root itself counts when it holds an output.xml. */
export function scanRuns(root: string, limit = LIST_LIMIT): RunRecord[] {
  const names = subfolders(root);
  const key = (name: string) => {
    const stamp = parseStamp(name);
    if (stamp) return stamp.time;
    try { return fs.statSync(path.join(root, name)).mtimeMs; } catch { return 0; }
  };
  const ordered = names.map((n) => ({ n, t: key(n) })).sort((a, b) => b.t - a.t).slice(0, limit);
  const out: RunRecord[] = [];
  for (const { n } of ordered) {
    const dir = path.join(root, n);
    if (!fileIn(dir, OUTPUT_FILE) && !fileIn(dir, RUN_INFO_FILE)
        && !subfolders(dir).some((m) => fileIn(path.join(dir, m), OUTPUT_FILE))) continue;
    out.push(readRun(dir, n));
  }
  if (fileIn(root, OUTPUT_FILE)) out.push(readRun(root));
  return out.sort((a, b) => (b.started || 0) - (a.started || 0));
}

/** A run with no end written counts as still alive while its output was written less than this long ago. */
export const ALIVE_MS = 3600 * 1000;

/** A run of this folder may still be running: no end was written, and the output is recent. */
export function maybeAlive(r: RunRecord, now = Date.now()): boolean {
  if (r.status === 'running' || r.status === 'stopping') return true;
  if (r.status !== 'incomplete' && r.status !== 'unknown') return false;
  return r.updated !== null && now - r.updated < ALIVE_MS;
}

/**
 * Runs that ended more than `ageMs` ago. A run with no end written (a
 * command-line run, or one of another window) counts by the time its
 * output was last written, and not at all while that is recent.
 */
export function olderThan(runs: RunRecord[], ageMs: number, now = Date.now()): RunRecord[] {
  return runs.filter((r) => {
    if (maybeAlive(r, now)) return false;
    const ended = r.info?.ended ?? (r.status === 'incomplete' || r.status === 'unknown' ? r.updated
      : r.started !== null && r.elapsed !== null ? r.started + r.elapsed * 1000 : r.started);
    return ended !== null && now - ended > ageMs;
  });
}

/** "1.2 s", "41 s", "3 min 5 s", "2 h 10 min". */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return '';
  if (seconds < 10) return `${Math.round(seconds * 10) / 10} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} min ${Math.round(seconds - m * 60)} s`;
  const h = Math.floor(m / 60);
  return `${h} h ${m - h * 60} min`;
}

/** The tests (a flow's phases) of an output.xml, streamed by run_summary.py. */
export async function readOutputDetail(env: RobotEnv, output: string): Promise<OutputDetail> {
  const answer: Answer = await runHelper(env, 'run_summary.py', output, '', [], 'the results');
  return answer as unknown as OutputDetail;
}
