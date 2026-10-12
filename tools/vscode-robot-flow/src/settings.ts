// The user's settings (+ the file's test project, if any) -> a RobotEnv.
//
// Interpreter: robotFlow.python, else the project's run.python
// (testproject.json), else the Python extension's, else "python".
// PYTHONPATH:  robotFlow.robotSource, the project's run.pythonpath,
// robotFlow.pythonPath. Working folder: the project root, else the workspace
// folder -- the Manager GUI runs and reads a project's files the same way.
import * as path from 'node:path';
import * as vscode from 'vscode';
import { RobotEnv, cleanPath } from './backend/helpers';
import { Project, projectOf } from './backend/project';

const cache = new Map<string, string>();   // workspace folder -> interpreter from the Python extension

/** The workspace folder of a document (or the document's own folder). */
export function folderOf(uri: vscode.Uri): string {
  const ws = vscode.workspace.getWorkspaceFolder(uri);
  return ws ? ws.uri.fsPath : path.dirname(uri.fsPath);
}

/** The test project of a file (testproject.json above it, within its workspace folder). */
export function projectFor(uri: vscode.Uri): Project | null {
  return projectOf(uri.fsPath, folderOf(uri));
}

function expand(value: string, folder: string): string {
  return cleanPath(value).replace(/\$\{workspaceFolder\}/g, folder);
}

async function interpreter(uri: vscode.Uri, folder: string, project: Project | null): Promise<string> {
  const configured = vscode.workspace.getConfiguration('robotFlow', uri).get<string>('python', '');
  if (configured.trim()) return expand(configured, folder);
  if (cleanPath(project?.run.python || '')) return cleanPath(project!.run.python);
  if (cache.has(folder)) return cache.get(folder)!;
  let found = 'python';
  try {
    const ext = vscode.extensions.getExtension('ms-python.python');
    if (ext) {
      const api = ext.isActive ? ext.exports : await ext.activate();
      const env = api?.environments?.getActiveEnvironmentPath?.(uri);
      if (env?.path) found = env.path;
    }
  } catch {
    // no Python extension, or an older API: keep "python"
  }
  cache.set(folder, found);
  return found;
}

/** Forget the interpreters found through the Python extension (it may have a new one). */
export function forgetInterpreters(): void {
  cache.clear();
}

export async function robotEnvFor(uri: vscode.Uri, extensionPath: string): Promise<RobotEnv> {
  const folder = folderOf(uri);
  const project = projectFor(uri);
  const cfg = vscode.workspace.getConfiguration('robotFlow', uri);
  return {
    python: await interpreter(uri, folder, project),
    robotSource: expand(cfg.get<string>('robotSource', ''), folder),
    pythonPath: [...(project?.run.pythonpath || []),
                 ...cfg.get<string[]>('pythonPath', []).map((p) => expand(p, folder))].filter(Boolean),
    cwd: project ? project.root : folder,
    timeoutMs: Math.max(5, cfg.get<number>('timeoutSeconds', 60)) * 1000,
    helpersDir: path.join(extensionPath, 'python'),
  };
}

export function refreshDelay(uri: vscode.Uri): number {
  return Math.max(0, vscode.workspace.getConfiguration('robotFlow', uri).get<number>('refreshDelay', 400));
}

/** Where runs write their output: robotFlow.resultsFolder, relative to the project root (or workspace folder). */
export function resultsRoot(uri: vscode.Uri): string {
  const folder = folderOf(uri);
  const base = projectFor(uri)?.root || folder;
  const configured = expand(vscode.workspace.getConfiguration('robotFlow', uri).get<string>('resultsFolder', 'results'), folder);
  return path.isAbsolute(configured) ? configured : path.join(base, configured);
}
