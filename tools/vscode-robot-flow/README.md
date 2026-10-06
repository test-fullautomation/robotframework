# Robot Framework Grid & Flow (VS Code)

The Manager GUI's two Robot Framework views, inside VS Code:

- **Grid** — a `.robot` suite or `.resource` file as a grid: one row per
  statement, the keyword first, then one cell per argument labelled with the
  parameter it fills. Every keyword is resolved the way the run will resolve
  it (BuiltIn, the file's libraries and resources and theirs, Libdoc for
  arguments and documentation); unknown keywords, values a keyword does not
  take and missing required arguments are marked. Click a line number to find
  it in the text; click a step to change it (keyword completion, one input per
  parameter, FOR / IF / WHILE / TRY / THREAD blocks, settings, variables).
- **Flow Diagram** — a RobotFramework AIO flow file (`*.flow.json`) as the
  diagram the fork builds from it: one lane per phase, loops and tries as
  frames with their recovery, decisions as diamonds, sub-flows that open in
  place. Click a step to find it in the text; *Edit flow* drags steps in from
  a palette, moves, changes, deletes or wraps them — every change is checked
  by the fork's validator first.

Both are editors of the **text document**: what you change in a view is an
ordinary text edit (it marks the file dirty, Save and Ctrl+Z work as usual),
and what you type in the text redraws the view, unsaved changes included.

- **Runs** — run a suite or a flow from its view, the editor title bar or the
  Explorer. The Diagram follows the run: the running step pulses, every step
  that ran shows its pass / fail count, *Motion* chooses how it follows
  (trail, hop, off). The toolbar shows the status and opens the Output, the
  Log and the Report when the run is done.
- **Run groups** — the `groups` of a Manager GUI test project
  (`testproject.json`): the members' Diagrams side by side with the signals
  where one waits for another, and a Run that starts all members together on
  one shared signal store, each drawn live.
- **Robot Framework text** — `.robot` and `.resource` files open as the
  *Robot Framework* language, coloured by your theme: section headers, test
  and keyword names, keyword calls, settings and `[Settings]`, control words
  (FOR, IF, WHILE, TRY, THREAD, …), variables, named arguments, comments.
  (If another Robot Framework extension is installed, VS Code uses one of
  the two grammars.)
- **Go to Definition** (F12, Ctrl+Click) — on a keyword call in a suite or
  resource, or on a `"keyword"` step of a flow file, it opens where Robot
  finds that keyword: the file's own keywords, its resources and theirs, its
  libraries (the Python source), BuiltIn. Robot's name matching applies
  (case, spaces, `Given`/`When`/`Then`). On an import or a sub-flow
  (`"file": "sub/x.flow.json"`) it opens that file. Unsaved text counts.
- **Debug** — run a suite or a flow with breakpoints (see *Debug* below):
  stop at a flow step or a line, step into resource keywords and sub-flows,
  see variables, run keywords in the Debug Console. The Diagram shows the
  breakpoints and the step the run is paused at.

The views follow the VS Code theme: light themes draw them on white paper,
dark and high-contrast themes in a dark palette on the editor background.

## Install

```bash
code --install-extension robot-flow-0.1.0.vsix
```

or *Extensions → … → Install from VSIX…*. Build the `.vsix` with
`npm run package` (see Develop).

## Use

- Open a `.robot` / `.resource` file and click **Open Grid to the Side** in
  the editor title bar (or *Open With… → Robot Grid*).
- Open a `*.flow.json` and click **Open Flow Diagram to the Side** (or *Open
  With… → Flow Diagram*).
- **Run** (▶ in the editor title bar, in the view's toolbar, or the
  Explorer's context menu) runs the suite or flow; **Stop** ends it after the
  running keyword (a flow: at its next step, with a checkpoint; see *Pause,
  resume, stop and continue a flow*). Each run writes `output.xml`, `log.html` and `report.html`
  to its own folder under `robotFlow.resultsFolder`.
- **Robot Flow: Open Run Group Diagram…** (or right-click a
  `testproject.json`) picks a group of the workspace's test projects.
- A file inside a test project is read and run with the project's `run`
  settings (interpreter, `pythonpath`, `args`, `env`), the same way the
  Manager GUI runs it.
- **Debug** (in the editor title bar next to Run, in the view's toolbar, the
  Explorer's context menu, or F5) — see below.
- **Robot Flow: Show Which Robot Framework Is Used** reports the interpreter,
  the Robot Framework version and location, and whether the Diagram can be
  drawn with it.

## Debug

**Debug** runs the suite or flow like Run, with VS Code's debugger attached
(the debugger type is `robotflow`; F5 with no `launch.json` debugs the open
file).

- **Breakpoints in a flow**: F9 or a click in the gutter on any line of a
  step in the `.flow.json` text, or a click on the dot at the top-left corner
  of a step in the Diagram (faint while the pointer is on the step, red once
  set). Both are the same VS Code breakpoint. A breakpoint in a sub-flow's
  file stops in every call of that sub-flow from the debugged flow.
- **Breakpoints in a suite or resource**: on the line of a keyword call, a
  `FOR`, an `IF`, ….
- **Failed keyword** (Breakpoints view, exception filters): stop where a
  keyword fails, before its callers report the failure.
- When it stops, the **Diagram** marks the step in amber and the toolbar
  says *Paused*; the **Call Stack** lists the flow steps, sub-flow steps
  (`<sub-flow>::<step>`) and keywords with their files and lines.
- **Continue**, **Step Over** (the next step or keyword at this level),
  **Step Into** (into the keyword or sub-flow), **Step Out**, **Pause** (at
  the next step), **Stop** (graceful: teardowns run, log and report are
  written).
- **Variables**: everything Robot has in scope, and the current keyword's
  arguments; lists and dictionaries expand. Hover a `${variable}` in a Robot
  file for its value.
- **Debug Console**: `${var}`, `${var}[0]`, `${var.attr}` show values; any
  other line runs a keyword, cells separated by two spaces —
  `Log To Console  hello`, `Set Variable  42`.
- **Into Python**: with the *Python Debugger* extension (`ms-python.debugpy`)
  installed and debugpy importable by the run's interpreter (else the
  extension's own copy is used), the run also starts debugpy and VS Code
  attaches its Python debugger as a second session under the Robot one.
  **Step Into** on a keyword of your own Python library stops at the first
  line of its function, with Python's variables, stepping and call stack;
  when the function returns, the Robot session stops at the next step.
  Breakpoints in your `.py` files work too. Without the Python Debugger,
  Step Into still goes into the function, with the listener's own stepper
  (Step Over / Into / Out, locals, Python expressions in the Debug Console).
  Robot Framework's own libraries
  (BuiltIn, Collections, …) and installed packages are stepped over, unless
  `"justMyCode": false`. `"python": false` debugs only the Robot side.

A `launch.json` entry can set the target, variables, more Robot options and
stop on entry:

```json
{
  "type": "robotflow",
  "request": "launch",
  "name": "Debug the climate profile",
  "target": "${workspaceFolder}/flows/climate_profile.flow.json",
  "variables": { "CYCLES": "2" },
  "args": ["--loglevel", "DEBUG"],
  "stopOnEntry": false,
  "python": true,
  "justMyCode": true
}
```

Only the main thread stops: THREAD blocks (the fork) run on. A flow stops at
its steps, not inside the keywords the fork generates around phases and
sub-flows.

## Pause, resume, stop and continue a flow

A running flow takes the fork's own flow commands (`python -m robot.flow
control`) through its signal store, without the debugger. They need the
RobotFramework AIO fork with flow control (`robotFlow.robotSource`).

- **Pause** (the view's toolbar, *Robot Flow: Pause the Flow*, or a click on
  the flow's entry in the status bar) holds the flow at its next step, loop
  iteration or gate poll. A keyword that is running, such as a `Sleep`, ends
  first. While paused, loop deadlines, gate timeouts and watchdogs do not
  advance. **Resume** goes on.
- **Stop** on a flow leaves at the next step: the test ends UNKNOWN, the
  teardown runs and a checkpoint (`<flow>.checkpoint.json`) is written to the
  run's folder. If the flow has not ended a minute later, the stop file
  follows. A second Stop uses the stop file at once (the running keyword
  ends, teardowns run), and a third ends the process. A suite, and a
  debugged run, stop through the stop file.
- **Continue** (the toolbar after the run, the run's notification, or
  *Robot Flow: Continue the Stopped Run from Its Checkpoint*) starts a new
  run from that checkpoint: finished test phases are skipped and the
  interrupted loop goes on with what is left of it and with its saved
  variables. The setup phase runs again.
- **Step** (the toolbar, or *Robot Flow: Run in Step Mode*) runs the flow in
  step mode: it pauses before every step of its test phases, and **Next step**
  runs one more. The setup phase is not stepped.
- In a **run group**, Pause, Resume and Stop act on every member, and each
  member has its own ⏸ / ▶ in the toolbar. Pausing one member alone lets
  the others' gates that wait for it run into their timeouts; pause the
  whole group for members that run in lockstep. A group cannot be continued
  from checkpoints.

## Settings

| Setting | Meaning |
|---|---|
| `robotFlow.python` | Interpreter with Robot Framework. Empty: the one selected in the Python extension, else `python`. |
| `robotFlow.robotSource` | A Robot Framework source tree (`…/src`) put first on `PYTHONPATH`. The Diagram needs the RobotFramework AIO fork (`robot.flow`), the `src` folder of a checkout of this repository (e.g. `C:/work/robotframework/src`). |
| `robotFlow.pythonPath` | More `PYTHONPATH` folders (libraries the suites import). |
| `robotFlow.timeoutSeconds` | How long one read or edit may take (default 60). |
| `robotFlow.resultsFolder` | Where runs write their results, one folder per run (default `results`, relative to the test project, else the workspace folder). |
| `robotFlow.runArgs` | More Robot Framework options for runs, after the project's `run.args`. |
| `robotFlow.refreshDelay` | Milliseconds after typing before the views read the text again (default 400). |

`${workspaceFolder}` is expanded in all paths.

## Robot Framework versions

| Robot Framework | Grid | Diagram |
|---|---|---|
| 6.1, RobotFramework AIO fork | yes, with THREAD blocks | yes |
| 6.1, stock | yes | no: needs `robot.flow` — set `robotFlow.robotSource` to the fork |
| 7.5 port (`robotframework_7.5`, in progress) | yes (read and edit, with THREAD) | edit yes; reading a flow whose keywords have `[Arguments]` fails in the port's `robot.flow` — see `docs/ARCHITECTURE.md` in the source |

The extension never hard-codes a version: it asks the configured Robot what
it has (`robot.flow`, THREAD) and says what is missing.

## Develop

The extension lives in `tools/vscode-robot-flow/` of the RobotFramework AIO
fork; run these there. The tests use this repository's own `src` as the
Robot Framework (`ROBOT_FLOW_SRC` to use another).

```bash
npm install
npm run compile          # TypeScript -> out/
npm test                 # the Python side with a real Robot (ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC)
npm run test:webview     # both views rendered in headless Edge/Chromium -> test/webview/out/*.png
npm run test:vscode      # smoke test in a real VS Code (VSCODE_EXE, ROBOT_FLOW_DEMO)
npm run test:grammar     # the Robot grammar, tokenized by VS Code's own TextMate engine
npm run grammar          # rebuild syntaxes/robotframework.tmLanguage.json from scripts/build-grammar.py
npm run sync             # refresh the shared code from the Manager GUI repo (MB_GUI_REPO)
npm run package          # robot-flow-<version>.vsix (needs vsce: npm install -g @vscode/vsce)
```

`test:webview` renders every view twice, in a light and in a dark theme
(`<view>.png`, `<view>-dark.png`). Some `npm test` cases, `test:webview` and
`test:vscode` open the demo projects (`bench_gui/`, `climate_endurance/`):
set `ROBOT_FLOW_DEMO` to their folder; without it those `npm test` cases skip.

Press **F5** in VS Code (this folder open) to start an Extension Development
Host on `test/fixtures`.

The views and the Python helpers are **shared with the Manager GUI**
([python-microservice-base](https://github.com/test-fullautomation/python-microservice-base)),
not forked: `media/vendor/` and `python/` are copies made by `npm run sync`
(`media/vendor/SOURCE.json` names the commit). Change them in the Manager
GUI repository, then sync. The copies are committed, so a build needs only
this repository. See `docs/ARCHITECTURE.md` in the source.

### Builds

`.github/workflows/vscode_extension.yml` builds the extension when a pull
request that changes it (or `src/robot/flow/`) is merged into `develop_6.1`:
it runs `npm test` against this repository's `src` and packages the `.vsix`,
which the run keeps as an artifact (*Actions → VS Code extension → the run →
Artifacts*). Pull requests run the same build, so a change is checked before
it is merged; it can also be started by hand (*Run workflow*).
