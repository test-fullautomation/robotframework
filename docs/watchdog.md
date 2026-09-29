# Watchdog library

**Find out in ten minutes that your test hung — not in two days.**
{ .lead }

!!! pain "The pain"
    A test running for hours or days can hang silently: a keyword blocks on dead
    hardware, a condition never becomes true, a loop stops making progress.
    Nobody notices until the CI job times out two days later — with no
    diagnostics and no proof-of-life trail in the log.

!!! fix "The fix"
    A supervisor thread watches the test from the side. The test *feeds* it as
    it makes progress; when the feeding stops, the watchdog fires, runs your
    diagnostics keyword, and the test fails with the reason. The whole contract
    is two keyword names — it supervises anything.

```plantuml
!include diagrams/watchdog_fire.puml
```

The `Watchdog` library runs in the fork's own [`THREAD`](thread/lifecycle.md)
block and is deliberately independent of what it supervises: a state machine, a
measurement loop, a flashing procedure.

```robotframework
*** Settings ***
Library    Watchdog
```

## Three mechanisms

| Mechanism | Trigger | Purpose |
|-----------|---------|---------|
| **Heartbeat** | every `heartbeat` interval | proof-of-life message to console and log (elapsed time, feed count); optional `heartbeat_keyword`, e.g. to log system statistics |
| **Stall detection** | no `Feed Watchdog` within `stall_timeout` | detects a flow that stopped making progress |
| **Deadline** | `max_duration` exceeded | bounds the whole run (e.g. 48 h) |

When the watchdog fires it logs a WARN with the reason and runs the
`on_timeout` keyword in the supervisor thread.

## Workflow

```robotframework
*** Test Cases ***
Long Measurement
    Configure Watchdog    max_duration=48h    stall_timeout=10 min
    ...                   heartbeat=5 min     on_timeout=Collect Diagnostics
    THREAD    SUPERVISOR    True
        Run Watchdog
    END
    FOR    ${cycle}    IN RANGE    ${CYCLES}
        Measure One Cycle    ${cycle}
        Feed Watchdog
    END
    Stop Watchdog
    [Teardown]    Watchdog Should Not Have Fired
```

Console during a healthy run:

```text
Watchdog 'DEFAULT' started.
WATCHDOG DEFAULT heartbeat: 5 minutes elapsed, 42 feed(s).
WATCHDOG DEFAULT heartbeat: 10 minutes elapsed, 84 feed(s).
```

When it fires:

```text
[ WARN ] WATCHDOG DEFAULT FIRED: no feed within stall_timeout 10 minutes.
         -> on_timeout keyword 'Collect Diagnostics' runs in the supervisor thread
         -> teardown: Watchdog Should Not Have Fired -> the test FAILS with the reason
```

## Who fails the test?

Failures inside a `THREAD` block do **not** fail the test — the thread runner
turns them into warnings by design. A fired watchdog therefore records its
state and reason, and the **main flow** turns that into a verdict with
`Watchdog Should Not Have Fired` (typically in the test teardown) or, for
negative tests, `Watchdog Should Have Fired`.

## Keywords

| Keyword | Purpose |
|---------|---------|
| `Configure Watchdog    name=DEFAULT    max_duration=    stall_timeout=    heartbeat=1 min    on_timeout=    heartbeat_keyword=` | Create a watchdog. Must be called before its `THREAD` starts. |
| `Run Watchdog    name=DEFAULT` | The supervision loop; blocks until it fires or is stopped, so run it inside a `THREAD`. |
| `Feed Watchdog    name=DEFAULT` | Signal progress; resets the stall timer. Call it from the supervised work. |
| `Stop Watchdog    name=DEFAULT    timeout=10 s` | Stop gracefully and wait for the loop to end; fails if it does not end in time. |
| `Get Watchdog Status    name=DEFAULT` | `CONFIGURED`, `RUNNING`, `STOPPED` or `FIRED`. |
| `Watchdog Should Not Have Fired    name=DEFAULT` | Fail with the recorded reason if it fired. |
| `Watchdog Should Have Fired    name=DEFAULT` | Fail unless it fired — for negative tests. |
| `Reset Watchdogs` | Signal all running supervisors to stop and clear all configurations (teardown). |

Several named watchdogs coexist through `name=`.

## Loose coupling: supervising the StateMachine

Watchdog and [StateMachine](statemachine.md) never reference each other — the
wiring is one keyword name:

```robotframework
Configure Watchdog    name=SM    stall_timeout=15 min    on_timeout=Stop State Machine
THREAD    SM_SUPERVISOR    True
    Run Watchdog    name=SM
END
Run State Machine    initial=INIT    checkpoint=${OUTPUT DIR}${/}sm.json
# The machine's during= keywords call Feed Watchdog. If it stalls, the
# watchdog fires and the machine returns gracefully (checkpoint saved -> resumable).
```

Swap `Stop State Machine` for any other reaction keyword to supervise a
different subsystem — that is the whole integration surface.

## Feeding safely from supervised work

To make a keyword work with or without supervision, feed only while a watchdog
is actually running:

```robotframework
*** Keywords ***
Feed If Running
    ${status}=    Run Keyword And Ignore Error    Get Watchdog Status
    IF    $status[0] == 'PASS' and $status[1] == 'RUNNING'
        Feed Watchdog
    END
```

## Implementation notes

| Aspect | Choice |
|--------|--------|
| Scope | one shared library instance (`GLOBAL`) — the main flow and the supervisor thread see the same state |
| Timing source | `time.monotonic()` — immune to clock adjustments during multi-day runs |
| Poll granularity | 0.2 s (`Event.wait`) → timing precision ±0.2 s, negligible CPU |
| Reaction keywords | `on_timeout` and `heartbeat_keyword` run in the supervisor thread; their messages appear inside the `SUPERVISOR` block in `log.html`, and their failures are logged, never swallowed |
| Fire once | after firing the loop exits — re-arm explicitly with `Configure Watchdog` |
| Cleanup | `Reset Watchdogs` in a teardown |
