// The webview side: gives the shared views (media/vendor/...) the `ctx` they
// get in the Manager GUI's sandboxed frame -- selection(), onSelection(fn),
// reveal(target), edit(change) -> Promise -- over VS Code's postMessage.
// The views are the Manager GUI's own files, unchanged.
const vscode = acquireVsCodeApi();

let selection = null;
const listeners = [];
const pending = new Map();
let seq = 0;

const ctx = {
  selection: () => selection,
  onSelection(fn) {
    listeners.push(fn);
    return () => { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); };
  },
  /** Show a line or a flow node in the text: { line } | { node } | { follow, ... } (live, ignored). */
  reveal(target) {
    vscode.postMessage({ type: 'reveal', target });
  },
  /** A change made in the view; the extension applies it to the document. */
  edit(change) {
    const id = ++seq;
    return new Promise((resolve) => {
      pending.set(id, resolve);
      vscode.postMessage({ type: 'edit', id, change });
    });
  },
  /** A step's breakpoint dot was clicked: the extension sets or removes a VS Code breakpoint. */
  breakpoint(node) {
    vscode.postMessage({ type: 'breakpoint', node });
  },
};

// ---- the extension's toolbar above the view: run, stop, live options, results

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ESC[c]);

function showToolbar(t) {
  const el = document.getElementById('toolbar');
  if (!el) return;
  if (!t || (!t.canRun && !t.status)) { el.hidden = true; el.innerHTML = ''; return; }
  const running = t.status === 'running' || t.status === 'stopping';
  const opt = (name, value, label, current) =>
    `<option value="${esc(value)}"${String(current) === String(value) ? ' selected' : ''}>${esc(label)}</option>`;
  el.hidden = false;
  el.innerHTML =
    (t.canRun ? `<button type="button" class="tb-btn tb-run" data-act="run"${running ? ' disabled' : ''} title="${esc(t.runTitle || 'Run')}">▶ Run</button>` : '') +
    (t.canDebug ? `<button type="button" class="tb-btn" data-act="debug"${running ? ' disabled' : ''} title="Debug: stop at breakpoints (click a step's dot, or F9 in the text), step, see variables">⏵ Debug</button>` : '') +
    (t.canStep ? `<button type="button" class="tb-btn" data-act="step"${running ? ' disabled' : ''} title="Run in step mode: the flow pauses before every step of its test phases; Next step goes on">⏯ Step</button>` : '') +
    (t.canContinue ? '<button type="button" class="tb-btn tb-run" data-act="continue" title="A new run that continues from where this one stopped: finished test phases are skipped, the loop goes on with what is left">⟳ Continue</button>' : '') +
    (t.pausable ? (t.flowPaused
      ? `<button type="button" class="tb-btn tb-run" data-act="resume" title="${t.step ? 'Run the next step, then pause again' : 'Go on from where the flow paused'}">${t.step ? '⏭ Next step' : '▶ Resume'}</button>`
      : '<button type="button" class="tb-btn" data-act="pause" title="Pause at the next step: loop deadlines and gate timeouts wait too">⏸ Pause</button>') : '') +
    (running ? `<button type="button" class="tb-btn" data-act="stop" title="${t.status === 'stopping'
      ? 'Stopping: again stops through the stop file, a third time ends it at once'
      : t.flowStop ? 'Stop at the next step: the teardown runs and a checkpoint is written to continue from'
                   : 'Stop gracefully: the running step ends, teardowns run, log and report are written'}">■ ${t.status === 'stopping' ? 'Stop now' : 'Stop'}</button>` : '') +
    (t.status ? `<span class="tb-status tb-${esc(t.status)}">${esc(t.text || t.status)}</span>` : '') +
    (t.members || []).map((m) => `<span class="tb-member${m.paused ? ' tb-member-paused' : ''}" title="${esc(m.id + ': ' + m.text)}">${esc(m.id)}` +
      `<button type="button" class="tb-mini" data-act="${m.paused ? 'resume' : 'pause'}:${esc(m.id)}" aria-label="${m.paused ? 'Resume' : 'Pause'} ${esc(m.id)}" ` +
      `title="${m.paused ? 'Resume' : 'Pause'} ${esc(m.id)} alone (members that wait for it may run into their gate timeouts)">${m.paused ? '▶' : '⏸'}</button></span>`).join('') +
    '<span class="tb-spacer"></span>' +
    (t.live ? `<label class="tb-opt">Zoom <select data-opt="zoom">${
      opt('zoom', 'fit', 'Fit', t.zoom) + opt('zoom', '0.75', '75%', t.zoom) + opt('zoom', 'natural', '100%', t.zoom)}</select></label>` +
      `<label class="tb-opt">Motion <select data-opt="motion">${
      opt('motion', 'tail', 'Trail', t.motion) + opt('motion', 'hop', 'Hop', t.motion) + opt('motion', 'off', 'Off', t.motion)}</select></label>` : '') +
    (t.hasLog ? '<button type="button" class="tb-btn" data-act="log" title="Open log.html in the browser">Log</button>' +
                '<button type="button" class="tb-btn" data-act="report" title="Open report.html in the browser">Report</button>' : '') +
    (t.status ? '<button type="button" class="tb-btn" data-act="output" title="The run\'s console output">Output</button>' : '');
}

document.addEventListener('click', (e) => {
  const b = e.target.closest && e.target.closest('#toolbar [data-act]');
  if (b && !b.disabled) vscode.postMessage({ type: 'toolbar', action: b.getAttribute('data-act') });
});
document.addEventListener('change', (e) => {
  const s = e.target.closest && e.target.closest('#toolbar [data-opt]');
  if (s) vscode.postMessage({ type: 'toolbar', action: 'option', name: s.getAttribute('data-opt'), value: s.value });
});

function showStatus(status) {
  const el = document.getElementById('status');
  if (!el) return;
  const text = status && status.text;
  el.hidden = !text;
  el.className = status && status.kind ? 'status ' + status.kind : 'status';
  el.textContent = text || '';
}

window.addEventListener('message', (event) => {
  const msg = event.data || {};
  if (msg.type === 'selection') {
    selection = msg.selection;
    showStatus(msg.status);
    for (const fn of listeners.slice()) {
      try { fn(selection); } catch (e) { showStatus({ kind: 'error', text: 'The view failed: ' + e.message }); }
    }
  } else if (msg.type === 'status') {
    showStatus(msg.status);
  } else if (msg.type === 'toolbar') {
    showToolbar(msg.toolbar);
  } else if (msg.type === 'result') {
    const resolve = pending.get(msg.id);
    pending.delete(msg.id);
    if (resolve) resolve(msg.result);
  }
});

/** Mount a shared view and tell the extension it can send data. */
export function start(mount) {
  try {
    mount(document.getElementById('view'), ctx);
  } catch (e) {
    showStatus({ kind: 'error', text: 'The view could not start: ' + e.message });
  }
  vscode.postMessage({ type: 'ready' });
}
