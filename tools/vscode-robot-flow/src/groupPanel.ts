// The Diagram of a run group (flow-view/group.js): every member's flow as a
// column and where they wait for each other. Opened from testproject.json's
// groups; redrawn when a member's text changes; Run starts all members
// together and the marks follow each one.
import * as vscode from 'vscode';
import { inspectGroup } from './backend/group';
import { Project, RunGroup, memberPath, readProject } from './backend/project';
import { RunManager, groupKey } from './runs';
import { robotEnvFor } from './settings';
import { LiveOptions, revealIn, setOption, toolbar } from './shared';
import { robotOf } from './viewEditor';
import { diagramUnavailable } from './backend/robot';
import { viewHtml } from './webview';

export class GroupPanel implements vscode.Disposable {
  private static readonly open = new Map<string, GroupPanel>();
  private readonly subs: vscode.Disposable[] = [];
  private readonly key: string;
  private base: Record<string, unknown> | null = null;
  private status: { kind?: string; text: string } | null = null;
  private ready = false;
  private timer: NodeJS.Timeout | undefined;
  private ticker: NodeJS.Timeout | undefined;
  private reading = 0;
  private readonly live: LiveOptions = { zoom: 'fit', motion: 'tail' };
  /** The last read (for the smoke test). */
  report: { ok: boolean; error?: string; drawn: boolean; links?: number } | null = null;

  /** Show a group's Diagram (one panel per group). */
  static show(extension: vscode.ExtensionContext, runs: RunManager, project: Project, group: RunGroup): GroupPanel {
    const key = groupKey(project, group);
    const existing = GroupPanel.open.get(key);
    if (existing) {
      existing.panel.reveal();
      return existing;
    }
    const panel = vscode.window.createWebviewPanel('robotFlow.group', `Group: ${group.title}`, vscode.ViewColumn.Active, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(extension.extensionUri, 'media')],
    });
    const p = new GroupPanel(extension, runs, project, group, panel);
    GroupPanel.open.set(key, p);
    return p;
  }

  static reportFor(project: Project, group: RunGroup): GroupPanel['report'] {
    return GroupPanel.open.get(groupKey(project, group))?.report ?? null;
  }

  private constructor(
    private readonly extension: vscode.ExtensionContext,
    private readonly runs: RunManager,
    private project: Project,
    private group: RunGroup,
    private readonly panel: vscode.WebviewPanel,
  ) {
    this.key = groupKey(project, group);
    panel.iconPath = new vscode.ThemeIcon('type-hierarchy');
    panel.webview.html = viewHtml(panel.webview, extension.extensionUri, 'flow-view/group.js', group.title);
    const members = () => new Set(this.group.members.map((m) => memberPath(this.project, m).toLowerCase()));
    this.subs.push(
      panel.webview.onDidReceiveMessage((msg) => this.onMessage(msg)),
      panel.onDidDispose(() => this.dispose()),
      vscode.workspace.onDidChangeTextDocument((e) => {
        if (e.contentChanges.length && members().has(e.document.uri.fsPath.toLowerCase())) this.schedule(400);
      }),
      vscode.workspace.onDidSaveTextDocument((d) => {
        // testproject.json itself: the group may have changed
        if (d.uri.fsPath.toLowerCase().endsWith('testproject.json') &&
            d.uri.fsPath.toLowerCase().startsWith(this.project.root.toLowerCase())) this.reloadProject();
      }),
      runs.onDidChange((key) => { if (key === this.key) this.onRun(); }),
    );
  }

  dispose(): void {
    GroupPanel.open.delete(this.key);
    clearTimeout(this.timer);
    clearInterval(this.ticker);
    this.subs.forEach((s) => s.dispose());
  }

  private reloadProject(): void {
    const fresh = readProject(`${this.project.root}/testproject.json`);
    const group = fresh?.groups.find((g) => g.id === this.group.id);
    if (fresh && group) {
      this.project = fresh;
      this.group = group;
      this.panel.title = `Group: ${group.title}`;
      this.schedule(0);
    }
  }

  private schedule(ms: number): void {
    if (!this.ready) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.refresh(); }, ms);
  }

  private async refresh(): Promise<void> {
    const n = ++this.reading;
    const first = vscode.Uri.file(memberPath(this.project, this.group.members[0]));
    const env = await robotEnvFor(first, this.extension.extensionPath);
    const why = diagramUnavailable(await robotOf(env));
    if (why) {
      this.base = null;
      this.status = { kind: 'error', text: why };
      this.report = { ok: false, error: why, drawn: false };
      this.send();
      return;
    }
    const open = new Map(vscode.workspace.textDocuments.map((d) => [d.uri.fsPath.toLowerCase(), d.getText()]));
    const answer = await inspectGroup(env, this.project, this.group, (f) => open.get(f.toLowerCase()));
    if (n !== this.reading) return;
    if (answer.ok) {
      this.base = { members: answer.members, links: answer.links };
      this.status = null;
    } else {
      this.status = { kind: 'error', text: String(answer.error) + (this.base ? '\nShowing the last version that could be read.' : '') };
    }
    this.report = { ok: answer.ok, error: answer.error, drawn: !!this.base,
                    links: Array.isArray(answer.links) ? answer.links.length : undefined };
    this.send();
  }

  private send(): void {
    const state = this.runs.state(this.key);
    const live = state && Object.values(state.positions).some(Boolean) ? state.positions : undefined;
    const selection = this.base
      ? (live ? { ...this.base, live, zoom: this.live.zoom, motion: this.live.motion } : this.base)
      : null;
    void this.panel.webview.postMessage({ type: 'selection', selection, status: this.status });
    void this.panel.webview.postMessage({ type: 'toolbar', toolbar: toolbar(state, {
      ...this.live,
      canRun: true,
      runTitle: `Run all ${this.group.members.length} members together (saves them first)`,
      live: !!live,
      hasLog: !!state && !!this.runs.artifact(this.key, 'log.html'),
    }) });
  }

  private onRun(): void {
    const s = this.runs.state(this.key)?.status;
    const running = s === 'running' || s === 'stopping';
    if (running && !this.ticker) this.ticker = setInterval(() => this.send(), 1000);
    if (!running && this.ticker) { clearInterval(this.ticker); this.ticker = undefined; }
    this.send();
  }

  private async onMessage(msg: { type?: string; target?: Record<string, unknown>; action?: string; name?: string; value?: string }): Promise<void> {
    if (msg.type === 'ready') {
      this.ready = true;
      this.schedule(0);
    } else if (msg.type === 'reveal' && msg.target && !msg.target.follow) {
      const member = this.group.members.find((m) => m.id === msg.target!.member);
      if (!member) return;
      const doc = await vscode.workspace.openTextDocument(memberPath(this.project, member));
      await revealIn(doc, { node: msg.target.node });
    } else if (msg.type === 'toolbar') {
      if (msg.action === 'run') await this.runs.runGroup(this.project, this.group);
      else if (msg.action === 'stop') await this.runs.stop(this.key);
      else if (msg.action && /^(pause|resume)(:|$)/.test(msg.action)) {
        // 'pause' / 'resume': every member; 'pause:<member>': that one alone.
        const [command, member] = msg.action.split(/:(.*)/s) as ['pause' | 'resume', string?];
        await this.runs.control(this.key, command, member || '');
      }
      else if (msg.action === 'log' || msg.action === 'report') await this.runs.open(this.key, `${msg.action}.html`);
      else if (msg.action === 'output') this.runs.output(this.key);
      else if (msg.action === 'option' && msg.name && msg.value !== undefined) {
        setOption(this.live, msg.name, msg.value);
        this.send();
      }
    }
  }
}
