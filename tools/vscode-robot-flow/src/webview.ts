// The HTML of a view's webview: the host shim, then the shared view module.
import * as crypto from 'node:crypto';
import * as vscode from 'vscode';

export function viewHtml(webview: vscode.Webview, extensionUri: vscode.Uri, entry: string, title: string): string {
  const media = vscode.Uri.joinPath(extensionUri, 'media');
  const asUri = (...p: string[]) => webview.asWebviewUri(vscode.Uri.joinPath(media, ...p)).toString();
  const nonce = crypto.randomBytes(16).toString('base64');
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    // the views add their styles with a <style> element (style.js)
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource}`,
    `script-src 'nonce-${nonce}' ${webview.cspSource}`,
  ].join('; ');
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<link rel="stylesheet" href="${asUri('host', 'host.css')}">
</head>
<body>
<div id="toolbar" class="toolbar" hidden></div>
<div id="status" class="status" hidden></div>
<div id="view"></div>
<script type="module" nonce="${nonce}">
import { start } from '${asUri('host', 'host.js')}';
import { mount } from '${asUri('vendor', ...entry.split('/'))}';
start(mount);
</script>
</body>
</html>`;
}
