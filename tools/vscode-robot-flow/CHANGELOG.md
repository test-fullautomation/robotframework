# Changelog

## 0.1.0 — not released

- Grid of `.robot` / `.resource` files and Diagram of `*.flow.json` files as
  VS Code editors of the text, with the Manager GUI's own views and helpers.
- Edits made in a view are text edits (dirty, Save, Ctrl+Z); typing redraws
  the views; click a row or step to find it in the text.
- *Show Which Robot Framework Is Used*; the Diagram says when the configured
  Robot has no `robot.flow`.
- Run a suite or flow from its view, the title bar or the Explorer; the
  Diagram follows the run live (running step, counts, trail); Stop; Output,
  Log and Report of each run, and the Flow report of a flow run
  (`robotFlow.flowReport`, off by default). *Run with Variables…* passes
  `NAME=value` pairs as `--variable`.
- Run groups of a test project (`testproject.json`): the members' Diagrams
  with their meeting points, run together on one signal store.
- Files in a test project are read and run with the project's `run` settings.
- Dark and high-contrast themes: the views' colours are theme tokens (in the
  Manager GUI's shared styles, light by default), given a dark palette here.
- `npm run package` builds the `.vsix`.
- Paths in the settings and `testproject.json` may be quoted (Windows "Copy as path"): `"C:\Program Files\...\python.exe"` works.
- Robot Framework language for `.robot` / `.resource` with a grammar: sections, test and keyword
  names, calls, settings, control words, variables, named arguments, comments.
- Go to Definition (F12, Ctrl+Click) for keyword calls in suites and resources and for keyword
  steps of flow files, resolved like the Grid and the run (`robot_grid.py --define`); imports and
  sub-flows open their file.
- Debug (`robotflow` debugger): breakpoints on flow steps (in the text and on the Diagram's
  step dots, sub-flows included) and on lines of suites and resources, stop on a failed keyword,
  Continue / Step Over / Into / Out / Pause / Stop, call stack across flows, sub-flows and
  keywords, variables, Debug Console (variables and keyword calls), hover. The Diagram marks the
  paused step; Debug in the editor title, the toolbar and the Explorer.
- Debug into Python: debugpy in the run and VS Code's Python debugger attached as a child
  session; Step Into a keyword of the user's Python library stops in its function, and the Robot
  session stops at the next step when it returns; breakpoints in `.py` files. Launch options
  `python` and `justMyCode`. Without the Python Debugger, the listener's own stepper goes into the
  function. The debug listener (`flow_debug.py`) is now shared with the Manager GUI (synced).
- Flow control without the debugger (the fork's `robot.flow control`): Pause / Resume a running
  flow, and each member of a run group on its own; Stop at the next step with a checkpoint, then
  Continue a new run from it; Step mode (a pause before every step, Next step). The flow's state
  in the toolbar and the status bar.
- Fixed: the debug listener failed on `FOR`, `IF` and other control structures
  (`'tuple' object has no attribute 'strip'`), which also put Step Over / Out one level off after them.
