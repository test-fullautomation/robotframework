// The Run page's HTML: the page itself, drawn from the data the extension
// posts ({ type: 'data', data }), see runPage.ts for the shape. No VS Code
// API here: test/webview/runpage.mjs renders it in headless Edge.

/**
 * A flow report (one self-contained HTML file) as the page's frame shows it
 * with `srcdoc`. The frame is sandboxed (scripts only, its own origin: it
 * cannot reach the page) but still inherits the page's policy, so its script
 * gets the page's nonce. A small script added to it sends clicks on its links
 * (into log.html) to the page, which has the extension open them, and says
 * when it has loaded.
 */
export function reportForFrame(html: string, n: string): string {
  const relay = `<script nonce="${n}">document.addEventListener('click', function (e) {
  var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
  if (!a) return;
  var href = a.getAttribute('href') || '';
  if (!href || href.charAt(0) === '#') return;
  e.preventDefault();
  window.parent.postMessage({ type: 'frameLink', href: href }, '*');
});
window.addEventListener('load', function () {
  window.parent.postMessage({ type: 'reportShown', length: document.body ? document.body.innerHTML.length : 0 }, '*');
});</script>`;
  const withNonce = html.replace(/<script\b(?![^>]*\bnonce=)/gi, `<script nonce="${n}"`);
  return /<\/body>/i.test(withNonce) ? withNonce.replace(/<\/body>/i, relay + '</body>') : withNonce + relay;
}

/** The page with the given Content-Security-Policy and the script's nonce. */
export function runPageHtml(csp: string, n: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Run</title>
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); padding: 0 20px 24px; }
  h1 { font-size: 1.4em; font-weight: 600; margin: 18px 0 4px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .badge { font-size: 0.75em; font-weight: 600; padding: 2px 8px; border-radius: 10px; color: #fff; background: var(--vscode-testing-iconUnset); text-transform: uppercase; letter-spacing: 0.03em; }
  .badge.passed { background: var(--vscode-testing-iconPassed); } .badge.failed, .badge.error { background: var(--vscode-testing-iconFailed); }
  .badge.stopped { background: var(--vscode-charts-orange); } .badge.running, .badge.stopping { background: var(--vscode-testing-iconQueued); }
  .meta { color: var(--vscode-descriptionForeground); margin: 0 0 10px; line-height: 1.6; }
  .meta a, a { color: var(--vscode-textLink-foreground); cursor: pointer; text-decoration: none; } a:hover { text-decoration: underline; }
  .meta code { font-family: var(--vscode-editor-font-family); font-size: 0.95em; }
  .live { margin: 0 0 10px; padding: 8px 12px; border-left: 3px solid var(--vscode-testing-iconQueued); background: var(--vscode-textBlockQuote-background); }
  .buttons { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 18px; }
  button { font-family: inherit; font-size: inherit; color: var(--vscode-button-secondaryForeground); background: var(--vscode-button-secondaryBackground); border: none; padding: 5px 12px; border-radius: 2px; cursor: pointer; }
  button:hover { background: var(--vscode-button-secondaryHoverBackground); }
  button.primary { color: var(--vscode-button-foreground); background: var(--vscode-button-background); } button.primary:hover { background: var(--vscode-button-hoverBackground); }
  button:disabled { opacity: 0.5; cursor: default; }
  h2 { font-size: 1.05em; font-weight: 600; margin: 18px 0 8px; display: flex; align-items: baseline; gap: 10px; }
  h2 small { font-weight: normal; color: var(--vscode-descriptionForeground); }
  .tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--vscode-panel-border); margin: 10px 0 0; }
  .tabs button { background: none; color: var(--vscode-foreground); border-bottom: 2px solid transparent; border-radius: 0; padding: 6px 12px; }
  .tabs button.on { border-bottom-color: var(--vscode-panelTitle-activeBorder); } .tabs button:hover { background: var(--vscode-toolbar-hoverBackground); }
  .tabs .dot { display: inline-block; width: 8px; height: 8px; border-radius: 4px; margin-right: 6px; background: var(--vscode-testing-iconUnset); vertical-align: middle; }
  table { border-collapse: collapse; width: 100%; max-width: 1100px; }
  th { text-align: left; font-weight: 600; color: var(--vscode-descriptionForeground); padding: 4px 10px 4px 0; border-bottom: 1px solid var(--vscode-panel-border); }
  td { padding: 5px 10px 5px 0; border-bottom: 1px solid var(--vscode-panel-border); vertical-align: top; }
  td.n { color: var(--vscode-descriptionForeground); width: 2.5em; } td.t { white-space: nowrap; width: 6em; text-align: right; }
  td.s { width: 5em; font-weight: 600; } .PASS { color: var(--vscode-testing-iconPassed); } .FAIL { color: var(--vscode-testing-iconFailed); }
  .UNKNOWN { color: var(--vscode-charts-orange); } .SKIP { color: var(--vscode-testing-iconSkipped); }
  td.bar { width: 28%; } .bar div { height: 10px; border-radius: 2px; background: var(--vscode-testing-iconPassed); min-width: 2px; }
  .bar div.FAIL { background: var(--vscode-testing-iconFailed); } .bar div.UNKNOWN { background: var(--vscode-charts-orange); } .bar div.SKIP { background: var(--vscode-testing-iconSkipped); }
  .msg { margin-top: 4px; white-space: pre-wrap; font-family: var(--vscode-editor-font-family); font-size: 0.92em; color: var(--vscode-descriptionForeground); }
  .note { color: var(--vscode-descriptionForeground); font-style: italic; }
  iframe { width: 100%; height: 78vh; border: 1px solid var(--vscode-panel-border); background: #fff; border-radius: 2px; }
  .report-head { display: flex; align-items: center; gap: 10px; }
  .hidden { display: none; }
</style>
</head>
<body>
<div id="page"><p class="note">Loading…</p></div>
<div id="reports"></div>
<script nonce="${n}">
(function () {
  const vscode = acquireVsCodeApi();
  const page = document.getElementById('page');
  let data = null;
  let tab = null;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const post = (m) => vscode.postMessage(m);
  window.addEventListener('error', (e) => post({ type: 'pageError', what: String(e.message || e) }));

  function member(m) {
    const parts = [];
    const total = m.total ? [m.total.pass + ' passed', m.total.fail ? m.total.fail + ' failed' : '', m.total.unknown ? m.total.unknown + ' unknown' : '', m.total.skip ? m.total.skip + ' skipped' : ''].filter(Boolean).join(', ') : '';
    parts.push('<h2>' + (m.flow ? 'Phases' : 'Tests') + (total ? ' <small>' + esc(total) + (m.duration ? ' · ' + esc(m.duration) : '') + '</small>' : '') + '</h2>');
    if (m.tests && m.tests.length) {
      const max = Math.max(0.001, ...m.tests.map((t) => t.elapsed || 0));
      parts.push('<table><tr><th></th><th>Name</th><th>Status</th><th>Time</th><th></th></tr>');
      m.tests.forEach((t, i) => {
        const w = Math.max(1, Math.round(100 * (t.elapsed || 0) / max));
        const time = t.elapsed == null ? '' : t.elapsed < 10 ? (Math.round(t.elapsed * 10) / 10) + ' s' : t.elapsed < 60 ? Math.round(t.elapsed) + ' s' : Math.floor(t.elapsed / 60) + ' min ' + Math.round(t.elapsed % 60) + ' s';
        parts.push('<tr><td class="n">' + (i + 1) + '</td><td>' + esc(t.name) + (t.message && t.status !== 'PASS' ? '<div class="msg">' + esc(t.message) + '</div>' : '') + '</td>'
          + '<td class="s ' + esc(t.status) + '">' + esc(t.status) + '</td><td class="t">' + esc(time) + '</td><td class="bar"><div class="' + esc(t.status) + '" style="width:' + w + '%"></div></td></tr>');
      });
      parts.push('</table>');
    } else if (m.note) {
      parts.push('<p class="note">' + esc(m.note) + '</p>');
    } else {
      parts.push('<p class="note">No tests.</p>');
    }
    return parts.join('');
  }

  // The report sections, one per member, kept across redraws: a frame that is
  // moved in the DOM reloads, so only the section of a changed report is rebuilt.
  const reports = document.getElementById('reports');
  const lastHtml = {};   // member id -> the report's HTML as last sent
  function drawReports(d) {
    const keep = new Set();
    d.members.forEach((m) => {
      if (!m.flow) return;
      keep.add(m.id);
      let section = reports.querySelector('section[data-member="' + CSS.escape(m.id) + '"]');
      const key = m.reportKey || (m.canReport ? 'can' : d.running ? 'running' : 'none');
      if (section && section.dataset.key === key) {
        section.hidden = d.group && m.id !== tab;
        return;
      }
      if (!section) {
        section = document.createElement('section');
        section.dataset.member = m.id;
        reports.appendChild(section);
      }
      section.dataset.key = key;
      section.hidden = d.group && m.id !== tab;
      section.innerHTML = '<h2 class="report-head">Flow report' + (m.reportKey ? ' <small><a data-open-report="' + esc(m.id) + '">open in the browser</a></small>' : '') + '</h2>'
        + (m.reportKey ? '<iframe data-report="' + esc(m.id) + '" sandbox="allow-scripts" title="Flow report"></iframe>'
          : m.canReport ? '<p><button data-flow-report="' + esc(m.id) + '">Write the flow report</button> <span class="note">from output.xml, with the fork\\'s robot.flow report</span></p>'
          : '<p class="note">' + (d.running ? 'Written when the run ends, or on demand afterwards.' : 'No flow report: the run has no output.xml.') + '</p>');
      const frame = section.querySelector('iframe');
      const html = m.reportHtml || lastHtml[m.id];
      if (frame && html) {
        frame.dataset.key = m.reportKey;
        frame.srcdoc = html;   // the frame says when it has loaded (a sandboxed frame's document cannot be read from here)
      }
    });
    reports.querySelectorAll('section[data-member]').forEach((s) => { if (!keep.has(s.dataset.member)) s.remove(); });
  }

  function render() {
    if (!data) return;
    const d = data;
    const h = [];
    h.push('<h1>' + esc(d.title) + ' <span class="badge ' + esc(d.status) + '">' + esc(d.status === 'incomplete' ? 'no end' : d.status) + '</span></h1>');
    const meta = [];
    if (d.when) meta.push(esc(d.when));
    if (d.duration) meta.push(esc(d.duration));
    if (d.target) meta.push('<a data-open="target" title="' + esc(d.target) + '">' + esc(d.target.split(/[\\\\/]/).pop()) + '</a>');
    meta.push('<a data-open="folder" title="' + esc(d.dir) + '">' + esc(d.dir.split(/[\\\\/]/).pop()) + '</a>');
    h.push('<p class="meta">' + meta.join(' · ') + (d.variables.length ? '<br>' + d.variables.map((v) => '<code>' + esc(v[0]) + '=' + esc(v[1]) + '</code>').join(' ') : '') + '</p>');
    if (d.live) h.push('<p class="live">' + esc(d.live) + '</p>');
    const b = [];
    if (d.running) {
      if (d.pausable) b.push('<button class="primary" data-control="' + (d.paused ? 'resume' : 'pause') + '">' + (d.paused ? 'Resume' : 'Pause') + '</button>');
      b.push('<button data-control="stop">Stop</button>');
      if (d.pausable) b.push('<button data-control="snapshot">Snapshot…</button>');
    }
    if (d.files.log) b.push('<button data-open="log">Log</button>');
    if (d.files.report) b.push('<button data-open="report">Report</button>');
    if (d.files.output) b.push('<button data-open="output">Output</button>');
    d.checkpoints.forEach((c) => b.push('<button data-continue="' + esc(c.file) + '" title="' + esc(c.file) + '">Continue from ' + esc(c.kind) + '</button>'));
    h.push('<div class="buttons">' + b.join('') + '</div>');
    if (d.group) {
      if (!tab || !d.members.some((m) => m.id === tab)) tab = d.members.length ? d.members[0].id : null;
      h.push('<div class="tabs">' + d.members.map((m) => '<button data-tab="' + esc(m.id) + '" class="' + (m.id === tab ? 'on' : '') + '"><span class="dot ' + esc(m.status) + '" style="background: var(--vscode-' + ({ passed: 'testing-iconPassed', failed: 'testing-iconFailed', error: 'testing-iconFailed', stopped: 'charts-orange', running: 'testing-iconQueued' }[m.status] || 'testing-iconUnset') + ')"></span>' + esc(m.title) + '</button>').join('') + '</div>');
      const m = d.members.find((x) => x.id === tab);
      if (m) h.push(member(m));
    } else if (d.members[0]) {
      h.push(member(d.members[0]));
    }
    page.innerHTML = h.join('');
    drawReports(d);
  }

  document.body.addEventListener('click', (e) => {
    const el = e.target.closest('[data-open],[data-control],[data-continue],[data-tab],[data-flow-report],[data-open-report]');
    if (!el) return;
    if (el.dataset.open) post({ type: 'open', what: el.dataset.open });
    else if (el.dataset.control) post({ type: 'control', command: el.dataset.control });
    else if (el.dataset.continue) post({ type: 'continue', file: el.dataset.continue });
    else if (el.dataset.flowReport) { el.disabled = true; el.textContent = 'Writing…'; post({ type: 'flowReport', member: el.dataset.flowReport }); }
    else if (el.dataset.openReport) { const m = data.members.find((x) => x.id === el.dataset.openReport); if (m) post({ type: 'openReport', member: m.id }); }
    else if (el.dataset.tab) { tab = el.dataset.tab; render(); }
  });
  window.addEventListener('message', (e) => {
    if (!e.data) return;
    // A report's frame may only say what it says below: the data comes from the extension alone.
    const frame = [...document.querySelectorAll('iframe[data-report]')].find((f) => f.contentWindow === e.source);
    if (e.data.type === 'data' && !frame) {
      // the extension's data; a report's HTML comes only when it changed
      data = e.data.data;
      data.members.forEach((m) => { if (m.reportHtml) lastHtml[m.id] = m.reportHtml; });
      render();
    } else if (e.data.type === 'frameLink' && frame) {
      // a link in a report's frame (log.html#id): the extension opens it
      post({ type: 'frameLink', member: frame.dataset.report, href: String(e.data.href || '') });
    } else if (e.data.type === 'reportShown' && frame) {
      post({ type: 'reportShown', member: frame.dataset.report, key: frame.dataset.key, length: Number(e.data.length) || 0 });
    }
  });
  post({ type: 'ready' });
})();
</script>
</body>
</html>`;
}
