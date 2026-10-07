// A shared view (Grid or Diagram) as a VS Code custom editor of the text
// document. The document stays the truth: every change a view makes is a
// normal text edit (dirty state, Save, Ctrl+Z are VS Code's), and every change
// of the text -- typed, pasted, from git -- redraws the view. The toolbar runs
// the file; a flow's Diagram follows its run live.
import * as path from 'node:path';
import * as vscode from 'vscode';
import * as fs from 'node:fs';
import { anchors, runName, same, stepAt, stepOf, subflows } from './backend/flowMap';
import { Answer, RobotEnv, editView, readFlow, readGrid } from './backend/helpers';
import { RobotInfo, diagramUnavailable, probeRobot } from './backend/robot';
import { RunManager, fileKey } from './runs';
import { refreshDelay, robotEnvFor } from './settings';
import { LiveOptions, revealIn, setOption, toolbar } from './shared';
import { viewHtml } from './webview';

export type ViewId = 'grid' | 'diagram';

interface ViewKind {
  id: ViewId;
  viewType: string;
  title: string;
  /** The shared view module, under media/vendor/. */
  entry: string;
  read(env: RobotEnv, file: string, text: string): Promise<Answer>;
  /** Whether the toolbar offers Run for this file (a .resource is not run). */
  runnable(file: string): boolean;
}

export const VIEWS: Record<ViewId, ViewKind> = {
  grid: {
    id: 'grid', viewType: 'robotFlow.grid', title: 'Robot Grid', entry: 'robot-grid/grid.js', read: readGrid,
    runnable: (f) => f.toLowerCase().endsWith('.robot'),
  },
  diagram: {
    id: 'diagram', viewType: 'robotFlow.diagram', title: 'Flow Diagram', entry: 'flow-view/view.js', read: readFlow,
    runnable: () => true,
  },
};

type Status = { kind?: 'error' | 'busy' | 'info'; text: string } | null;

/** What each interpreter + Robot source can do, asked once. */
const robots = new Map<string, Promise<RobotInfo>>();
export function robotOf(env: RobotEnv): Promise<RobotInfo> {
  const key = JSON.stringify([env.python, env.robotSource, env.pythonPath]);
  if (!robots.has(key)) robots.set(key, probeRobot(env));
  return robots.get(key)!;
}
export function forgetRobots(): void {
  robots.clear();
}

class Session implements vscode.Disposable {
  private readonly subs: vscode.Disposable[] = [];
  private timer: NodeJS.Timeout | undefined;
  private ticker: NodeJS.Timeout | undefined;
  private reading = 0;            // the newest read; an older answer is dropped
  private ours = -1;              // document version of our own last edit
  private undo: string[] = [];    // texts from before the view's edits, until the user types
  private lastGood: Answer | null = null;
  private base: Record<string, unknown> | null = null;   // the view's data without the live position
  private status: Status = null;
  private ready = false;
  private runVersion = -1;        // document version the shown run's position belongs to
  private runStarted = 0;         // which run that is
  private readonly live: LiveOptions = { zoom: 'fit', motion: 'tail' };
  private readonly key: string;
  /** The last read: what the view was sent (for the smoke test and diagnostics). */
  report: { ok: boolean; error?: string; drawn: boolean } | null = null;

  constructor(
    private readonly kind: ViewKind,
    private readonly document: vscode.TextDocument,
    private readonly panel: vscode.WebviewPanel,
    private readonly extension: vscode.ExtensionContext,
    private readonly log: vscode.OutputChannel,
    private readonly runs: RunManager,
  ) {
    this.key = fileKey(document.uri.fsPath);
    this.subs.push(
      vscode.workspace.onDidChangeTextDocument((e) => {
        if (e.document !== this.document || !e.contentChanges.length) return;
        if (this.document.version !== this.ours) this.undo = [];   // the user changed the text
        this.schedule(refreshDelay(this.document.uri));
      }),
      panel.webview.onDidReceiveMessage((msg) => this.onMessage(msg)),
      panel.onDidChangeViewState(() => { if (panel.visible) this.schedule(0); }),
      runs.onDidChange((key) => { if (key === this.key) this.onRun(); }),
      vscode.debug.onDidChangeBreakpoints(() => { if (this.kind.id === 'diagram') this.send(); }),
    );
  }

  dispose(): void {
    clearTimeout(this.timer);
    clearInterval(this.ticker);
    this.subs.forEach((s) => s.dispose());
  }

  get file(): string {
    return this.document.uri.fsPath;
  }

  /** Settings changed: read again. */
  refreshNow(): void {
    this.schedule(0);
  }

  private schedule(ms: number): void {
    if (!this.ready) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.refresh(); }, ms);
  }

  private post(message: unknown): void {
    void this.panel.webview.postMessage(message);
  }

  private editable(): boolean {
    return vscode.workspace.fs.isWritableFileSystem(this.document.uri.scheme) !== false && !this.document.isClosed;
  }

  private async env(): Promise<RobotEnv> {
    return robotEnvFor(this.document.uri, this.extension.extensionPath);
  }

  private async refresh(): Promise<void> {
    const n = ++this.reading;
    const env = await this.env();
    if (this.kind.id === 'diagram') {
      const why = diagramUnavailable(await robotOf(env));
      if (why) {
        if (n === this.reading) {
          this.report = { ok: false, error: why, drawn: false };
          this.base = null;
          this.status = { kind: 'error', text: why };
          this.send();
        }
        return;
      }
    }
    if (!this.lastGood) this.post({ type: 'status', status: { kind: 'busy', text: 'Reading the file…' } });
    const text = this.document.getText();
    const answer = await this.kind.read(env, this.document.uri.fsPath, text);
    if (n !== this.reading) return;   // the text moved on while this ran
    this.status = null;
    if (answer.ok) {
      this.lastGood = answer;
    } else {
      const where = answer.line ? ` (line ${answer.line})` : answer.node ? ` (step ${answer.node})` : '';
      this.status = { kind: 'error', text: `${answer.error || 'The file could not be read.'}${where}` +
                     (this.lastGood ? '\nShowing the last version that could be read.' : '') };
      this.log.appendLine(`[${this.kind.id}] ${this.document.uri.fsPath}: ${answer.error}`);
    }
    const data = this.lastGood;
    this.base = data ? { ...data, ok: undefined, robot: undefined,
                         editable: this.editable() && answer.ok, undo: this.undo.length > 0 } : null;
    this.report = { ok: answer.ok, error: answer.error, drawn: !!this.base };
    this.send();
  }

  /** The run's position, while it belongs to this text (a run, then no edits since it started). */
  private livePosition(): unknown {
    if (this.kind.id !== 'diagram') return undefined;
    const state = this.runs.state(this.key);
    if (!state || state.positions.main == null || this.runVersion !== this.document.version) return undefined;
    return state.positions.main;
  }

  // ---- breakpoints on the Diagram's steps ----------------------------------

  private subsMemo: { version: number; subs: Map<string, string> } | null = null;

  /** The sub-flow files this flow calls -> the name their steps run under (read once per text version). */
  private subflowNames(): Map<string, string> {
    if (!this.subsMemo || this.subsMemo.version !== this.document.version) {
      this.subsMemo = { version: this.document.version, subs: subflows(this.file, (f) => this.json(f)) };
    }
    return this.subsMemo.subs;
  }

  private textOf(file: string): string {
    if (same(file, this.file)) return this.document.getText();
    const open = vscode.workspace.textDocuments.find((d) => same(d.uri.fsPath, file));
    if (open) return open.getText();
    try { return fs.readFileSync(file, 'utf8'); } catch { return ''; }
  }

  private json(file: string): Record<string, unknown> | null {
    try { return JSON.parse(this.textOf(file)); } catch { return null; }
  }

  /** The steps with an enabled breakpoint: this file's, and its sub-flows' as "<name>::<id>". */
  private breakpointSteps(): string[] {
    const subs = this.subflowNames();
    const out: string[] = [];
    const byFile = new Map<string, ReturnType<typeof anchors>>();
    for (const bp of vscode.debug.breakpoints) {
      if (!(bp instanceof vscode.SourceBreakpoint) || !bp.enabled) continue;
      const file = bp.location.uri.fsPath;
      if (!same(file, this.file) && runName(this.file, file, '', subs) === null) continue;
      if (!byFile.has(file)) byFile.set(file, anchors(this.textOf(file)));
      const step = stepAt(byFile.get(file)!, bp.location.range.start.line + 1);
      const name = step && runName(this.file, file, step.id, subs);
      if (name) out.push(name);
    }
    return out;
  }

  /** A click on a step's breakpoint dot: set one on the step's line, or remove the step's. */
  private toggleBreakpoint(node: string): void {
    const where = stepOf(this.file, node, this.subflowNames());
    if (!where) return;
    const list = anchors(this.textOf(where.file));
    const step = list.find((a) => a.id === where.id);
    if (!step) return;
    const existing = vscode.debug.breakpoints.filter((bp) => bp instanceof vscode.SourceBreakpoint &&
      same(bp.location.uri.fsPath, where.file) && stepAt(list, bp.location.range.start.line + 1)?.id === where.id);
    if (existing.length) {
      vscode.debug.removeBreakpoints(existing);
    } else {
      const at = new vscode.Location(vscode.Uri.file(where.file), new vscode.Position(step.line - 1, 0));
      vscode.debug.addBreakpoints([new vscode.SourceBreakpoint(at)]);
    }
  }

  /** The view's data (+ live position, breakpoints, where the debugger paused) and the toolbar. */
  private send(): void {
    const state = this.runs.state(this.key);
    const live = this.livePosition();
    let selection: Record<string, unknown> | null = this.base
      ? (live === undefined ? this.base : { ...this.base, live, zoom: this.live.zoom, motion: this.live.motion })
      : null;
    if (selection && this.kind.id === 'diagram') {
      const paused = live !== undefined && state?.paused ? state.paused.node : null;
      selection = { ...selection, breakpoints: this.breakpointSteps(), paused };
    }
    this.post({ type: 'selection', selection, status: this.status });
    this.post({ type: 'toolbar', toolbar: toolbar(state, {
      ...this.live,
      canRun: this.kind.runnable(this.file),
      runTitle: `Run ${path.basename(this.file)} (saves it first)`,
      canDebug: this.kind.runnable(this.file) && /\.(robot|flow\.json)$/i.test(this.file),
      canStep: this.kind.runnable(this.file) && /\.flow\.json$/i.test(this.file),
      live: live !== undefined,
      hasLog: !!state && !!this.runs.artifact(this.key, 'log.html'),
      hasFlowReport: !!state && !!this.runs.artifact(this.key, 'flow.html'),
    }) });
  }

  private onRun(): void {
    const state = this.runs.state(this.key);
    if (state && state.started !== this.runStarted) {
      // A new run of this file (it was saved first): its positions belong to the text as it is now.
      this.runStarted = state.started;
      this.runVersion = this.document.version;
    }
    const running = state?.status === 'running' || state?.status === 'stopping';
    if (running && !this.ticker) this.ticker = setInterval(() => this.send(), 1000);   // the elapsed time
    if (!running && this.ticker) { clearInterval(this.ticker); this.ticker = undefined; }
    this.send();
  }

  private async onMessage(msg: { type?: string; id?: number; change?: { op?: string }; target?: Record<string, unknown>;
                                 action?: string; name?: string; value?: string; node?: string }): Promise<void> {
    if (msg.type === 'ready') {
      this.ready = true;
      this.schedule(0);
    } else if (msg.type === 'edit' && msg.id !== undefined) {
      const result = await this.edit(msg.change || {});
      this.post({ type: 'result', id: msg.id, result });
    } else if (msg.type === 'reveal' && msg.target) {
      if (!msg.target.follow) await revealIn(this.document, msg.target);
    } else if (msg.type === 'toolbar') {
      await this.onToolbar(msg.action || '', msg.name, msg.value);
    } else if (msg.type === 'breakpoint' && typeof msg.node === 'string') {
      this.toggleBreakpoint(msg.node);
    }
  }

  private async onToolbar(action: string, name?: string, value?: string): Promise<void> {
    if (action === 'run') {
      await this.runs.runFile(this.document.uri);
    } else if (action === 'debug') {
      await vscode.commands.executeCommand('robotFlow.debug', this.document.uri);
    } else if (action === 'stop') {
      await this.runs.stop(this.key);
    } else if (action === 'step') {
      await this.runs.runFile(this.document.uri, { step: true });
    } else if (action === 'continue') {
      await this.runs.continueRun(this.key);
    } else if (action === 'pause' || action === 'resume') {
      await this.runs.control(this.key, action);
    } else if (action === 'log' || action === 'report' || action === 'flow') {
      await this.runs.open(this.key, `${action}.html`);
    } else if (action === 'output') {
      this.runs.output(this.key);
    } else if (action === 'option' && name && value !== undefined) {
      setOption(this.live, name, value);
      this.send();
    }
  }

  private async edit(change: { op?: string }): Promise<Answer> {
    if (!this.editable()) return { ok: false, error: 'This file cannot be changed here.' };
    if (change.op === 'undo') {
      const before = this.undo.pop();
      if (before === undefined) return { ok: false, error: 'Nothing to undo: the text changed since.' };
      return (await this.replaceText(before)) ? { ok: true } : { ok: false, error: 'The text could not be put back.' };
    }
    const env = await this.env();
    const text = this.document.getText();
    const answer = await editView(env, this.kind.id, this.document.uri.fsPath, text, change);
    if (!answer.ok || typeof answer.text !== 'string') {
      return { ok: false, error: answer.error || 'The change could not be made.', ...(answer.missing ? { missing: answer.missing } : {}) };
    }
    if (answer.text === text) return { ...answer, text: undefined, ok: true };
    this.undo.push(text);
    const applied = await this.replaceText(answer.text);
    if (!applied) {
      this.undo.pop();
      return { ok: false, error: 'The text could not be changed (is it read-only?).' };
    }
    return { ...answer, text: undefined, ok: true };
  }

  /** The whole text, as one edit: VS Code keeps it on its undo stack too. */
  private async replaceText(text: string): Promise<boolean> {
    const doc = this.document;
    const all = new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length));
    const edit = new vscode.WorkspaceEdit();
    edit.replace(doc.uri, all, text);
    const ok = await vscode.workspace.applyEdit(edit);
    if (ok) this.ours = doc.version;
    return ok;
  }
}

export class ViewEditorProvider implements vscode.CustomTextEditorProvider {
  private readonly sessions = new Set<Session>();

  constructor(
    private readonly kind: ViewKind,
    private readonly extension: vscode.ExtensionContext,
    private readonly log: vscode.OutputChannel,
    private readonly runs: RunManager,
  ) {}

  resolveCustomTextEditor(document: vscode.TextDocument, panel: vscode.WebviewPanel): void {
    panel.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extension.extensionUri, 'media')],
    };
    panel.webview.html = viewHtml(panel.webview, this.extension.extensionUri, this.kind.entry, this.kind.title);
    const session = new Session(this.kind, document, panel, this.extension, this.log, this.runs);
    this.sessions.add(session);
    panel.onDidDispose(() => { session.dispose(); this.sessions.delete(session); });
  }

  refreshAll(): void {
    this.sessions.forEach((s) => s.refreshNow());
  }

  /** The last read of an open view of `file`, or null. */
  reportFor(file: string): Session['report'] {
    for (const s of this.sessions) if (s.file.toLowerCase() === file.toLowerCase() && s.report) return s.report;
    return null;
  }
}
