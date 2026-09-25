# Getting started

## Installation

RobotFramework AIO ships the fork as its bundled Robot Framework. To use it
standalone, install from the fork's source tree:

```bash
git clone https://github.com/test-fullautomation/robotframework.git
cd robotframework
pip install -e .          # or add src/ to PYTHONPATH
```

The fork targets **CPython 3.9–3.13**. On Python 3.13 the standard `Telnet`
library needs the `standard-telnetlib` backport (Python 3.13 removed the
`telnetlib` module); install it only if you use that library.

## A first threaded test

```robotframework
*** Test Cases ***
Hello Thread
    THREAD    WORKER    ${False}
        Log    running in a background worker
        Sleep    1s
    END
    Log    the main thread continues immediately
    Wait For Thread    WORKER    timeout=5s
```

Run it with the timeline enabled:

```bash
robot --timeline timeline.html --outputdir results hello_thread.robot
```

Then open `results/timeline.html` to see the main thread and `WORKER` as
separate lanes, and `results/log.html` where the worker's keywords appear as
their own block.

## Command-line options added by the fork

| Option | Purpose | Details |
|--------|---------|---------|
| `--timeline <file>` | Write an interactive per-thread execution timeline. | [Timeline](thread/merging-timeline.md#timeline) |
| `--segmentoutput <time>` | Seal `output.xml` into well-formed segments every `<time>` (e.g. `30s`, `5min`). | [Segmented output](long-running.md#segmented-output) |
| `--importfailure suite\|test` | Choose whether an import error makes the whole suite UNKNOWN (`suite`, default) or only the tests that use the failed import (`test`). | [UNKNOWN status](unknown-status.md#import-failures) |

All other Robot Framework options behave as upstream.

## New libraries

Two standard libraries are added and imported like any other:

```robotframework
*** Settings ***
Library    StateMachine
Library    Watchdog
```

See [StateMachine](statemachine.md) and [Watchdog](watchdog.md).
