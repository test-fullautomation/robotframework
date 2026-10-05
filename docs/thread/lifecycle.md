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

The name identifies the thread for synchronisation, merging and the timeline.
The daemon flag selects the thread's **lifecycle scope**.

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

`Wait For Thread` followed by an assertion is the way to make a thread's result
part of the test verdict.

!!! warning "Timeouts inside a worker"
    On Linux and macOS keyword and test timeouts rely on signals, which only
    work in the thread executing the suite. Keywords run by a `THREAD` worker
    there run without timeouts; on Windows timeouts apply as usual.

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
