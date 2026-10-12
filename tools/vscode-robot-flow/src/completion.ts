// Completion while editing a flow file's text: node ids in edges, the labels
// a node may carry, keywords of the imports, variables, sub-flow files. The
// structure (keys, kinds, enum values) comes from the JSON schema registered
// in package.json for *.flow.json.
import * as path from 'node:path';
import * as vscode from 'vscode';
import { FlowCatalog, catalogKey, flowCatalog, itemsFor, placeAt } from './backend/complete';
import { robotEnvFor } from './settings';

const FLOW_SELECTOR: vscode.DocumentSelector = { pattern: '**/*.flow.json' };
const KINDS: Record<string, vscode.CompletionItemKind> = {
  node: vscode.CompletionItemKind.Reference,
  label: vscode.CompletionItemKind.EnumMember,
  keyword: vscode.CompletionItemKind.Function,
  variable: vscode.CompletionItemKind.Variable,
  file: vscode.CompletionItemKind.File,
};

export function registerCompletion(context: vscode.ExtensionContext): void {
  // One catalog per file and imports section; reread when the imports change.
  const catalogs = new Map<string, { key: string; catalog: Promise<FlowCatalog> }>();

  function catalogFor(doc: vscode.TextDocument): Promise<FlowCatalog> {
    const key = catalogKey(doc.getText());
    const have = catalogs.get(doc.uri.toString());
    if (have && have.key === key) return have.catalog;
    const catalog = robotEnvFor(doc.uri, context.extensionPath)
      .then((env) => flowCatalog(env, doc.uri.fsPath, doc.getText()));
    catalogs.set(doc.uri.toString(), { key, catalog });
    return catalog;
  }

  context.subscriptions.push(vscode.workspace.onDidCloseTextDocument((doc) => catalogs.delete(doc.uri.toString())));

  context.subscriptions.push(vscode.languages.registerCompletionItemProvider(FLOW_SELECTOR, {
    async provideCompletionItems(doc, position, token) {
      const text = doc.getText();
      const place = placeAt(text, doc.offsetAt(position));
      if (!place) return undefined;
      const needsCatalog = place.key === 'keyword' || /\$\{[^}]*$/.test(place.prefix);
      const catalog = needsCatalog ? await catalogFor(doc) : null;
      if (token.isCancellationRequested) return undefined;
      const range = new vscode.Range(doc.positionAt(place.start), doc.positionAt(place.end));
      const items: vscode.CompletionItem[] = [];
      for (const it of itemsFor(text, place, catalog)) {
        if (it.kind === 'file') {
          for (const file of await subflowFiles(doc.uri)) {
            const item = new vscode.CompletionItem(file, KINDS.file);
            item.range = range;
            items.push(item);
          }
          continue;
        }
        const item = new vscode.CompletionItem(it.label, KINDS[it.kind]);
        item.insertText = it.insert ?? it.label;
        item.range = range;
        item.detail = it.detail;
        item.documentation = it.doc;
        item.sortText = it.sort;
        if (it.kind === 'label' && !it.insert) item.filterText = 'then';
        items.push(item);
      }
      return items;
    },
  }, '"', '$', '{', '/'));
}

/** Flow files near the document, as paths relative to it, for a sub-flow call. */
export async function subflowFiles(uri: vscode.Uri): Promise<string[]> {
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  const pattern = folder ? new vscode.RelativePattern(folder, '**/*.flow.json') : '**/*.flow.json';
  const files = await vscode.workspace.findFiles(pattern, '**/{node_modules,results,.git}/**', 200);
  const here = path.dirname(uri.fsPath);
  return files
    .filter((f) => f.fsPath !== uri.fsPath)
    .map((f) => path.relative(here, f.fsPath).split(path.sep).join('/'))
    .sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b));
}
