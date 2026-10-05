# Robot Framework (RobotFramework AIO fork)

This repository is an **extended fork of [Robot Framework](http://robotframework.org) 7.5**,
the generic open source automation framework for acceptance testing, ATDD and RPA. It forms
the core of *RobotFramework AIO* and extends the upstream framework where large, long-running
test systems need more than the standard feature set:

- **native parallel execution** inside test cases (the `THREAD` keyword with complete result
  reporting),
- **very long, condition-driven test runs** (hours to days) that survive crashes, bound their
  memory and stay debuggable,
- **test plans that run as they are drawn** (flow files: a flowchart of gates, bounded loops
  and recovery, executed directly), and
- **honest verdicts** (the `UNKNOWN` status separates "the product is wrong" from "nothing
  could be judged").

The feature guide with diagrams and examples is in [`docs/`](docs/index.md) and published at
<https://test-fullautomation.github.io/robotframework/>.

Everything from standard Robot Framework 7.5 — syntax, standard libraries, listener and
library APIs, output formats — is preserved; existing test suites run unchanged
(see [Compatibility](#compatibility)).

## What this fork adds

### Parallel execution: the THREAD keyword

`THREAD` is a native block keyword (like `FOR` or `IF`) that runs its body on a worker
thread, in parallel with the main test flow:

```robotframework
*** Test Cases ***
Parallel Measurement
    THREAD    MONITOR    False
        FOR    ${i}    IN RANGE    1000
            Read Sensors
        END
    END
    Flash The Device      # runs while MONITOR measures
    Stop Thread           MONITOR
```

- **Lifecycle scopes** — `daemon=True` threads are stopped automatically when their test
  ends, `daemon=False` threads continue over the following tests until the suite ends.
  Stopping is cooperative with a grace period; threads that cannot stop are abandoned safely
  and reported as `UNKNOWN`.
- **Deterministic control** — `Stop Thread` and `Wait For Thread` BuiltIn keywords.
- **Inter-thread communication** — `Send Thread Notification` / `Wait Thread Notification`
  and re-entrant locks (`Thread RLock Acquire` / `Release`).
- **Complete reporting** — every thread streams into its own output file; at run end the
  results are merged automatically into one `output.xml`, so `log.html` shows each thread as
  a collapsible block with real timestamps and statuses.
- **Execution timeline** — `--timeline timeline.html` generates an additional HTML view with
  one lane per thread on a common time axis (the true parallel interleaving), where every bar
  deep-links into `log.html` (expand, scroll, flash highlight).

### Very long test runs

- **StateMachine standard library** — model a multi-day test (e.g. battery endurance
  cycling) as states with attached keywords and guarded transitions, defined with keywords or
  loaded from YAML/JSON files. The engine checkpoints its progress atomically after *every*
  transition: a crash at hour 40 becomes a **resume**, not a rerun. Includes preemptive state
  timeouts, error routing to diagnostics states with cycle detection, per-state statistics,
  and multiple named machines running concurrently.
- **Watchdog standard library** — a supervisor running in a `THREAD` block with heartbeat
  logging, stall detection (`Feed Watchdog`) and an overall deadline; on firing it runs a
  configurable reaction keyword.
- **Segmented output** — `--segmentoutput 1h` periodically seals the streamed output (main
  and per-thread) into well-formed segment files, so a crash loses at most the given interval
  of log data. Segments are merged back automatically at run end; after a crash the
  `robot.output.segmentmerger` and `robot.output.threadmerger` command line tools recover
  everything written so far.
- **Bounded memory** — the framework's internal message collections are capped or spilled to
  disk, so execution memory stays flat over days-long runs.

### Flow files: the test plan is the test

A bench run is a plan — wait for the chamber, loop the cycle tests for eight hours, recover
when a cycle fails, release the bench. A *flow file* writes that flowchart down as nodes and
edges, and Robot Framework runs it directly: the suite is built in memory at run time, so
there is no `.robot` file to keep in step with the drawing.

```json
{
  "flow": { "name": "Endurance Cycle", "version": 1 },
  "imports": { "libraries": ["bench.py"] },
  "nodes": [
    { "id": "start",   "kind": "start" },
    { "id": "ready",   "kind": "gate",    "keyword": "Chamber Should Be Ready", "timeout": "30 min" },
    { "id": "loop",    "kind": "loop",    "max_loops": 1000, "max_seconds": "8h", "every": "10s" },
    { "id": "cycle",   "kind": "keyword", "keyword": "Run Cycle Tests" },
    { "id": "recover", "kind": "keyword", "keyword": "Recover DUT" },
    { "id": "end",     "kind": "end" }
  ],
  "edges": [
    ["start", "ready"], ["ready", "loop"],
    { "from": "loop",    "to": "cycle",   "label": "body" },
    { "from": "cycle",   "to": "loop",    "label": "next" },
    { "from": "loop",    "to": "recover", "label": "on_failure" },
    { "from": "recover", "to": "loop",    "label": "continue" },
    { "from": "loop",    "to": "end",     "label": "done" }
  ]
}
```

```
robot --parser robot.flow endurance.flow.json            # run it; every robot option applies
robot --parser robot.flow --dryrun endurance.flow.json   # check keywords and arguments without running them
python -m robot.flow validate endurance.flow.json        # check the structure, naming the faulty node
python -m robot.flow render   endurance.flow.json        # print the equivalent .robot text
```

A dry run executes no keyword, but it does import the libraries: module-level code and
library constructors run, so keep bench access out of those.

- **Gates instead of guessed sleeps** — a `gate` polls a keyword until it passes; on timeout
  the message carries the last error and the time waited, and the verdict is `UNKNOWN` (the
  bench was not ready) unless the gate says `fail`.
- **Loops bounded by construction** (`max_loops` and/or `max_seconds`, paced with `every`),
  **recovery** that continues the loop or aborts with the original error, **decisions**, and
  setup/test/teardown **phases**.
- **Sub-flows** — a region of the plan becomes a file of its own and is called as one step,
  with parameters.
- **Two flows as two processes** — with the `robot.flow.signals` library two flows meet and
  run in lockstep through a small signal file of the run; a missing peer ends `UNKNOWN`
  instead of hanging or failing falsely.
- **Pause, stop and restart** — `python -m robot.flow control <store> pause | resume | stop`
  holds or ends a running flow between two steps, with loop deadlines, gate timeouts
  and watchdogs frozen meanwhile; a stopped or crashed flow leaves a checkpoint and the
  next run continues it (finished phases SKIP, the loop goes on with what is left).
- **Editor support** — a JSON Schema (`python -m robot.flow schema`, shipped as
  `robot/flow/flow.schema.json`) gives completion and error marking for flow files.
- **A Python API** (experimental) — `robot.flow.api.Flow` builds the same file from `with`
  blocks.

A flow is a *workflow* (nodes are things the test does, edges mean "then"); the StateMachine
library above is the other model (nodes are what the system is, edges mean "when"). The
validator rejects a flow that is a state machine in disguise.

### The UNKNOWN test status

Standard Robot Framework knows only PASS, FAIL and SKIP — which forces two very different
situations into the same FAIL bucket. This fork separates them: **FAIL means a check ran and
the result contradicted the expectation** (a real verdict about the product), **UNKNOWN means
no verdict could be produced at all**.

Testing a calculator app with `3 + 2`:

| Observed behaviour | Status | Meaning |
|---|---|---|
| Result shown: `5` | **PASS** | product works |
| Result shown: `4` | **FAIL** | product defect — file a bug |
| No result — app crashed, exception in the test, keyword not found, bad arguments | **UNKNOWN** | no statement about the product possible; fix the test, environment or rerun |

This split pays off in triage and reporting: FAILs go to developers as defect candidates,
UNKNOWNs go to the test team — and neither pollutes the other's statistics (all outputs,
logs, reports and counters show `passed / failed / unknown` separately). It matters even more
in long runs, where an abandoned worker thread or an environment hiccup should not masquerade
as a product failure.

UNKNOWN is assigned automatically for broken test data (missing keywords, invalid arguments,
syntax problems) and for unexpected generic exceptions escaping from libraries; ordinary
assertion failures (`Should Be Equal` etc.) remain FAIL. Test libraries can also raise
`robot.errors.UnknownAssertionError` deliberately to state "no verdict".

- **Import errors** make tests UNKNOWN, not FAIL: `--importfailure suite` (the default) marks
  the whole suite, `--importfailure test` only the tests that use the failed import.
- **The return code carries both counts**, so CI can tell a broken bench from a regression
  without opening the log: `rc = (min(unknown, 14) << 4) | min(failed, 15)` — decode with
  `failed = rc & 0x0F` and `unknown = (rc >> 4) & 0x0F`. `0` means no test failed and none
  is unknown; skipped tests are not counted, so a run in which every test was skipped also
  returns `0`.

### Further extensions

- Additional log level `USER` for end-user oriented messages.

## Example

A resumable endurance test with the StateMachine library:

```robotframework
*** Settings ***
Library           StateMachine

*** Test Cases ***
Battery Endurance
    Define State    INIT         enter=Setup DUT           timeout=10 min
    Define State    CHARGING     during=Charge Step        on_error=FAULT
    Define State    DISCHARGING  during=Discharge Step     on_error=FAULT
    Define State    FAULT        enter=Collect Diagnostics    final=True
    Define State    DONE         final=True
    Define Transition    INIT         CHARGING       condition=$DUT_READY
    Define Transition    CHARGING     DONE           condition=$CYCLES >= 500
    Define Transition    CHARGING     DISCHARGING    condition=$SOC >= 80
    Define Transition    DISCHARGING  CHARGING       condition=$SOC <= 20
    Checkpoint Variable  \${CYCLES}
    Run State Machine    initial=INIT    max_duration=48h
    ...                  checkpoint=${OUTPUT DIR}${/}sm.json
```

Interrupted at any point, the next execution resumes from the last completed transition with
all registered variables restored.

## Compatibility

This fork is based on Robot Framework **7.5** and is a superset of it:

- standard test data syntax, standard libraries and public APIs are unchanged;
- generated outputs remain compatible with `rebot` and the existing reporting toolchain;
- suites written for upstream Robot Framework 7.5 run unchanged.

The extensions are additive (new keywords, new command line options, new standard libraries);
test data using them is not portable back to upstream Robot Framework.

## Installation

Install this fork from the repository source:

```
pip install .
```

For detailed instructions, including installing Python, see [INSTALL.rst](INSTALL.rst).
Python 3.8 or newer is required, as for upstream Robot Framework 7.5.

> **Note:** installing the upstream `robotframework` package from PyPI gives you the standard
> framework *without* the extensions described above.

## Documentation

- **Feature guide** — one page per feature, with the problem it solves, diagrams and
  examples: <https://test-fullautomation.github.io/robotframework/>. The sources are in
  [`docs/`](docs/index.md); preview them locally with `python -m properdocs serve`.
- Library documentation for the new standard libraries:
  `python -m robot.libdoc StateMachine StateMachine.html`,
  `python -m robot.libdoc Watchdog Watchdog.html` and
  `python -m robot.libdoc robot.flow.signals FlowSignals.html`
- Command line help for the new options (`--timeline`, `--segmentoutput`,
  `--importfailure`, …): `python -m robot --help`; for flow files:
  `python -m robot.flow --help`
- The *RobotFramework AIO Reference* (built from the separate `robotframework-documentation`
  project) contains dedicated chapters on threading and state machines.
- Acceptance tests under `atest/robot/` double as executable examples, e.g.
  `atest/robot/standard_libraries/statemachine/` and `atest/robot/flow/` (flow files in
  `atest/testdata/flow/`).

## Upstream project and license

Robot Framework is developed by the
[Robot Framework Foundation](http://robotframework.org/foundation); the upstream sources,
issue tracker and downloads are hosted on
[GitHub](https://github.com/robotframework/robotframework) and
[PyPI](https://pypi.python.org/pypi/robotframework). This fork gratefully builds on that work.

Both the upstream framework and the extensions in this repository are licensed under the
[Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0.html).

For guidelines about contributing to this fork see [CONTRIBUTING.rst](CONTRIBUTING.rst).
