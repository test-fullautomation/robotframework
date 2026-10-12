// Go to Definition (F12, Ctrl+Click) in Robot Framework files and flow files:
// a keyword call goes to the keyword, an import or a sub-flow to its file.
import * as vscode from 'vscode';
import { Definition, defineName, existingFile, flowStringAt, robotCellAt } from './backend/define';
import { robotEnvFor } from './settings';

const FLOW_SELECTOR: vscode.DocumentSelector = { pattern: '**/*.flow.json' };
const ROBOT_SELECTOR: vscode.DocumentSelector = { language: 'robotframework' };
// Keys of a flow node whose value is a keyword name.
const FLOW_KEYWORD_KEYS = new Set(['keyword']);

export function registerDefinitions(context: vscode.ExtensionContext): void {
  // Ctrl+hover asks on every mouse move: one helper run per document version and name.
  const cache = new Map<string, Promise<Definition>>();

  function lookup(doc: vscode.TextDocument, name: string): Promise<Definition> {
    const key = `${doc.uri.toString()}#${doc.version}#${name}`;
    let found = cache.get(key);
    if (!found) {
      if (cache.size > 200) cache.clear();
      found = robotEnvFor(doc.uri, context.extensionPath)
        .then((env) => defineName(env, doc.uri.fsPath, doc.getText(), name));
      cache.set(key, found);
    }
    return found;
  }

  function location(def: Definition, range: vscode.Range): vscode.LocationLink[] | undefined {
    if (!def.found || !def.source) return undefined;
    const line = Math.max(0, (def.line || 1) - 1);
    const target = new vscode.Range(line, 0, line, 0);
    return [{ originSelectionRange: range, targetUri: vscode.Uri.file(def.source), targetRange: target,
              targetSelectionRange: target }];
  }

  function fileLink(file: string, range: vscode.Range): vscode.LocationLink[] {
    const top = new vscode.Range(0, 0, 0, 0);
    return [{ originSelectionRange: range, targetUri: vscode.Uri.file(file), targetRange: top, targetSelectionRange: top }];
  }

  context.subscriptions.push(vscode.languages.registerDefinitionProvider(ROBOT_SELECTOR, {
    async provideDefinition(doc, position) {
      const cell = robotCellAt(doc.lineAt(position.line).text, position.character);
      if (!cell) return undefined;
      const range = new vscode.Range(position.line, cell.start, position.line, cell.start + cell.text.length);
      const file = /\.(robot|resource|py|json|ya?ml|txt)$/i.test(cell.text) ? existingFile(doc.uri.fsPath, cell.text) : null;
      if (file) return fileLink(file, range);
      return location(await lookup(doc, cell.text), range);
    },
  }));

  context.subscriptions.push(vscode.languages.registerDefinitionProvider(FLOW_SELECTOR, {
    async provideDefinition(doc, position) {
      const str = flowStringAt(doc.lineAt(position.line).text, position.character);
      if (!str || !str.text.trim()) return undefined;
      const range = new vscode.Range(position.line, str.start + 1, position.line, str.end - 1);
      if (!str.key || !FLOW_KEYWORD_KEYS.has(str.key)) {
        // A sub-flow ("file"), an imported resource or library file: open it.
        const file = existingFile(doc.uri.fsPath, str.text);
        if (file) return fileLink(file, range);
        if (str.key !== null) return undefined;   // some other value (an argument, an id)
      }
      // A keyword step, or an imported library by name.
      return location(await lookup(doc, str.text), range);
    },
  }));
}
