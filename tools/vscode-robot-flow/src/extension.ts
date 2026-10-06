// Robot Framework Grid & Flow: the Manager GUI's Grid and Diagram views as
// VS Code editors of .robot / .resource and *.flow.json files, runs with a
// live position, the Diagram of run groups, and Go to Definition in the text.
import * as path from 'node:path';
import * as vscode from 'vscode';
import { describeFlow } from './backend/control';
import { PROJECT_FILE, Project, RunGroup, readProject } from './backend/project';
import { registerDebugger } from './debug';
import { registerDefinitions } from './definitions';
import { diagramUnavailable, probeRobot } from './backend/robot';
import { GroupPanel } from './groupPanel';
import { RunManager, RunState, fileKey, groupKey } from './runs';
import { forgetInterpreters, robotEnvFor } from './settings';
import { VIEWS, ViewEditorProvider, forgetRobots } from './viewEditor';

/** What activate() returns: read-only, for tests and diagnostics. */
export interface RobotFlowApi {
  /** The last read of an open Grid / Diagram of `file`: { ok, error?, drawn }, or null. */
  report(viewType: string, file: string): { ok: boolean; error?: string; drawn: boolean } | null;
  /** The last read of an open group Diagram. */
  groupReport(projectFile: string, groupId: string): GroupPanel['report'];
  /** The state of the last run of a file, or of a group ('<testproject.json>#<id>'). */
  runState(fileOrGroup: string): RunState | null;
  /** Run a file, or open a group's Diagram / run it -- as the toolbar does. */
  run(file: string): Promise<RunState | null>;
  /** Pause / resume a file's flow run, or stop it (as the toolbar does). */
  control(file: string, command: 'pause' | 'resume' | 'stop'): Promise<boolean>;
  openGroup(projectFile: string, groupId: string): boolean;
  runGroup(projectFile: string, groupId: string): Promise<RunState | null>;
}

/** The file of the active editor: a text editor or one of our custom editors. */
function activeFile(): vscode.Uri | undefined {
  const tab = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
  if (tab instanceof vscode.TabInputCustom || tab instanceof vscode.TabInputText) return tab.uri;
  return vscode.window.activeTextEditor?.document.uri;
}

function findGroup(projectFile: string, groupId: string): { project: Project; group: RunGroup } | null {
  const project = readProject(projectFile);
  const group = project?.groups.find((g) => g.id === groupId);
  return project && group ? { project, group } : null;
}

/** Pick a run group from the workspace's testproject.json files. */
async function pickGroup(): Promise<{ project: Project; group: RunGroup } | null> {
  const files = await vscode.workspace.findFiles(`**/${PROJECT_FILE}`, '**/{node_modules,results,.git}/**', 50);
  const items: (vscode.QuickPickItem & { project: Project; group: RunGroup })[] = [];
  for (const f of files) {
    const project = readProject(f.fsPath);
    for (const group of project?.groups || []) {
      items.push({
        label: group.title,
        description: group.members.map((m) => m.id).join(' + '),
        detail: `${project!.name} · ${vscode.workspace.asRelativePath(f)}`,
        project: project!, group,
      });
    }
  }
  if (!items.length) {
    void vscode.window.showInformationMessage(`No run groups: none of the workspace's ${PROJECT_FILE} files has "groups".`);
    return null;
  }
  return (await vscode.window.showQuickPick(items, { placeHolder: 'Run group to open', matchOnDescription: true, matchOnDetail: true })) ?? null;
}

export function activate(context: vscode.ExtensionContext): RobotFlowApi {
  const log = vscode.window.createOutputChannel('Robot Flow');
  const runs = new RunManager(context);
  registerDefinitions(context);
  registerDebugger(context, runs);
  const providers = Object.values(VIEWS).map((kind) => {
    const provider = new ViewEditorProvider(kind, context, log, runs);
    context.subscriptions.push(vscode.window.registerCustomEditorProvider(kind.viewType, provider, {
      webviewOptions: { retainContextWhenHidden: true },   // keep the drawing and its scroll position
      supportsMultipleEditorsPerDocument: true,
    }));
    return provider;
  });

  const openBeside = (viewType: string) => async (uri?: vscode.Uri) => {
    const target = uri instanceof vscode.Uri ? uri : activeFile();
    if (!target) {
      void vscode.window.showInformationMessage('Open a Robot Framework file first.');
      return;
    }
    await vscode.commands.executeCommand('vscode.openWith', target, viewType, vscode.ViewColumn.Beside);
  };

  /** The run of a file (the active one when none is given). */
  const runOf = (uri?: vscode.Uri): RunState | null => {
    const target = uri instanceof vscode.Uri ? uri : activeFile();
    return target ? runs.state(fileKey(target.fsPath)) : null;
  };
  const flowCommand = async (uri: vscode.Uri | undefined, command: 'pause' | 'resume') => {
    const state = runOf(uri);
    if (!state || state.status !== 'running') {
      void vscode.window.showInformationMessage('No flow of the active file is running.');
      return false;
    }
    return runs.control(state.key, command);
  };

  // The active file's flow run in the status bar: where it is; a click pauses or resumes it.
  const statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusItem.command = 'robotFlow.togglePause';
  const showStatus = () => {
    const state = runOf();
    const flow = state && state.status === 'running' && state.pausable ? describeFlow(state.flow) : null;
    if (!state || !flow) { statusItem.hide(); return; }
    const paused = flow.paused > 0;
    statusItem.text = `${paused ? '$(debug-pause)' : '$(pulse)'} ${state.title}: ${flow.text}`;
    statusItem.tooltip = paused ? (state.step ? 'Click: run the next step' : 'Click: resume the flow') : 'Click: pause the flow at its next step';
    statusItem.show();
  };

  const runTarget = async (uri?: vscode.Uri) => {
    const target = uri instanceof vscode.Uri ? uri : activeFile();
    if (!target || !/\.(robot|flow\.json)$/i.test(target.fsPath)) {
      void vscode.window.showInformationMessage('Open a .robot suite or a .flow.json file to run it.');
      return null;
    }
    return runs.runFile(target);
  };

  context.subscriptions.push(
    log,
    runs,
    vscode.commands.registerCommand('robotFlow.openGrid', openBeside(VIEWS.grid.viewType)),
    vscode.commands.registerCommand('robotFlow.openDiagram', openBeside(VIEWS.diagram.viewType)),
    vscode.commands.registerCommand('robotFlow.run', runTarget),
    vscode.commands.registerCommand('robotFlow.stop', async (uri?: vscode.Uri) => {
      const target = uri instanceof vscode.Uri ? uri : activeFile();
      if (target) await runs.stop(fileKey(target.fsPath));
    }),
    vscode.commands.registerCommand('robotFlow.runStep', async (uri?: vscode.Uri) => {
      const target = uri instanceof vscode.Uri ? uri : activeFile();
      if (!target || !/\.flow\.json$/i.test(target.fsPath)) {
        void vscode.window.showInformationMessage('Step mode is for flow files: open a .flow.json file.');
        return null;
      }
      return runs.runFile(target, { step: true });
    }),
    vscode.commands.registerCommand('robotFlow.pause', (uri?: vscode.Uri) => flowCommand(uri, 'pause')),
    vscode.commands.registerCommand('robotFlow.resume', (uri?: vscode.Uri) => flowCommand(uri, 'resume')),
    vscode.commands.registerCommand('robotFlow.togglePause', (uri?: vscode.Uri) => {
      const state = runOf(uri);
      return flowCommand(uri, state && describeFlow(state.flow)?.paused ? 'resume' : 'pause');
    }),
    vscode.commands.registerCommand('robotFlow.continue', async (uri?: vscode.Uri) => {
      const target = uri instanceof vscode.Uri ? uri : activeFile();
      if (!target) return null;
      if (!runs.state(fileKey(target.fsPath))) {
        void vscode.window.showInformationMessage(`No run of ${path.basename(target.fsPath)} in this window to continue.`);
        return null;
      }
      return runs.continueRun(fileKey(target.fsPath));
    }),
    statusItem,
    runs.onDidChange(() => showStatus()),
    vscode.window.tabGroups.onDidChangeTabs(() => showStatus()),
    vscode.window.onDidChangeActiveTextEditor(() => showStatus()),
    vscode.commands.registerCommand('robotFlow.openGroup', async () => {
      const picked = await pickGroup();
      if (picked) GroupPanel.show(context, runs, picked.project, picked.group);
    }),
    vscode.commands.registerCommand('robotFlow.showRobot', async () => {
      const uri = activeFile() ?? vscode.workspace.workspaceFolders?.[0]?.uri;
      if (!uri) {
        void vscode.window.showInformationMessage('Open a folder or a file first.');
        return;
      }
      const env = await robotEnvFor(uri, context.extensionPath);
      const info = await probeRobot(env);
      log.appendLine(`python: ${env.python}`);
      log.appendLine(`robotSource: ${env.robotSource || '(installed Robot Framework)'}`);
      log.appendLine(`pythonPath: ${env.pythonPath.join(', ') || '(none)'}`);
      log.appendLine(`working folder: ${env.cwd}`);
      log.appendLine(`answer: ${JSON.stringify(info)}`);
      if (!info.ok) {
        void vscode.window.showErrorMessage(`Robot Framework not found with ${env.python}: ${info.error}`);
        return;
      }
      const diagram = diagramUnavailable(info);
      void vscode.window.showInformationMessage(
        `Robot Framework ${info.version} (Python ${info.python}) from ${info.location}. ` +
        `Grid: yes${info.thread ? ' (with THREAD)' : ''}. Diagram: ${diagram ? 'no' : 'yes'}.`);
      if (diagram) log.appendLine(`Diagram: ${diagram}`);
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (!e.affectsConfiguration('robotFlow')) return;
      forgetInterpreters();
      forgetRobots();
      providers.forEach((p) => p.refreshAll());
    }),
  );

  const byType = new Map(Object.values(VIEWS).map((kind, i) => [kind.viewType, providers[i]]));
  const stateOf = (fileOrGroup: string) => {
    const hash = fileOrGroup.lastIndexOf('#');
    if (hash > 0 && fileOrGroup.slice(0, hash).toLowerCase().endsWith(PROJECT_FILE)) {
      const found = findGroup(fileOrGroup.slice(0, hash), fileOrGroup.slice(hash + 1));
      return found ? runs.state(groupKey(found.project, found.group)) : null;
    }
    return runs.state(fileKey(fileOrGroup));
  };
  return {
    report: (viewType, file) => byType.get(viewType)?.reportFor(file) ?? null,
    groupReport: (projectFile, id) => {
      const found = findGroup(projectFile, id);
      return found ? GroupPanel.reportFor(found.project, found.group) : null;
    },
    runState: stateOf,
    run: (file) => runs.runFile(vscode.Uri.file(path.resolve(file))),
    control: async (file, command) => {
      const key = fileKey(path.resolve(file));
      if (command === 'stop') { await runs.stop(key); return true; }
      return runs.control(key, command);
    },
    openGroup: (projectFile, id) => {
      const found = findGroup(projectFile, id);
      if (found) GroupPanel.show(context, runs, found.project, found.group);
      return !!found;
    },
    runGroup: async (projectFile, id) => {
      const found = findGroup(projectFile, id);
      return found ? runs.runGroup(found.project, found.group) : null;
    },
  };
}

export function deactivate(): void {
  // RunManager.dispose stops what still runs (gracefully)
}
