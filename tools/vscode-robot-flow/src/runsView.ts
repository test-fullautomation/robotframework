// The Runs view (Explorer): what ran, what runs now and what each run left
// behind. Fed by the results folders (backend/history.ts), so it survives a
// restart and lists command-line runs too; the runs of this window add
// their live state (RunManager). A run expands to its phases or tests
// (python/run_summary.py), its Log / Report / Flow report, its checkpoint
// and snapshots (Continue from here), and its folder.
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { describeFlow } from './backend/control';
import { Checkpoint, HistoryStatus, OutputDetail, RunRecord, TestSummary, formatDuration, olderThan, readOutputDetail, readRun, scanRuns } from './backend/history';
import { PROJECT_FILE } from './backend/project';
import { writeFlowReport } from './backend/report';
import { RunPage } from './runPage';
import { RunManager, RunState } from './runs';
import { resultsRoot, robotEnvFor } from './settings';

export const VIEW_ID = 'robotFlow.runs';
const EXTRA_ROOTS_KEY = 'robotFlow.resultsRoots';

export type Node =
  | { type: 'root'; dir: string }
  | { type: 'run'; run: RunRecord; state: RunState | null; member: boolean }
  | { type: 'live'; run: RunRecord; state: RunState }
  | { type: 'tests'; run: RunRecord; state: RunState | null }
  | { type: 'test'; test: TestSummary }
  | { type: 'artifact'; label: string; file: string; icon: string }
  | { type: 'flowReport'; run: RunRecord }
  | { type: 'output'; state: RunState }
  | { type: 'folder'; dir: string }
  | { type: 'checkpoint'; run: RunRecord; checkpoint: Checkpoint }
  | { type: 'text'; text: string; icon?: string };

const ICONS: Record<HistoryStatus, [string, string | null]> = {
  running: ['sync~spin', 'testing.iconQueued'],
  stopping: ['debug-stop', 'testing.iconQueued'],
  passed: ['testing-passed-icon', 'testing.iconPassed'],
  failed: ['testing-failed-icon', 'testing.iconFailed'],
  stopped: ['debug-stop', 'charts.orange'],
  error: ['testing-error-icon', 'testing.iconErrored'],
  incomplete: ['circle-slash', 'testing.iconUnset'],
  unknown: ['question', 'testing.iconUnset'],
};

const TEST_ICONS: Record<string, [string, string]> = {
  PASS: ['testing-passed-icon', 'testing.iconPassed'],
  FAIL: ['testing-failed-icon', 'testing.iconFailed'],
  SKIP: ['testing-skipped-icon', 'testing.iconSkipped'],
  UNKNOWN: ['question', 'charts.orange'],
};

const AGES: { label: string; ms: number }[] = [
  { label: 'Older than a day', ms: 24 * 3600 * 1000 },
  { label: 'Older than a week', ms: 7 * 24 * 3600 * 1000 },
  { label: 'Older than a month', ms: 30 * 24 * 3600 * 1000 },
  { label: 'Every finished run', ms: -1 },
];

function icon(name: string, color: string | null): vscode.ThemeIcon {
  return new vscode.ThemeIcon(name, color ? new vscode.ThemeColor(color) : undefined);
}

export function formatWhen(ms: number | null, now = new Date()): string {
  if (ms === null) return '';
  const d = new Date(ms);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === now.toDateString() ? time : `${d.toLocaleDateString()} ${time}`;
}

const WORDS: Record<HistoryStatus, string> = {
  running: 'running', stopping: 'stopping', passed: 'passed', failed: 'failed', stopped: 'stopped',
  error: 'error', incomplete: 'no end written', unknown: 'unknown',
};

/** "failed · 14:02 · 9 s · 1 of 4 failed": one line after the run's name. */
export function describeRun(run: RunRecord, state: RunState | null, now = Date.now()): string {
  const parts: string[] = [WORDS[run.status]];
  if (run.status === 'running' || run.status === 'stopping') {
    const flow = state && state.pausable ? describeFlow(state.flow) : null;
    if (flow) parts[0] = flow.text;
    else if (state && state.positions.main?.node) parts.push(`step ${state.positions.main.node}`);
    parts.push(formatDuration((now - (state?.started ?? run.started ?? now)) / 1000));
    return parts.filter(Boolean).join(' · ');
  }
  parts.push(formatWhen(run.started, new Date(now)), formatDuration(run.elapsed));
  if (run.total) {
    const all = run.total.pass + run.total.fail + run.total.skip + run.total.unknown;
    if (run.total.fail) parts.push(`${run.total.fail} of ${all} failed`);
    else if (run.total.unknown) parts.push(`${run.total.unknown} of ${all} unknown`);
  }
  if (run.checkpoints.length && run.status !== 'passed') parts.push('can continue');
  return parts.filter(Boolean).join(' · ');
}

interface Cached {
  record: RunRecord;
  signature: string;
}

export class RunsProvider implements vscode.TreeDataProvider<Node>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private readonly cache = new Map<string, Cached>();
  private readonly details = new Map<string, { signature: string; detail: OutputDetail }>();
  private projectFiles: string[] = [];
  private watchers: vscode.FileSystemWatcher[] = [];
  private watched = '';
  private timer: NodeJS.Timeout | null = null;
  private ticker: NodeJS.Timeout | null = null;
  private readonly subscriptions: vscode.Disposable[] = [];

  constructor(private readonly context: vscode.ExtensionContext, private readonly runs: RunManager) {
    this.subscriptions.push(
      runs.onDidChange(() => this.refresh()),
      vscode.workspace.onDidChangeWorkspaceFolders(() => this.rescan()),
      vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration('robotFlow.resultsFolder')) this.rescan(); }),
    );
    void this.rescan();
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer);
    if (this.ticker) clearInterval(this.ticker);
    this.watchers.forEach((w) => w.dispose());
    this.subscriptions.forEach((s) => s.dispose());
    this.changed.dispose();
  }

  /** Find the workspace's test projects again (their results folders are roots), then redraw. */
  async rescan(): Promise<void> {
    try {
      const files = await vscode.workspace.findFiles(`**/${PROJECT_FILE}`, '**/{node_modules,results,.git}/**', 50);
      this.projectFiles = files.map((f) => f.fsPath);
    } catch {
      this.projectFiles = [];
    }
    this.refresh(0);
  }

  /** Redraw soon (events come in bursts: positions every 250 ms, files as a run ends). */
  refresh(delayMs = 500): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.changed.fire(undefined);
    }, delayMs);
  }

  /** The results roots: the workspace folders', the test projects', and those of the runs of this window. */
  roots(): string[] {
    const out = new Map<string, string>();
    const add = (p: string) => {
      const full = path.resolve(p);
      const key = full.toLowerCase();
      if (!out.has(key)) out.set(key, full);
    };
    for (const f of vscode.workspace.workspaceFolders || []) add(resultsRoot(f.uri));
    for (const p of this.projectFiles) add(resultsRoot(vscode.Uri.file(p)));
    const extra = new Set(this.context.workspaceState.get<string[]>(EXTRA_ROOTS_KEY, []));
    let grew = false;
    for (const s of this.runs.all()) {
      const root = path.dirname(path.resolve(s.outDir));
      add(root);
      if (!extra.has(root)) { extra.add(root); grew = true; }
    }
    if (grew) void this.context.workspaceState.update(EXTRA_ROOTS_KEY, [...extra]);
    extra.forEach(add);
    const roots = [...out.values()].filter((dir) => fs.existsSync(dir));
    this.watch(roots);
    return roots;
  }

  private watch(roots: string[]): void {
    const key = roots.join('\n');
    if (key === this.watched) return;
    this.watched = key;
    this.watchers.forEach((w) => w.dispose());
    this.watchers = roots.map((root) => {
      const w = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(root, '{*,*/output.xml,*/run.json,*/*.checkpoint.json,*/*.snapshot-*.json,*/*/output.xml}'));
      w.onDidCreate(() => this.refresh());
      w.onDidDelete(() => this.refresh());
      w.onDidChange(() => this.refresh());
      return w;
    });
  }

  /** The runs below a root, newest first and the running ones on top, with the live state of this window's runs. */
  runsOf(root: string): { run: RunRecord; state: RunState | null }[] {
    const seen = new Set<string>();
    const out: { run: RunRecord; state: RunState | null }[] = [];
    for (const fresh of scanRuns(root)) {
      const run = this.cached(fresh.dir, fresh.name);
      seen.add(run.dir.toLowerCase());
      const state = this.runs.stateOfDir(run.dir);
      if (state && (state.status === 'running' || state.status === 'stopping')) run.status = state.status;
      out.push({ run, state });
    }
    // A run of this window whose folder was just made (nothing in it yet).
    for (const s of this.runs.all()) {
      const dir = path.resolve(s.outDir);
      if (path.dirname(dir).toLowerCase() !== root.toLowerCase() || seen.has(dir.toLowerCase())) continue;
      const run = readRun(dir);
      run.title = run.info?.title || s.title;
      if (s.status === 'running' || s.status === 'stopping') run.status = s.status;
      out.push({ run, state: s });
    }
    const live = (r: RunRecord) => r.status === 'running' || r.status === 'stopping' ? 1 : 0;
    out.sort((a, b) => live(b.run) - live(a.run) || (b.run.started || 0) - (a.run.started || 0));
    const anyLive = out.some((o) => live(o.run));
    if (anyLive && !this.ticker) this.ticker = setInterval(() => this.changed.fire(undefined), 2000);
    if (!anyLive && this.ticker) { clearInterval(this.ticker); this.ticker = null; }
    return out;
  }

  /** Every run of every root (the API, tests). */
  list(): RunRecord[] {
    return this.roots().flatMap((root) => this.runsOf(root).map((o) => o.run));
  }

  /** One run folder as the view shows it now (a member's folder gives the member). */
  lookup(dir: string): { run: RunRecord; state: RunState | null } | null {
    const full = path.resolve(dir);
    if (!fs.existsSync(full)) return null;
    const state = this.runs.stateOfDir(full);
    const parent = path.dirname(full);
    const group = state && state.outDir.toLowerCase() === parent.toLowerCase() ? this.cached(parent, path.basename(parent)) : null;
    const member = group?.members.find((m) => m.dir.toLowerCase() === full.toLowerCase());
    const run = member ?? this.cached(full, path.basename(full));
    if (state && !member && (state.status === 'running' || state.status === 'stopping')) run.status = state.status;
    return { run, state: member ? null : state };
  }

  /** The flow report of a run: the file it has, or one written now from its output (null when that fails). */
  async flowReportOf(run: RunRecord): Promise<string | null> {
    if (run.files.flow) return run.files.flow;
    if (!run.files.output) {
      void vscode.window.showInformationMessage(`${run.title}: no output.xml to write the flow report from.`);
      return null;
    }
    const env = await robotEnvFor(vscode.Uri.file(run.target && fs.existsSync(run.target) ? run.target : run.files.output), this.context.extensionPath);
    const answer = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `Writing the flow report of ${run.title}…` },
                                                    () => writeFlowReport(env, run.files.output!));
    if (!answer.ok) {
      void vscode.window.showWarningMessage(`The flow report was not written: ${answer.error}` +
        (answer.missing ? ' (it needs the RobotFramework AIO fork: robotFlow.robotSource)' : ''));
      return null;
    }
    this.cache.delete(run.dir.toLowerCase());
    this.refresh(0);
    return answer.file ?? null;
  }

  /** A folder is read again only when it, its output.xml, run.json or a checkpoint changed. */
  private cached(dir: string, name: string): RunRecord {
    const signature = [dir, 'output.xml', 'run.json', 'log.html', 'flow.html'].map((f) => {
      try {
        const st = fs.statSync(f === dir ? dir : path.join(dir, f));
        return `${st.mtimeMs}:${st.size}`;
      } catch {
        return '-';
      }
    }).join('|') + '|' + this.checkpointSignature(dir);
    const hit = this.cache.get(dir.toLowerCase());
    if (hit && hit.signature === signature) return hit.record;
    const record = readRun(dir, name);
    this.cache.set(dir.toLowerCase(), { record, signature });
    return record;
  }

  private checkpointSignature(dir: string): string {
    try {
      return fs.readdirSync(dir).filter((f) => /\.(checkpoint|snapshot-.+)\.json$/i.test(f)).sort().join(',');
    } catch {
      return '';
    }
  }

  getTreeItem(node: Node): vscode.TreeItem {
    switch (node.type) {
      case 'root': {
        const item = new vscode.TreeItem(vscode.workspace.asRelativePath(node.dir, true), vscode.TreeItemCollapsibleState.Expanded);
        item.id = 'root:' + node.dir.toLowerCase();
        item.iconPath = new vscode.ThemeIcon('folder');
        item.tooltip = node.dir;
        item.contextValue = 'root';
        return item;
      }
      case 'run': return this.runItem(node);
      case 'live': {
        const flow = node.state.pausable ? describeFlow(node.state.flow) : null;
        const paused = !!flow && flow.paused > 0;
        const pos = node.state.positions.main;
        const text = flow ? flow.text : pos?.node ? `step ${pos.node}` : node.state.status;
        const item = new vscode.TreeItem(text);
        item.id = 'live:' + node.run.dir.toLowerCase();
        item.iconPath = new vscode.ThemeIcon(paused ? 'debug-pause' : 'pulse');
        item.description = node.state.paused ? `paused by the debugger: ${node.state.paused.reason}` : node.state.step ? 'step mode' : '';
        item.contextValue = 'live';
        item.tooltip = 'Where the run is now';
        return item;
      }
      case 'tests': {
        const flow = node.run.kind === 'flow';
        const item = new vscode.TreeItem(flow ? 'Phases' : 'Tests', vscode.TreeItemCollapsibleState.Collapsed);
        item.id = 'tests:' + node.run.dir.toLowerCase();
        item.iconPath = new vscode.ThemeIcon('list-tree');
        const t = node.run.total;
        if (t) {
          const parts = [`${t.pass} passed`];
          if (t.fail) parts.push(`${t.fail} failed`);
          if (t.unknown) parts.push(`${t.unknown} unknown`);
          if (t.skip) parts.push(`${t.skip} skipped`);
          item.description = parts.join(', ');
        }
        item.contextValue = 'tests';
        return item;
      }
      case 'test': {
        const item = new vscode.TreeItem(node.test.name || node.test.id);
        const [name, color] = TEST_ICONS[node.test.status] || ['circle-outline', 'testing.iconUnset'];
        item.iconPath = icon(name, color);
        const message = node.test.message.replace(/\s+/g, ' ');
        item.description = [formatDuration(node.test.elapsed), node.test.status === 'PASS' ? '' : message.slice(0, 120)].filter(Boolean).join(' · ');
        item.tooltip = new vscode.MarkdownString(`**${node.test.status}** ${node.test.name}` + (node.test.message ? `\n\n${node.test.message}` : ''));
        item.contextValue = 'test';
        return item;
      }
      case 'artifact': {
        const item = new vscode.TreeItem(node.label);
        item.iconPath = new vscode.ThemeIcon(node.icon);
        item.tooltip = node.file;
        item.command = { command: 'robotFlow.runs.openFile', title: 'Open', arguments: [node.file] };
        item.contextValue = 'artifact';
        return item;
      }
      case 'flowReport': {
        const item = new vscode.TreeItem('Flow report');
        item.iconPath = new vscode.ThemeIcon('type-hierarchy');
        item.description = 'written on demand';
        item.tooltip = 'Not written with the run (robotFlow.flowReport is off): a click writes it from output.xml and opens it.';
        item.command = { command: 'robotFlow.runs.flowReport', title: 'Flow report', arguments: [node] };
        item.contextValue = 'flowReport';
        return item;
      }
      case 'output': {
        const item = new vscode.TreeItem('Output');
        item.iconPath = new vscode.ThemeIcon('output');
        item.tooltip = 'The console text of the run, in the Output panel';
        item.command = { command: 'robotFlow.runs.output', title: 'Output', arguments: [node] };
        item.contextValue = 'output';
        return item;
      }
      case 'folder': {
        const item = new vscode.TreeItem('Open folder');
        item.iconPath = new vscode.ThemeIcon('folder-opened');
        item.tooltip = node.dir;
        item.command = { command: 'robotFlow.runs.openFolder', title: 'Open folder', arguments: [node] };
        item.contextValue = 'folder';
        return item;
      }
      case 'checkpoint': {
        const c = node.checkpoint;
        const item = new vscode.TreeItem(path.basename(c.file));
        item.iconPath = new vscode.ThemeIcon(c.kind === 'snapshot' ? 'save' : 'bookmark');
        item.description = c.kind === 'snapshot' ? `snapshot "${c.name}"` : 'checkpoint';
        item.tooltip = new vscode.MarkdownString(`${c.file}\n\n*Continue from here* starts a new run of the flow from this file (\`FLOW_CHECKPOINT\`).`);
        item.command = { command: 'vscode.open', title: 'Open', arguments: [vscode.Uri.file(c.file)] };
        item.contextValue = this.continueTarget(node.run, c) ? 'checkpoint.continue' : 'checkpoint';
        return item;
      }
      case 'text': {
        const item = new vscode.TreeItem(node.text);
        if (node.icon) item.iconPath = new vscode.ThemeIcon(node.icon);
        item.contextValue = 'text';
        return item;
      }
    }
  }

  private runItem(node: { run: RunRecord; state: RunState | null; member: boolean }): vscode.TreeItem {
    const { run, state } = node;
    const item = new vscode.TreeItem(run.title, vscode.TreeItemCollapsibleState.Collapsed);
    item.id = 'run:' + run.dir.toLowerCase();
    const [name, color] = ICONS[run.status];
    item.iconPath = icon(name, color);
    item.description = describeRun(run, state);
    const lines = [`**${run.title}** · ${WORDS[run.status]}`, '', run.dir];
    if (run.started) lines.push(`started ${new Date(run.started).toLocaleString()}`);
    if (run.target) lines.push(`target ${run.target}`);
    const variables = Object.entries(run.info?.variables || {});
    if (variables.length) lines.push(`variables ${variables.map(([k, v]) => `${k}=${v}`).join(', ')}`);
    if (run.kind === 'group') lines.push(`members ${run.members.map((m) => m.name).join(', ')}`);
    item.tooltip = new vscode.MarkdownString(lines.join('  \n'));
    const flags: string[] = [node.member ? 'member' : 'run', run.status, run.kind];
    if (state && (run.status === 'running' || run.status === 'stopping') && state.pausable) {
      flags.push('pausable', describeFlow(state.flow)?.paused ? 'paused' : 'live');
    }
    if (run.target && fs.existsSync(run.target)) flags.push('target');
    item.contextValue = flags.join('.');
    item.command = { command: 'robotFlow.runs.open', title: 'Open Run Page', arguments: [node] };
    return item;
  }

  private continueTarget(run: RunRecord, c: Checkpoint): string | null {
    for (const candidate of [c.flow, run.target]) {
      if (candidate && /\.flow\.json$/i.test(candidate) && fs.existsSync(candidate)) return candidate;
    }
    return null;
  }

  async getChildren(node?: Node): Promise<Node[]> {
    if (!node) {
      const roots = this.roots();
      if (roots.length === 1) return this.runNodes(roots[0]);
      return roots.map((dir) => ({ type: 'root', dir }));
    }
    switch (node.type) {
      case 'root': return this.runNodes(node.dir);
      case 'run': return this.runChildren(node.run, node.state);
      case 'tests': return this.testNodes(node.run);
      default: return [];
    }
  }

  private runNodes(root: string): Node[] {
    return this.runsOf(root).map(({ run, state }) => ({ type: 'run', run, state, member: false }));
  }

  private runChildren(run: RunRecord, state: RunState | null): Node[] {
    const out: Node[] = [];
    const live = state && (state.status === 'running' || state.status === 'stopping');
    if (live) out.push({ type: 'live', run, state });
    if (run.kind === 'group') {
      for (const m of run.members) out.push({ type: 'run', run: m, state: null, member: true });
    } else if (run.files.output) {
      out.push({ type: 'tests', run, state });
    }
    if (run.files.flow) out.push({ type: 'artifact', label: 'Flow report', file: run.files.flow, icon: 'type-hierarchy' });
    else if (run.kind === 'flow' && run.files.output && !live) out.push({ type: 'flowReport', run });
    if (run.files.log) out.push({ type: 'artifact', label: 'Log', file: run.files.log, icon: 'book' });
    if (run.files.report) out.push({ type: 'artifact', label: 'Report', file: run.files.report, icon: 'graph' });
    for (const checkpoint of run.checkpoints) out.push({ type: 'checkpoint', run, checkpoint });
    if (state) out.push({ type: 'output', state });
    out.push({ type: 'folder', dir: run.dir });
    return out;
  }

  private async testNodes(run: RunRecord): Promise<Node[]> {
    const output = run.files.output;
    if (!output) return [];
    let signature: string;
    try {
      const st = fs.statSync(output);
      signature = `${st.mtimeMs}:${st.size}`;
    } catch {
      return [{ type: 'text', text: 'output.xml is gone', icon: 'warning' }];
    }
    const hit = this.details.get(output.toLowerCase());
    let detail = hit && hit.signature === signature ? hit.detail : null;
    if (!detail) {
      const env = await robotEnvFor(vscode.Uri.file(run.target && fs.existsSync(run.target) ? run.target : output), this.context.extensionPath);
      detail = await readOutputDetail(env, output);
      if (detail.ok && !detail.partial) this.details.set(output.toLowerCase(), { signature, detail });
    }
    if (!detail.ok) return [{ type: 'text', text: detail.error || 'The results could not be read.', icon: 'warning' }];
    const tests = detail.tests || [];
    if (!tests.length) return [{ type: 'text', text: detail.partial ? 'Nothing finished yet' : 'No tests', icon: 'info' }];
    return tests.map((test) => ({ type: 'test', test }));
  }

  /**
   * Delete the folders of finished runs older than `ageMs` (-1: every finished run). The number
   * deleted. A run of this window that runs, and a folder with no end written whose output is
   * still being written (a command-line run, another window's), are kept.
   */
  deleteOlder(ageMs: number): number {
    let n = 0;
    for (const root of this.roots()) {
      const runs = this.runsOf(root).filter((o) => !o.state || !(o.state.status === 'running' || o.state.status === 'stopping')).map((o) => o.run);
      const old = olderThan(runs, Math.max(0, ageMs));
      for (const r of old) {
        if (r.dir.toLowerCase() === root.toLowerCase()) continue;   // the root itself holds a command-line run: keep it
        try {
          fs.rmSync(r.dir, { recursive: true, force: true });
          n++;
        } catch { /* in use: it stays */ }
      }
    }
    this.cache.clear();
    this.refresh(0);
    return n;
  }
}

export function registerRunsView(context: vscode.ExtensionContext, runs: RunManager): RunsProvider {
  const provider = new RunsProvider(context, runs);
  const view = vscode.window.createTreeView(VIEW_ID, { treeDataProvider: provider, showCollapseAll: true });
  const runNode = (node: unknown): { run: RunRecord; state: RunState | null } | null =>
    node && typeof node === 'object' && (node as Node).type === 'run' ? node as { run: RunRecord; state: RunState | null } : null;
  const liveKey = (node: unknown): string | null => {
    const n = runNode(node);
    return n?.state && (n.state.status === 'running' || n.state.status === 'stopping') ? n.state.key : null;
  };
  context.subscriptions.push(
    view, provider,
    vscode.commands.registerCommand('robotFlow.runs.refresh', () => provider.rescan()),
    vscode.commands.registerCommand('robotFlow.runs.open', (node: Node | string) => {
      const dir = typeof node === 'string' ? node : runNode(node)?.run.dir;
      return dir ? !!RunPage.show(context, provider, runs, dir) : false;
    }),
    vscode.commands.registerCommand('robotFlow.runs.flowReport', async (node: Node) => {
      const run = node.type === 'flowReport' ? node.run : runNode(node)?.run;
      if (!run) return;
      const file = await provider.flowReportOf(run);
      if (file) await vscode.env.openExternal(vscode.Uri.file(file));
    }),
    vscode.commands.registerCommand('robotFlow.runs.openFile', (file: string) => vscode.env.openExternal(vscode.Uri.file(file))),
    vscode.commands.registerCommand('robotFlow.runs.openFolder', (node: Node) => {
      const dir = node.type === 'folder' ? node.dir : node.type === 'run' ? node.run.dir : null;
      if (dir) return vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(dir));
    }),
    vscode.commands.registerCommand('robotFlow.runs.openTarget', (node: Node) => {
      const n = runNode(node);
      if (n?.run.target) return vscode.commands.executeCommand('vscode.open', vscode.Uri.file(n.run.target));
    }),
    vscode.commands.registerCommand('robotFlow.runs.output', (node: Node) => {
      const state = node.type === 'output' ? node.state : runNode(node)?.state;
      if (state) runs.output(state.key);
    }),
    vscode.commands.registerCommand('robotFlow.runs.stop', (node: Node) => { const key = liveKey(node); if (key) return runs.stop(key); }),
    vscode.commands.registerCommand('robotFlow.runs.pause', (node: Node) => { const key = liveKey(node); if (key) return runs.control(key, 'pause'); }),
    vscode.commands.registerCommand('robotFlow.runs.resume', (node: Node) => { const key = liveKey(node); if (key) return runs.control(key, 'resume'); }),
    vscode.commands.registerCommand('robotFlow.runs.snapshot', (node: Node) => { const key = liveKey(node); if (key) return runs.snapshot(key); }),
    vscode.commands.registerCommand('robotFlow.runs.continueFrom', async (node: Node) => {
      if (node.type !== 'checkpoint') return null;
      const target = [node.checkpoint.flow, node.run.target].find((t) => t && /\.flow\.json$/i.test(t) && fs.existsSync(t));
      if (!target) {
        void vscode.window.showInformationMessage(`${path.basename(node.checkpoint.file)} names no flow file that exists: nothing to continue.`);
        return null;
      }
      const variables = { ...(node.run.info?.variables || {}) };
      delete variables.FLOW_CHECKPOINT;
      return runs.continueFrom(target, node.checkpoint.file, variables);
    }),
    vscode.commands.registerCommand('robotFlow.runs.delete', async (node: Node) => {
      const n = runNode(node);
      if (!n || (n.state && (n.state.status === 'running' || n.state.status === 'stopping'))) return;
      const pick = await vscode.window.showWarningMessage(`Delete the results of ${n.run.title} (${formatWhen(n.run.started)})?`,
                                                          { modal: true, detail: n.run.dir }, 'Delete');
      if (pick !== 'Delete') return;
      try {
        fs.rmSync(n.run.dir, { recursive: true, force: true });
      } catch (e) {
        void vscode.window.showErrorMessage(`Not deleted: ${(e as Error).message}`);
      }
      provider.refresh(0);
    }),
    vscode.commands.registerCommand('robotFlow.runs.deleteOld', async () => {
      const pick = await vscode.window.showQuickPick(AGES.map((a) => ({ label: a.label, ms: a.ms })), { placeHolder: 'Delete the results of finished runs' });
      if (!pick) return;
      const confirm = await vscode.window.showWarningMessage(`${pick.label}: delete those run folders?`,
                                                             { modal: true, detail: 'Runs still running are kept: those of this window by their processes, others while they wrote anything in the last day. This cannot be undone.' }, 'Delete');
      if (confirm !== 'Delete') return;
      const n = provider.deleteOlder(pick.ms);
      void vscode.window.showInformationMessage(n ? `${n} run folder${n === 1 ? '' : 's'} deleted.` : 'Nothing to delete.');
    }),
  );
  return provider;
}
