# RobotFramework AIO — Feature Guide

This is the feature documentation for the **RobotFramework AIO** core, a fork of
[Robot Framework](https://robotframework.org) 6.1 maintained by the
[test-fullautomation](https://github.com/test-fullautomation) project. It adds
capabilities aimed at **long-running, condition-driven and concurrent tests** —
the kind of endurance and hardware-in-the-loop scenarios that run for hours or
days rather than seconds.

Everything here is additive: existing Robot Framework test suites keep working
unchanged. The features below are opt-in through new keywords, libraries and
command-line options.

## What the fork adds

| Feature | What it is for | Start here |
|---------|----------------|------------|
| **UNKNOWN status** | A fourth test status beside PASS/FAIL/SKIP for results that are neither a pass nor a genuine assertion failure — a crash, a missing keyword, an environment problem. | [UNKNOWN status](unknown-status.md) |
| **THREAD keyword** | Run part of a test in a background worker thread, with test- or suite-scoped lifecycles and cooperative stopping. | [Lifecycle and scopes](thread/lifecycle.md) |
| **Thread result merging + timeline** | Per-thread output is merged back into `log.html`/`report.html`, and `--timeline` renders an interactive execution timeline. | [Result merging and timeline](thread/merging-timeline.md) |
| **StateMachine library** | State-machine-driven execution for multi-day tests, with guarded transitions, timeouts, `on_error` routing and crash-safe checkpoints. | [StateMachine](statemachine.md) |
| **Watchdog library** | Supervise worker threads: heartbeat, stall detection and reporting. | [Watchdog](watchdog.md) |
| **Segmented output** | Periodically seal `output.xml` into well-formed segments so a crash during a very long run loses at most one interval of log data. | [Long-running tests](long-running.md) |
| **Bounded memory** | Caps on the message cache and error section (with spill-to-disk) so days-long runs do not grow without bound. | [Long-running tests](long-running.md) |
| **Return-code encoding** | The process exit code encodes both the failed and the UNKNOWN test counts. | [UNKNOWN status](unknown-status.md#return-code) |

## Why UNKNOWN matters (the one-minute version)

Classic Robot Framework has two outcomes for a test that did not pass: FAIL
(an assertion was checked and did not hold) and SKIP. But a test can also end
for reasons that are **neither** — the system under test crashed, a keyword or
variable was missing, an import broke, an unexpected exception was raised. Those
are not "the product is wrong", they are "we could not tell". Reporting them as
FAIL hides real regressions in the noise; this fork reports them as **UNKNOWN**.

!!! note "Calculator example"
    Testing `3 + 2`: if the result is `5` the test **passes**; if it is `4` the
    product is wrong and the test **fails**; if the calculator crashes or shows
    nothing, there is no result to judge — that is **UNKNOWN**.

See [UNKNOWN status](unknown-status.md) for the full model and the command-line
options that control it.

## Getting started

Head to [Getting started](getting-started.md) for installation and a first run,
then pick the feature you need from the sidebar.
