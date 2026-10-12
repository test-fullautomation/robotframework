// The Run page: one run of the Runs view as a tab in the editor area. The
// header (status, time, duration, variables, the target), the buttons (Log,
// Report, Flow report, Continue, Output, folder; Pause / Resume / Stop while
// it runs), the phases or tests as a table with time bars and the failure
// messages in full, and for a flow run the flow report itself, shown in a
// frame (it is one self-contained HTML file). A group run has one tab per
// member. The page follows the run: it is redrawn when the folder or the
// run's state changes.
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { describeFlow } from './backend/control';
import { Counts, HistoryStatus, OutputDetail, RunRecord, TestSummary, formatDuration, readOutputDetail } from './backend/history';
import { reportForFrame, runPageHtml } from './backend/runPageHtml';
import { RunManager, RunState } from './runs';
import { robotEnvFor } from './settings';

/** Who finds a run again and writes its flow report: the Runs view provider. */
export interface RunSource {
  lookup(dir: string): { run: RunRecord; state: RunState | null } | null;
  flowReportOf(run: RunRecord): Promise<string | null>;
  onDidChangeTreeData: vscode.Event<unknown>;
}

interface PageMember {
  id: string;
  title: string;
  status: HistoryStatus;
  duration: string;
  total: Counts | null;
  tests: TestSummary[] | null;
  /** Why there are no tests (yet). */
  note: string | null;
  /** The flow report's file and version (its size and time); null without one. */
  reportKey: string | null;
  /** The report's HTML for the page's frame, sent when it changed since the last time. */
  reportHtml?: string;
  /** A flow run without a report yet: it can be written. */
  canReport: boolean;
  flow: boolean;
}

interface PageData {
  title: string;
  status: HistoryStatus;
  when: string;
  duration: string;
  target: string | null;
  variables: [string, string][];
  dir: string;
  live: string | null;
  running: boolean;
  pausable: boolean;
  paused: boolean;
  files: { log: boolean; report: boolean; output: boolean };
  checkpoints: { name: string; file: string; kind: string }[];
  group: boolean;
  members: PageMember[];
}

const DETAIL_EVERY_MS = 10000;

function nonce(): string {
  return Array.from({ length: 24 }, () => 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(Math.random() * 36)]).join('');
}

export class RunPage implements vscode.Disposable {
  private static pages = new Map<string, RunPage>();

  static show(context: vscode.ExtensionContext, source: RunSource, runs: RunManager, dir: string): RunPage | null {
    const key = path.resolve(dir).toLowerCase();
    const open = RunPage.pages.get(key);
    if (open) {
      open.panel.reveal();
      return open;
    }
    const found = source.lookup(dir);
    if (!found) return null;
    const page = new RunPage(context, source, runs, found.run);
    RunPage.pages.set(key, page);
    return page;
  }

  private readonly panel: vscode.WebviewPanel;
  private readonly subscriptions: vscode.Disposable[] = [];
  private readonly details = new Map<string, { signature: string; at: number; detail: OutputDetail }>();
  private timer: NodeJS.Timeout | null = null;
  private record: RunRecord;
  private state: RunState | null = null;
  private pending = new Set<string>();
  private readonly nonce = nonce();
  /** The report versions the page has (by member id). */
  private readonly sentReports = new Map<string, string>();
  /** The last data sent (tests and diagnostics). */
  data: PageData | null = null;
  /** What the page said it shows in each report frame: the version and the document's length. */
  readonly reportShown: Record<string, { key: string; length: number }> = {};
  /** Errors the page's script reported (diagnostics). */
  readonly errors: string[] = [];

  /** The open page of a run folder, or null. */
  static get(dir: string): RunPage | null {
    return RunPage.pages.get(path.resolve(dir).toLowerCase()) ?? null;
  }

  private constructor(private readonly context: vscode.ExtensionContext, private readonly source: RunSource,
                      private readonly runs: RunManager, record: RunRecord) {
    this.record = record;
    this.panel = vscode.window.createWebviewPanel('robotFlow.runPage', `Run: ${record.title}`, vscode.ViewColumn.Active, {
      enableScripts: true, retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.file(record.dir), vscode.Uri.file(path.join(context.extensionPath, 'media'))],
    });
    this.panel.webview.html = this.html();
    this.subscriptions.push(
      this.panel.webview.onDidReceiveMessage((m) => this.onMessage(m)),
      source.onDidChangeTreeData(() => this.refresh()),
      this.panel.onDidDispose(() => this.dispose()),
    );
    this.refresh(0);
  }

  dispose(): void {
    RunPage.pages.delete(path.resolve(this.record.dir).toLowerCase());
    if (this.timer) clearTimeout(this.timer);
    this.subscriptions.forEach((s) => s.dispose());
    this.panel.dispose();
  }

  refresh(delayMs = 500): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.send();
    }, delayMs);
  }

  private async send(): Promise<void> {
    const found = this.source.lookup(this.record.dir);
    if (found) {
      this.record = found.run;
      this.state = found.state;
    }
    const data = this.pageData();
    this.data = data;
    await this.panel.webview.postMessage({ type: 'data', data });
    this.panel.title = `Run: ${this.record.title}`;
    // The phases: read when the output changed (every 10 s at most while it grows).
    for (const member of this.record.kind === 'group' ? this.record.members : [this.record]) void this.loadDetail(member);
  }

  private async loadDetail(member: RunRecord): Promise<void> {
    const output = member.files.output;
    if (!output || this.pending.has(output)) return;
    let signature: string;
    try {
      const st = fs.statSync(output);
      signature = `${st.mtimeMs}:${st.size}`;
    } catch {
      return;
    }
    const hit = this.details.get(output);
    if (hit && (hit.signature === signature || (hit.detail.partial && Date.now() - hit.at < DETAIL_EVERY_MS))) return;
    this.pending.add(output);
    try {
      const env = await robotEnvFor(vscode.Uri.file(member.target && fs.existsSync(member.target) ? member.target : output), this.context.extensionPath);
      const detail = await readOutputDetail(env, output);
      this.details.set(output, { signature, at: Date.now(), detail });
    } finally {
      this.pending.delete(output);
    }
    const data = this.pageData();
    this.data = data;
    await this.panel.webview.postMessage({ type: 'data', data });
  }

  private pageData(): PageData {
    const run = this.record;
    const state = this.state;
    const running = run.status === 'running' || run.status === 'stopping';
    const flow = state && running && state.pausable ? describeFlow(state.flow) : null;
    const live = running ? (flow ? flow.text : state?.positions.main?.node ? `step ${state.positions.main.node}` : run.status) : null;
    const started = state?.started ?? run.started;
    const members = (run.kind === 'group' ? run.members : [run]).map((m): PageMember => {
      const hit = m.files.output ? this.details.get(m.files.output) : null;
      const detail = hit?.detail ?? null;
      const flowRun = m.kind === 'flow';
      const member: PageMember = {
        id: m.name, title: m.title, status: m.status, duration: formatDuration(m.elapsed), total: m.total,
        tests: detail?.ok ? detail.tests || [] : null,
        note: !m.files.output ? (running ? 'Nothing finished yet.' : 'No output.xml.') : detail ? (detail.ok ? (detail.partial && !(detail.tests || []).length ? 'Nothing finished yet.' : null) : detail.error || 'The results could not be read.') : 'Reading the phases…',
        reportKey: null,
        canReport: flowRun && !m.files.flow && !!m.files.output && !running,
        flow: flowRun,
      };
      // The report goes to the page as text (a frame cannot load a file of the run's folder): once per version.
      if (m.files.flow) {
        try {
          const st = fs.statSync(m.files.flow);
          member.reportKey = `${m.files.flow}|${st.size}|${st.mtimeMs}`;
          if (this.sentReports.get(m.name) !== member.reportKey) {
            member.reportHtml = reportForFrame(fs.readFileSync(m.files.flow, 'utf8'), this.nonce);
            this.sentReports.set(m.name, member.reportKey);
          }
        } catch {
          member.reportKey = null;   // being written
        }
      }
      return member;
    });
    return {
      title: run.title, status: run.status, when: started ? new Date(started).toLocaleString() : '',
      duration: formatDuration(running && started ? (Date.now() - started) / 1000 : run.elapsed),
      target: run.target, variables: Object.entries(run.info?.variables || {}), dir: run.dir, live, running,
      pausable: !!state && running && state.pausable, paused: !!flow && flow.paused > 0,
      files: { log: !!run.files.log, report: !!run.files.report, output: !!state },
      checkpoints: run.checkpoints.map((c) => ({ name: path.basename(c.file), file: c.file, kind: c.kind === 'snapshot' ? `snapshot "${c.name}"` : 'checkpoint' })),
      group: run.kind === 'group', members,
    };
  }

  private async onMessage(m: { type: string; what?: string; file?: string; member?: string; command?: string; key?: string; length?: number; href?: string }): Promise<void> {
    const run = this.record;
    const key = this.state?.key;
    switch (m.type) {
      case 'ready':
        this.sentReports.clear();   // the page starts afresh (a reload): send the reports again
        this.refresh(0);
        return;
      case 'reportShown':
        if (m.member) this.reportShown[m.member] = { key: String(m.key || ''), length: Number(m.length || 0) };
        return;
      case 'pageError':
        this.errors.push(String(m.what || 'error'));
        return;
      case 'frameLink': {
        // "log.html#s1-t1-k2" in a report: the file next to that report, in the browser
        const member = run.kind === 'group' ? run.members.find((x) => x.name === m.member) : run;
        const href = String(m.href || '');
        if (!member || !href || /^[a-z]+:/i.test(href)) {
          if (/^https?:/i.test(href)) void vscode.env.openExternal(vscode.Uri.parse(href));
          return;
        }
        const [file, fragment] = href.split('#');
        const target = path.resolve(member.dir, file || 'log.html');
        if (fs.existsSync(target)) void vscode.env.openExternal(vscode.Uri.file(target).with({ fragment: fragment || '' }));
        return;
      }
      case 'openReport': {
        const member = run.kind === 'group' ? run.members.find((x) => x.name === m.member) : run;
        if (member?.files.flow) void vscode.env.openExternal(vscode.Uri.file(member.files.flow));
        return;
      }
      case 'open':
        if (m.what === 'log' && run.files.log) return void vscode.env.openExternal(vscode.Uri.file(run.files.log));
        if (m.what === 'report' && run.files.report) return void vscode.env.openExternal(vscode.Uri.file(run.files.report));
        if (m.what === 'folder') return void vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(run.dir));
        if (m.what === 'target' && run.target) return void vscode.commands.executeCommand('vscode.open', vscode.Uri.file(run.target));
        if (m.what === 'file' && m.file) return void vscode.commands.executeCommand('vscode.open', vscode.Uri.file(m.file));
        if (m.what === 'output' && key) return this.runs.output(key);
        if (m.what === 'external' && m.file) return void vscode.env.openExternal(vscode.Uri.file(m.file));
        return;
      case 'continue': {
        const c = run.checkpoints.find((x) => x.file === m.file);
        const target = [c?.flow, run.target].find((t) => t && /\.flow\.json$/i.test(t) && fs.existsSync(t));
        if (!c || !target) {
          void vscode.window.showInformationMessage('The checkpoint names no flow file that exists: nothing to continue.');
          return;
        }
        const variables = { ...(run.info?.variables || {}) };
        delete variables.FLOW_CHECKPOINT;
        await this.runs.continueFrom(target, c.file, variables);
        return;
      }
      case 'flowReport': {
        const member = run.kind === 'group' ? run.members.find((x) => x.name === m.member) : run;
        if (!member) return;
        await this.source.flowReportOf(member);
        this.refresh(0);
        return;
      }
      case 'control':
        if (!key) return;
        if (m.command === 'stop') await this.runs.stop(key);
        else if (m.command === 'pause' || m.command === 'resume') await this.runs.control(key, m.command);
        else if (m.command === 'snapshot') await this.runs.snapshot(key);
        this.refresh(0);
        return;
    }
  }

  private html(): string {
    const webview = this.panel.webview;
    const n = this.nonce;
    // The report frames are srcdoc documents: they inherit this policy, so their script carries the nonce.
    const csp = `default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${n}'; frame-src ${webview.cspSource} about:; img-src ${webview.cspSource} data:;`;
    return runPageHtml(csp, n);
  }
}

