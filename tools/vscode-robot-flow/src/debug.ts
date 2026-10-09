// The "robotflow" debugger: Debug a suite or a flow like Run, with breakpoints
// (in the text of .robot / .resource / .flow.json files and on the Diagram's
// steps), stepping, variables and the Debug Console. The adapter runs inline
// (backend/debugSession.ts); the run itself is the RunManager's, so the views
// follow it live as they do any run.
//
// Python: the run also starts debugpy, and VS Code's Python debugger (the
// ms-python.debugpy extension, type "debugpy") attaches to it as a child
// session -- breakpoints in .py files work, and Step Into a keyword of the
// user's Python library stops in its function (PythonSide below).
import * as path from 'node:path';
import * as vscode from 'vscode';
import { DebugSession, LaunchArgs, RunHandle } from './backend/debugSession';
import { RunManager, fileKey } from './runs';

export const DEBUG_TYPE = 'robotflow';
const PYTHON_TYPE = 'debugpy';
const PYTHON_EXTENSION = 'ms-python.debugpy';

/**
 * The Python debugger of one Robot debug session: the child session attached
 * to debugpy in the Robot process, and the one-time breakpoint of Step Into.
 */
class PythonSide {
  session: vscode.DebugSession | undefined;
  /** A one-time breakpoint not reached yet: its file, and the user's breakpoints there. */
  private once: { file: string; line: number; user: Record<string, unknown>[] } | null = null;
  static readonly all = new Set<PythonSide>();

  constructor(readonly parent: vscode.DebugSession, private readonly helpersDir: string) {
    PythonSide.all.add(this);
  }

  dispose(): void {
    PythonSide.all.delete(this);
  }

  /** Attach VS Code's Python debugger to debugpy on `port`, as a child of the Robot session. */
  async attach(port: number, justMyCode: boolean): Promise<boolean> {
    if (!vscode.extensions.getExtension(PYTHON_EXTENSION)) return false;
    const started = new Promise<void>((resolve) => {
      const sub = vscode.debug.onDidStartDebugSession((s) => {
        if (s.parentSession === this.parent && s.type === PYTHON_TYPE) {
          this.session = s;
          sub.dispose();
          resolve();
        }
      });
      setTimeout(() => { sub.dispose(); resolve(); }, 30000);
    });
    const ok = await vscode.debug.startDebugging(this.parent.workspaceFolder, {
      type: PYTHON_TYPE, request: 'attach', name: `Python: ${this.parent.name}`,
      connect: { host: '127.0.0.1', port }, justMyCode, subProcess: false,
      // Robot Framework's own code and these helpers are never stepped into.
      rules: [{ module: 'robot', include: false }, { path: this.helpersDir, include: false }],
    }, { parentSession: this.parent, lifecycleManagedByParent: true, compact: true });
    if (ok) await started;
    return ok && !!this.session;
  }

  /** The user's breakpoints in `file`, as setBreakpoints wants them. */
  private userBreakpoints(file: string): Record<string, unknown>[] {
    return vscode.debug.breakpoints
      .filter((b): b is vscode.SourceBreakpoint => b instanceof vscode.SourceBreakpoint && b.enabled &&
              b.location.uri.fsPath.toLowerCase() === file.toLowerCase())
      .map((b) => ({ line: b.location.range.start.line + 1, condition: b.condition,
                     hitCondition: b.hitCondition, logMessage: b.logMessage }));
  }

  /** Break once at `file`:`line`: the user's breakpoints there plus this one. */
  async breakOnce(file: string, line: number): Promise<boolean> {
    if (!this.session) return false;
    const user = this.userBreakpoints(file);
    try {
      await this.session.customRequest('setBreakpoints', { source: { path: file }, breakpoints: [...user, { line }] });
    } catch {
      return false;
    }
    this.once = { file, line, user };
    return true;
  }

  /** The one-time breakpoint has done its job (or the run moved on): the user's again. */
  clearOnce(): void {
    const once = this.once;
    this.once = null;
    if (!once || !this.session) return;
    void Promise.resolve(this.session.customRequest('setBreakpoints', {
      source: { path: once.file }, breakpoints: this.userBreakpoints(once.file),
    })).catch(() => undefined);
  }

  /** The Python session stopped: wherever that is, the one-time breakpoint goes. */
  static stopped(session: vscode.DebugSession): void {
    for (const side of PythonSide.all) if (side.session && side.session.id === session.id) side.clearOnce();
  }
}

/** Where a debugpy can be imported from when the run's interpreter has none: the Python Debugger extension's. */
function bundledDebugpy(): string | undefined {
  const ext = vscode.extensions.getExtension(PYTHON_EXTENSION);
  return ext ? path.join(ext.extensionPath, 'bundled', 'libs') : undefined;
}

class InlineAdapter implements vscode.DebugAdapter {
  private readonly sent = new vscode.EventEmitter<vscode.DebugProtocolMessage>();
  readonly onDidSendMessage = this.sent.event;

  constructor(private readonly session: DebugSession) {
    session.on('message', (m) => this.sent.fire(m));
  }

  handleMessage(message: vscode.DebugProtocolMessage): void {
    this.session.handleMessage(message as Record<string, unknown>);
  }

  dispose(): void {
    this.session.dispose();
    this.sent.dispose();
  }
}

/** The file to debug when nothing says which: the active editor's (text, Grid or Diagram). */
function activeFile(): vscode.Uri | undefined {
  const editor = vscode.window.activeTextEditor?.document.uri;
  if (editor) return editor;
  const tab = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
  if (tab instanceof vscode.TabInputCustom || tab instanceof vscode.TabInputText) return tab.uri;
  return undefined;
}

function debuggable(file: string): boolean {
  return /\.(robot|flow\.json)$/i.test(file);
}

export function registerDebugger(context: vscode.ExtensionContext, runs: RunManager): void {
  const helpersDir = path.join(context.extensionPath, 'python');

  context.subscriptions.push(vscode.debug.registerDebugConfigurationProvider(DEBUG_TYPE, {
    provideDebugConfigurations() {
      return [{ type: DEBUG_TYPE, request: 'launch', name: 'Debug the open suite or flow', target: '${file}' }];
    },
    resolveDebugConfiguration(_folder, config) {
      // F5 without a launch.json: the active suite or flow.
      if (!config.type && !config.request) {
        const file = activeFile();
        if (!file || !debuggable(file.fsPath)) {
          void vscode.window.showInformationMessage('Open a .robot suite or a .flow.json file to debug it.');
          return undefined;
        }
        return { type: DEBUG_TYPE, request: 'launch', name: `Debug ${path.basename(file.fsPath)}`, target: file.fsPath };
      }
      if (!config.target) {
        const file = activeFile();
        if (file) config.target = file.fsPath;
      }
      return config;
    },
  }));

  // A Python child session that stops ends a pending one-time breakpoint.
  context.subscriptions.push(vscode.debug.registerDebugAdapterTrackerFactory(PYTHON_TYPE, {
    createDebugAdapterTracker(session) {
      return {
        onDidSendMessage(m: { type?: string; event?: string }) {
          if (m.type === 'event' && m.event === 'stopped') PythonSide.stopped(session);
        },
      };
    },
  }));

  context.subscriptions.push(vscode.debug.registerDebugAdapterDescriptorFactory(DEBUG_TYPE, {
    createDebugAdapterDescriptor(parent: vscode.DebugSession) {
      let key = '';
      const python = new PythonSide(parent, helpersDir);
      const session = new DebugSession(async (args: LaunchArgs, extra): Promise<RunHandle> => {
        const uri = vscode.Uri.file(path.resolve(args.target));
        key = fileKey(uri.fsPath);
        if (runs.isRunning(key)) throw new Error(`${path.basename(uri.fsPath)} is running already.`);
        const state = await runs.runFile(uri, {
          robotArgs: extra.robotArgs, env: extra.env, variables: args.variables, args: args.args, debug: true,
        });
        const run = state && runs.process(key);
        if (!run) throw new Error(`${path.basename(uri.fsPath)} could not be started.`);
        return { on: (event: string, fn: (...a: unknown[]) => void) => run.on(event, fn), stop: () => runs.stop(key) } as RunHandle;
      }, helpersDir, {
        paused: (_target, node, reason) => runs.setPaused(key, reason ? { node, reason } : null),
        text: (file) => vscode.workspace.textDocuments.find((d) => d.uri.fsPath.toLowerCase() === file.toLowerCase())?.getText() ?? null,
        debugpyPath: bundledDebugpy(),
        attachPython: (port, opts) => python.attach(port, opts.justMyCode),
        breakInPython: (file, line) => python.breakOnce(file, line),
        robotStopped: () => python.clearOnce(),
      });
      session.once('dispose', () => python.dispose());
      return new vscode.DebugAdapterInlineImplementation(new InlineAdapter(session));
    },
  }));

  context.subscriptions.push(vscode.commands.registerCommand('robotFlow.debug', async (arg?: vscode.Uri) => {
    const uri = arg instanceof vscode.Uri ? arg : activeFile();
    if (!uri || !debuggable(uri.fsPath)) {
      void vscode.window.showInformationMessage('Open a .robot suite or a .flow.json file to debug it.');
      return;
    }
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.fsPath === uri.fsPath);
    if (doc?.isDirty) await doc.save();
    await vscode.debug.startDebugging(vscode.workspace.getWorkspaceFolder(uri), {
      type: DEBUG_TYPE, request: 'launch', name: `Debug ${path.basename(uri.fsPath)}`, target: uri.fsPath,
    });
  }));
}
