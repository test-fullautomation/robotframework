# Robot Framework (RobotFramework AIO fork)

This repository is an **extended fork of [Robot Framework](http://robotframework.org) 6.1**,
the generic open source automation framework for acceptance testing, ATDD and RPA. It forms
the core of *RobotFramework AIO* and extends the upstream framework where large, long-running
test systems need more than the standard feature set:

- **native parallel execution** inside test cases (the `THREAD` keyword with complete result
  reporting), and
- **very long, condition-driven test runs** (hours to days) that survive crashes, bound their
  memory and stay debuggable.

Everything from standard Robot Framework 6.1 — syntax, standard libraries, listener and
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

This fork is based on Robot Framework **6.1** and is a superset of it:

- standard test data syntax, standard libraries and public APIs are unchanged;
- generated outputs remain compatible with `rebot` and the existing reporting toolchain;
- suites written for upstream Robot Framework 6.1 run unchanged.

The extensions are additive (new keywords, new command line options, new standard libraries);
test data using them is not portable back to upstream Robot Framework.

## Installation

Install this fork from the repository source:

```
pip install .
```

For detailed instructions, including installing Python, see [INSTALL.rst](INSTALL.rst).
Python 3.6 or newer is required.

> **Note:** installing the upstream `robotframework` package from PyPI gives you the standard
> framework *without* the extensions described above.

## Documentation

- Library documentation for the new standard libraries:
  `python -m robot.libdoc StateMachine StateMachine.html` and
  `python -m robot.libdoc Watchdog Watchdog.html`
- Command line help for the new options (`--timeline`, `--segmentoutput`, …):
  `python -m robot --help`
- The *RobotFramework AIO Reference* (built from the separate `robotframework-documentation`
  project) contains dedicated chapters on threading and state machines.
- Acceptance tests under `atest/robot/` double as executable examples, e.g.
  `atest/robot/standard_libraries/statemachine/`.

## Upstream project and license

Robot Framework is developed by the
[Robot Framework Foundation](http://robotframework.org/foundation); the upstream sources,
issue tracker and downloads are hosted on
[GitHub](https://github.com/robotframework/robotframework) and
[PyPI](https://pypi.python.org/pypi/robotframework). This fork gratefully builds on that work.

Both the upstream framework and the extensions in this repository are licensed under the
[Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0.html).

For guidelines about contributing to this fork see [CONTRIBUTING.rst](CONTRIBUTING.rst).
