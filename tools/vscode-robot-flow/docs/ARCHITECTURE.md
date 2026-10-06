# Architecture

## One code base with the Manager GUI

```
 VS Code                                   Manager GUI (Electron / browser)
 ───────                                   ────────────────────────────────
 custom text editor (viewEditor.ts)        project view (app.js)
   │ webview: media/host/host.js             │ sandboxed frame: frame-sdk.js
   │   ctx = { selection, onSelection,       │   ctx = { selection, onSelection,
   │           reveal, edit }                │           reveal, edit }
   ▼                                         ▼
 media/vendor/robot-grid/grid.js  ═══════  web/plugins/robot-grid/grid.js     (same files)
 media/vendor/flow-view/view.js   ═══════  web/plugins/flow-view/view.js
   ▲ data            │ edits                 ▲ data            │ edits
   │                 ▼                       │                 ▼
 backend/helpers.ts: spawn python          adapters/test_project/robot_aio.py
   python/robot_grid.py   ════════════════  robot_grid.py        (same files)
   python/flow_inspect.py ════════════════  flow_inspect.py (+ flow_edit.py)
   │
   ▼
 the user's Python + Robot Framework (+ robotSource on PYTHONPATH)
```

- **Views** (`media/vendor/`) are the Manager GUI plugins' ES modules,
  unchanged. Each exports `mount(el, ctx)` and needs only the four `ctx`
  calls above; `media/host/host.js` provides them over `postMessage`.
- **Helpers** (`python/`) are the Manager GUI runner's scripts, unchanged:
  the file's text on stdin (unsaved edits included), JSON on stdout;
  `--edit` gets `{text, edit}` and answers the new text. They import only
  the standard library and Robot Framework.
- **The extension** (`src/`) only connects the two: it reads the document,
  runs a helper with the configured interpreter, posts the answer to the
  view, applies a view's edit as one `WorkspaceEdit`, and turns `reveal`
  into a cursor move in the text editor.
- `npm run sync` copies both from the Manager GUI repository and records the
  commit in `media/vendor/SOURCE.json`. A fix or feature goes into the
  Manager GUI repo first; both tools get it from there.

## The text editor: grammar and Go to Definition

- **Colours** come from a TextMate grammar
  (`syntaxes/robotframework.tmLanguage.json`, built from
  `scripts/build-grammar.py`) for the `robotframework` language this
  extension contributes. It is line-based: a section header opens a section
  rule (Settings, Variables, Test Cases / Tasks / Keywords, Comments), and in
  a body a line's first cell after any `${var}=` is the keyword call. Scopes
  are the common ones themes colour. `npm run test:grammar` tokenizes a
  sample with VS Code's own engine (from the installed VS Code) and checks
  each piece's scope.
- **Go to Definition** splits the job: `src/backend/define.ts` finds what the
  cursor is on (the Robot cell, or the flow file's JSON string and its key);
  `robot_grid.py --define` (shared with the Manager GUI) says where Robot
  finds that name -- the same catalog the Grid uses, with Libdoc's `source`
  and `lineno`; for a flow, its `imports` become a suite's settings. Paths
  (imports, `"file"` sub-flows) open directly. Answers are cached per
  document version and name, since Ctrl+hover asks on every mouse move.

## Debugging

```text
 VS Code ──DAP──► backend/debugSession.ts ──launch──► RunManager.runFile(…, debug)
                     ▲     │                            │ robot_boot.py --listener flow_debug.py
              stopped│     │breakpoints, continue,      ▼
                     │     ▼step, variables, evaluate  Robot process
               127.0.0.1:<port> ◄──── JSON lines ──── python/flow_debug.py (listener v2)
                                                        └─ flow_position.py: which step
```

- `src/debug.ts` registers the `robotflow` debugger with an inline adapter;
  `backend/debugSession.ts` speaks the Debug Adapter Protocol (no VS Code API,
  so `test/debug.test.mjs` drives it like VS Code does, with real runs). It
  opens a local port, starts the run through the RunManager with
  `--listener python/flow_debug.py` and `MM_DEBUG_PORT`, and forwards
  breakpoints, stepping and questions to the listener.
- `python/flow_debug.py` (shared with the Manager GUI, whose bridge debugs
  with it too -- `debugging.py`): a Robot listener that connects at load, waits for the breakpoints, and blocks in
  `start_keyword` where it should stop, answering variables and evaluations
  until told to go on. It knows the flow step from `flow_position.py` (the
  live Diagram's listener, loaded first): the fork's items carry a line
  number that maps to their step. Lines of suites and resources come from
  Robot itself (`source`, `lineno` of the call).
- `backend/flowMap.ts` turns lines of a flow file into steps (a breakpoint
  anywhere in a step's object is the step's) and back (the stack's line),
  and finds the sub-flows a flow calls with the name their steps run under
  (`<flow.name>::<id>`).
- **Python**: the listener also starts debugpy in the Robot process
  (`MM_DEBUGPY`; the interpreter's debugpy, else the Python Debugger
  extension's bundled one) and says its port in `hello`; `src/debug.ts`
  attaches a `debugpy` session as a child of the Robot one (`PythonSide`)
  before the run starts. A stop's top frame carries `py` (file and first body
  line of the keyword's function, found through Robot's handler) for a
  keyword of the user's code. Step Into then sets a one-time breakpoint
  there in the Python session (with the user's breakpoints of that file) and
  sends Step Over to the listener: Python stops in the function, Robot stops
  at the next step after it returns. The one-time breakpoint is dropped at
  the next stop of either side.
- The Diagram gets `breakpoints` and `paused` with its data and
  `ctx.breakpoint(id)` from the host (`flow-view/breakpoints.js`, shared;
  the Manager GUI sends neither, so it draws no dots).

## Flow control

```text
 toolbar / commands ──► RunManager.control / stop / continueRun
                           │ python -m robot.flow control <store> pause|resume|stop [--rig M]
                           ▼
                        <run>/signals.json  (ROBOT_FLOW_SIGNALS: the flow's store)
                           ▲ flow.control[.<rig>]          │ flow.state.<rig or pid>
                           │ polled every 0.5 s             ▼ read every 1 s (readControlState)
                        Robot process (the fork's robot.flow.control)
```

- `backend/control.ts` (no VS Code API, `test/control.test.mjs` runs it
  against the fork) sends the command with the run's interpreter and Robot,
  reads the processes' published state, and finds the checkpoint a stopped
  run left (`<outdir>/<flow>.checkpoint.json`). It mirrors `robot_aio.py`'s
  `control`, `control_state` and `restart_variables` in the Manager GUI.
- `backend/run.ts` plans step mode (`--variable FLOW_STEP:yes`) and a group
  member's rig (`ROBOT_FLOW_RIG` = the member id, so `--rig` reaches it
  alone). The store is `signalsFile`: the run's, or the one the project's or
  group's environment names.
- `RunManager.stop` gives a flow (not a debugged one) the fork's stop first
  and marks its processes stopping; the stop file follows after 60 s, as in
  the GUI. A second Stop writes the stop file, a third kills.
- `RunManager.continueRun` starts the same file with the old run's
  variables plus `FLOW_CHECKPOINT`.

## Theme

The shared views are drawn for a light page. Their neutrals and tints are CSS
variables with the light colour as fallback — `var(--fv-surface, #ffffff)` in
`flow-view/style.js`, `var(--rg-surface, #ffffff)` in `robot-grid/style.js` —
so the Manager GUI, which sets none of them, renders exactly as before (the
light screenshots are byte-identical). `media/host/host.css` sets them under
VS Code's `body.vscode-dark` / `body.vscode-high-contrast` classes. Strong
hues (teal gates, purple loops, orange recovery, red failures) stay literal:
they read on both grounds. A new colour in a shared style should get a token
the same way, or the dark theme shows it as a light patch.

## Edits and undo

The text document is the only state. A view's change runs the helper's
`--edit`, which applies it with Robot's own model (Grid) or rewires and
validates the flow with the fork (Diagram), and returns the whole new text;
the extension replaces the document text in one `WorkspaceEdit`. So:

- the file becomes dirty, Save / Revert / Ctrl+Z are VS Code's;
- the views' own **Undo** button puts back the text from before the view's
  last change (until the user types), like in the Manager GUI;
- typing in the text editor redraws the views after `robotFlow.refreshDelay`.

A read that fails (a syntax error while typing) keeps the last good drawing
and shows the error above it.

## Robot Framework 6.1 now, 7.5 next

The extension talks to *a* Robot Framework, chosen by `robotFlow.python` and
`robotFlow.robotSource`; it never assumes a version.

- `backend/robot.ts` asks the configured Robot what it is and has:
  version, location, `robot.flow` (flow files) and THREAD blocks — the same
  checks the helpers use (`robot_grid.has_threads()`).
- The **Grid** needs Robot's parsing API and Libdoc. `robot_grid.py` reports
  `features` (e.g. `thread`) and the view offers only what exists.
- The **Diagram** needs `robot.flow` from the RobotFramework AIO fork. Without
  it the view says so, and for 7.x that the flow features are not ported yet.

**Measured on 5 Oct 2026** with `robotSource` = `robotframework_7.5/src`
(branch `develop_7.5`, Python 3.13), demo files:

| Check | 6.1 fork | 7.5 port |
|---|---|---|
| probe: `robot.flow`, THREAD | yes, yes | yes, yes |
| Grid: read a suite | ok | ok |
| Grid: edit (delete a step) | ok | ok |
| Diagram: read a flow | ok | **fails**: `TypeError: sequence item 1: expected str instance, ArgInfo found` |
| Diagram: edit (insert a step) | ok | ok |

The Diagram failure is in the 7.5 port, not here: `robot/flow/render.py`
writes `[Arguments]` from `keyword.args` (line 94), which in RF 7 is an
argument spec whose items are `ArgInfo` objects, not strings
(`_row(lines, 1, '[Arguments]', *keyword.args)` → `SEPARATOR.join(cells)`).
Rendering each as text (e.g. `*(str(a) for a in keyword.args)`, checked
against RF 7's `ArgInfo.__str__`) fixes it. Flows without keyword arguments
read fine.

Moving to 7.5 (the 7.5 port of this fork):

1. Point `robotFlow.robotSource` at the 7.5 tree's `src` and run
   `npm test` / `npm run test:webview` with `ROBOT_FLOW_SRC` set to it: that
   shows what the helpers need for 7.5.
2. `robot_grid.py` uses `robot.api.parsing` and `robot.libdocpkg`; the
   statement / block classes it edits changed between 6.1 and 7.x (e.g.
   `Var`, `Group`, `ReturnStatement` → `Return`, typed `[Arguments]`). Where
   they differ, branch inside `robot_grid.py` on the model's classes (as
   `has_threads()` does), not on version numbers — in the Manager GUI repo,
   then sync, so both tools keep one helper.
3. When `robot.flow` exists in the 7.5 port, the Diagram works with no change
   here: the probe sees it.
4. New 7.x syntax the Grid should offer (`VAR`, `GROUP`) is a view change in
   `robot-grid` (shared), gated on a `features` flag from `robot_grid.py`.

## Roadmap

| Step | What | Where |
|---|---|---|
| done | Grid and Diagram as custom editors; edits as text edits; reveal in the text; Robot probe; tests (helpers, views in Chromium, VS Code smoke) | here |
| done | **Run a flow / suite** from the view with the live position (`flow_position.py` writes the position; `flow-view/live.js` draws it); Stop; results per run | here, with the shared listener |
| done | **Run groups** from `testproject.json` (`flow-view/group.js`, `inspect_group`): the Diagram of processes that wait for each other, run together | here |
| done | Dark theme: `--fv-*` / `--rg-*` tokens in the shared `style.js` (light fallbacks, so the Manager GUI is unchanged); the dark palette in `media/host/host.css` | Manager GUI repo + here |
| done | `npm run package` → `.vsix` | here |
| later | Robot Framework 7.5 (above) | `robot_grid.py`, the 7.5 port |
| done | Debug: breakpoints on flow steps and lines, stepping, variables, Debug Console | here + `flow-view/breakpoints.js` |
| later | Publish: an icon, a repository URL, a marketplace or internal gallery entry | here |
