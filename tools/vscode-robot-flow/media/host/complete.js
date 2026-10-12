// Completion in the Diagram's field editor (the extension's own layer, the
// shared view stays unchanged): the keyword field offers the keywords of the
// file's imports, every value field offers ${variables} after "${", the
// sub-flow file fields offer the other flow files. The catalog comes from the
// extension ('catalog' message: keywords, variables, files).
//
// The matching is plain and exported for tests; attach() does the DOM part.

/** Robot's name matching: case, spaces and underscores do not count. */
export function normName(name) {
  return String(name || '').replace(/[\s_]+/g, '').toLowerCase();
}

/** The keywords matching what was typed: a prefix of the name first, then a part of it, then of the owner. */
export function matchKeywords(keywords, typed, max = 12) {
  const n = normName(typed);
  const rank = (k) => {
    const name = normName(k.name);
    if (!n) return 2;
    if (name.startsWith(n)) return 0;
    if (name.includes(n)) return 1;
    if (normName(k.owner).includes(n)) return 2;
    return -1;
  };
  return keywords
    .map((k, i) => ({ k, r: rank(k), i }))
    .filter((x) => x.r >= 0)
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .slice(0, max)
    .map((x) => x.k);
}

/** The "${name" being typed before `caret` in `text`: its start and prefix, or null. */
export function variableAt(text, caret) {
  const before = String(text || '').slice(0, caret);
  const m = /[$@&%]\{[^}\n]*$/.exec(before);
  return m ? { start: m.index, prefix: m[0] } : null;
}

/** The variables matching the prefix typed ("${" plus the start of a name). */
export function matchVariables(variables, prefix, max = 12) {
  const n = normName(prefix.slice(2));
  const seen = new Set();
  const out = [];
  for (const v of variables) {
    const name = String(v.name || '');
    if (!name || seen.has(name)) continue;
    if (n && !normName(name.slice(2)).includes(n)) continue;
    seen.add(name);
    out.push(v);
    if (out.length >= max) break;
  }
  return out;
}

/** The parameter list of a keyword, as a hint for its arguments field. */
export function argsHint(keyword) {
  if (!keyword || !keyword.args || !keyword.args.length) return '';
  return keyword.args.map((a) => (a.kind === 'VAR_POSITIONAL' ? '*' + a.name
    : a.kind === 'VAR_NAMED' ? '**' + a.name
    : a.required ? a.name : a.name + '=' + (a.default == null ? '' : a.default))).join('\n');
}

/** The keyword of the catalog a field names, by Robot's matching, or null. */
export function findKeyword(keywords, name) {
  const n = normName(name);
  return n ? keywords.find((k) => normName(k.name) === n) || null : null;
}

// ---- the DOM part -----------------------------------------------------------

const KEYWORD_FIELDS = new Set(['fv-f-keyword']);
const FILE_FIELDS = new Set(['fv-f-file', 'fv-ask-file']);
const ARGS_FIELDS = new Set(['fv-f-args', 'fv-f-params']);

/** Attach the completion to the field editor under `root`; `catalog()` gives the current catalog. */
export function attach(root, catalog) {
  const list = document.createElement('div');
  list.className = 'hc-list';
  list.hidden = true;
  document.body.appendChild(list);
  let field = null;      // the input the list belongs to
  let items = [];        // [{ text, meta, apply() }]
  let index = 0;

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function close() { list.hidden = true; items = []; field = null; }

  function show(input, entries) {
    items = entries;
    index = 0;
    field = input;
    if (!items.length) { list.hidden = true; return; }
    list.innerHTML = items.map((it, i) =>
      `<div class="hc-item${i === 0 ? ' on' : ''}" data-i="${i}"><span class="hc-name">${esc(it.text)}</span>` +
      (it.meta ? `<span class="hc-meta">${esc(it.meta)}</span>` : '') + '</div>').join('');
    const r = input.getBoundingClientRect();
    list.style.left = `${r.left + window.scrollX}px`;
    list.style.top = `${r.bottom + window.scrollY + 2}px`;
    list.style.minWidth = `${Math.max(220, r.width)}px`;
    list.hidden = false;
  }

  function setValue(input, value, caret) {
    input.value = value;
    if (caret != null) input.setSelectionRange(caret, caret);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function entriesFor(input) {
    const data = catalog() || {};
    const id = input.id || '';
    if (KEYWORD_FIELDS.has(id)) {
      return matchKeywords(data.keywords || [], input.value).map((k) => ({
        text: k.name, meta: `${k.owner}${k.args && k.args.length ? ' · ' + argsHint(k).split('\n').join(', ') : ''}`,
        apply() { setValue(input, k.name, k.name.length); hintArgs(k); },
      }));
    }
    if (FILE_FIELDS.has(id)) {
      const typed = input.value.trim().toLowerCase();
      return (data.files || []).filter((f) => !typed || f.toLowerCase().includes(typed)).slice(0, 12).map((f) => ({
        text: f, meta: 'sub-flow', apply() { setValue(input, f, f.length); },
      }));
    }
    const v = variableAt(input.value, input.selectionStart == null ? input.value.length : input.selectionStart);
    if (!v) return [];
    const after = input.value.slice(input.selectionStart == null ? input.value.length : input.selectionStart);
    return matchVariables(data.variables || [], v.prefix).map((x) => ({
      text: x.name, meta: x.source + (x.value ? ' · ' + x.value : ''),
      apply() {
        const value = input.value.slice(0, v.start) + x.name + after;
        setValue(input, value, v.start + x.name.length);
      },
    }));
  }

  /** The arguments field shows the chosen keyword's parameters while it is empty. */
  function hintArgs(keyword) {
    const args = root.querySelector('#fv-f-args');
    if (args) args.placeholder = keyword ? argsHint(keyword).replace(/\n/g, '\n') : '';
  }

  function refresh(input) {
    if (!input || !(input.matches('input, textarea')) || !/^fv-(f-|ask-)/.test(input.id || '')) { close(); return; }
    if (KEYWORD_FIELDS.has(input.id)) hintArgs(findKeyword((catalog() || {}).keywords || [], input.value));
    show(input, entriesFor(input));
  }

  root.addEventListener('focusin', (e) => {
    const input = e.target;
    if (input && ARGS_FIELDS.has(input.id || '')) {
      const kw = root.querySelector('#fv-f-keyword');
      if (kw) hintArgs(findKeyword((catalog() || {}).keywords || [], kw.value));
    }
    refresh(input);
  });
  root.addEventListener('input', (e) => refresh(e.target));
  root.addEventListener('focusout', () => setTimeout(() => { if (!list.matches(':hover')) close(); }, 120));
  root.addEventListener('keydown', (e) => {
    if (list.hidden || !field || e.target !== field) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      index = (index + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      list.querySelectorAll('.hc-item').forEach((el, i) => el.classList.toggle('on', i === index));
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      if (items[index]) { e.preventDefault(); items[index].apply(); close(); }
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      close();
    }
  }, true);
  list.addEventListener('mousedown', (e) => {
    const el = e.target.closest && e.target.closest('.hc-item');
    if (!el) return;
    e.preventDefault();
    const it = items[Number(el.getAttribute('data-i'))];
    if (it) { it.apply(); close(); }
  });
}
