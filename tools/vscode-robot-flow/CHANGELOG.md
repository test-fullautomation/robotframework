# Changelog

## 0.1.0 — not released

- *Robot Runs*, a view in the Explorer: the runs of the results folders,
  running ones first with where they are and Pause / Resume / Stop inline,
  then newest first with status, time, duration and the failed count. A run
  expands to its phases or tests (streamed from `output.xml` by
  `run_summary.py`, no Robot needed), Log / Report / Flow report, its
  checkpoint and snapshots with *Continue from here* (a new run from that
  file with the original run's variables), the Output and the folder. Read
  from disk, so it survives a restart and lists command-line runs; runs
  started here write a `run.json` next to their output. Delete a run, or
  the finished runs older than a day, a week or a month.
- The *Run page*: a click on a run opens it as a tab in the editor area,
  with the header, the buttons (Log, Report, Output, Continue from each
  checkpoint and snapshot; Pause / Resume, Stop and Snapshot while it runs),
  the phases or tests as a table with time bars and the failure messages in
  full, one tab per member of a group, and for a flow run the flow report
  on the page itself. The page follows a running run.
- The flow report on demand: a flow run that wrote none
  (`robotFlow.flowReport` is off by default) gets it from `output.xml` with
  the fork's `robot.flow report` when asked for, in the tree (*Flow report ·
  written on demand*) and on the Run page (*Write the flow report*).
- Completion in the text of flow files: the fork's JSON schema registered for
  `*.flow.json` (keys, kinds, enum values), node ids in edges, the labels the
  source node allows, keywords of the imports with their parameters (through
  `robot_grid.py --catalog`), variables after `${`, sub-flow files. The same
  in the Diagram's step editor: keywords in the *Keyword* field with the
  parameters as the arguments' placeholder, variables in every value field,
  flow files in the sub-flow field (the extension's host layer; the shared
  view is unchanged).
- Fixed: *Undo* in the Diagram's edit mode was always disabled, because the
  change event of the extension's own edit was taken for the user typing.
- The fork's `parallel` node (branches run side by side as threads and join)
  is validated, run and reported; the Diagram draws it as one box with its
  branches listed in the inspection data until the flow view learns the kind.

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
  `NAME=value` pairs as `--variable`. *Set…* and *Snapshot* act on a running
  flow through its signal store.
- Run groups of a test project (`testproject.json`): the members' Diagrams
  with their meeting points, run together on one signal store. A project
  whose `run.env` names a store URL (`ROBOT_FLOW_SIGNALS=http://hub:8765/run`,
  the fork's signal server for members on several machines) is followed
  through `python -m robot.flow control <store> status --json`. *Add to Run
  Group…* on a flow file and the panel's *Member…* / *Group…* edit the
  groups (only the `groups` value of the file changes); a JSON schema for
  `testproject.json`.
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
