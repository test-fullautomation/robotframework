# THREAD keyword — lifecycle and scopes

**Run the stimulus, the monitor and the test at the same time — in one suite.**
{ .lead }

!!! pain "The pain"
    Real bench tests are parallel: something feeds CAN frames to the device
    while the test measures, a monitor samples the current, a logger records
    the bus. Stock Robot Framework runs one keyword at a time, so this ends up in
    external scripts, background processes nobody stops, and logs that never
    reach the report.

!!! fix "The fix"
    A `THREAD` block runs its body in the background while the test continues.
    You choose how long it lives — until the test ends, or until the suite ends —
    and the framework stops it for you. Its keywords appear in `log.html` like
    any other, and the [timeline](merging-timeline.md#execution-timeline) shows
    who ran when.

```plantuml
!include diagrams/thread_parallel.puml
```

The `THREAD` keyword is a block keyword, closed by `END`, and takes a **name**
and a **daemon** flag:

```robotframework
*** Test Cases ***
Example
    THREAD    WORKER    ${False}
        Do Something Long
    END
    Log    main thread continues here immediately
```

The name is how you address the thread (`Wait For Thread`, `Stop Thread`,
`Send Thread Notification`) and how it is labelled in the log and the timeline.
It need not be unique: a `THREAD` inside a `FOR` loop, or the same name in two
tests, gives several threads of that name, each with its own variables, its own
log and its own place in the output. A keyword given the name addresses the
newest of them still running. The daemon flag selects the thread's
**lifecycle scope**.

Each thread runs with runtime state of its own: a keyword timeout inside a
worker ends that worker's keyword and nothing in the main thread, and a
`robot:continue-on-failure` tag on a worker's keyword does not change what the
main thread does after a failure. A `THREAD` may start another `THREAD`; the
inner block appears inside the outer one in the log.

## Scopes

```plantuml
!include diagrams/thread_scopes.puml
```

| | `daemon=True` — **TEST** scope (default) | `daemon=False` — **SUITE** scope |
|-|-|-|
| At its test's end | stopped by the reaper | keeps running into the following tests |
| At suite end | (already handled) | stopped by the reaper |
| Typical use | a bounded helper or parallel step | a continuous monitor or stimulator spanning tests |

Workers are always Python daemon threads, so they can never block the run or
interpreter exit; their real lifecycle is owned by the **scope reaper** in the
suite runner. A worker is any thread other than the one executing the suite,
so `robot.run()` may itself be called from a thread of your own program.

## The stop ladder

Python cannot kill a thread, so "terminate" honestly means
**request → grace → abandon**:

1. **Request** — the thread's stop event is set. The body runner checks it
   before every keyword, so the worker stops at the next keyword boundary.
2. **Grace** — the reaper waits up to **5 seconds**
   (`SuiteRunner.thread_stop_grace`) for the worker to finish.
3. **Abandon** — a worker stuck inside one long keyword (e.g. `Sleep 60s`) cannot
   be interrupted cooperatively. It is abandoned and, being a Python daemon,
   dies with the process.

The reaper runs after each test (TEST scope) and after each suite (everything
remaining). Registration lives in the execution context: worker, stop event,
scope and owner test.

## What you see

- **Stopped at scope end** is normal cleanup: an INFO
  `Thread 'X' stopped because its scope ended.` inside the thread's log block;
  its real status (usually PASS) is kept.
- **Abandoned** is a WARN in the errors section, and the merger reports the
  thread as **UNKNOWN** with `Thread did not finish before its scope ended.` — an
  unstoppable worker is visible in the report rather than silently lost.
- Every later test logs `Thread 'X' started in '…' is still running.`, so a
  surviving SUITE thread is visible wherever it might interfere.
- The [timeline](merging-timeline.md#execution-timeline) marks a test's own end
  with a dashed red line when thread bars continue past it.
- **Test verdicts are never affected** — thread problems stay warnings. An
  exception inside a `THREAD` body does not fail the test; assert explicitly
  when a thread's outcome matters.

## Deterministic control

```robotframework
Stop Thread        SUITEWORKER    timeout=10 s    # request a stop and wait; fail if it is stuck
Wait For Thread    SUITEWORKER    timeout=1 min   # join without stopping; fail if still running
```

`Wait For Thread` is the way to make a thread's result part of the test
verdict: a thread whose keyword failed only logs a warning while it runs (its
`THREAD` block shows FAIL in the log), and `Wait For Thread` on it fails with
that message, with the thread's own kind of failure (a thread that could not
judge, a missing keyword say, gives UNKNOWN). `fail_on_error=False` only logs
it, for a thread whose failure the test judges itself afterwards. The failure
is reported once, for the thread that was waited for; the failure of a
test-scoped thread nobody waited for is forgotten when its test ends.

!!! note "Changed behaviour"
    Earlier versions let `Wait For Thread` pass whatever the thread did. A
    suite that tolerated a failing thread on purpose now needs
    `fail_on_error=False` on that `Wait For Thread`.

## Variables set in a thread

A worker starts with a copy of the variables in scope where the `THREAD`
began, and what it sets with plain assignment stays its own. `Set Test
Variable`, `Set Suite Variable` and `Set Global Variable` in a worker reach the
main thread as well, which is how a worker hands a result back:

```robotframework
Measure In The Background
    THREAD    METER    True
        ${v}=    Read Voltage
        Set Test Variable    ${VOLTAGE}    ${v}
    END
    Wait For Thread    METER
    Should Be True    ${VOLTAGE} > 11.5
```

One guard keeps this honest: a `Set Test Variable` from a suite-scoped thread
that outlives the test it started in is dropped, with a note at DEBUG level,
instead of landing in whatever test runs next. Suite and global variables are
always applied.

!!! note "Timeouts inside a worker"
    A keyword timeout inside a `THREAD` worker applies on every platform: the
    worker runs it with a timer (the way Windows runs every timeout), since
    the signal-based timeout of Linux and macOS reaches the main thread only.
    The test timeout belongs to the main thread; a worker starts without it.
    On an interpreter without CPython's C API a worker runs without timeouts.

## Variable scoping

Each worker gets its **own copy** of the variables that existed when the thread
was started (snapshotted in the spawning thread, so there is no race with the
main thread's own scope changes). Variables set inside a worker stay local to
that worker; use [thread notifications](synchronisation.md) to hand data back
to the main thread.

!!! warning "SUITE-scoped threads outlive their test's variables"
    A thread that outlives its test still holds that test's variable scope.
    Rule of thumb: after the origin test ends, touch only suite or global
    variables from the thread.

!!! warning "Guards inside a worker"
    A condition evaluated inside a worker thread does not see variable writes
    made in the same worker after the guard was set up. When a StateMachine runs
    inside a THREAD, control it from the main flow with `Stop State Machine`
    rather than a same-thread variable guard.

## Where to go next

- [Result merging and timeline](merging-timeline.md) — how a worker's keywords
  appear in `log.html` and the interactive timeline.
- [Synchronisation](synchronisation.md) — notifications and reentrant locks.
