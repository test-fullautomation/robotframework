# StateMachine library

**A crash at hour 40 becomes a resume, not a rerun.**
{ .lead }

!!! pain "The pain"
    A battery endurance test charges to 80 %, discharges to 20 %, and repeats
    500 times — about 48 hours. As a keyword list it becomes a `FOR` loop full of
    nested `IF`s nobody can read. Then the bench PC reboots at hour 40, and the
    only option is to start again from zero.

!!! fix "The fix"
    Describe what the device *is* — states — and when it moves on — guarded
    transitions. The engine runs them, and after every transition it writes a
    checkpoint. The next run picks up exactly where the last one died.

## Draw the behaviour, run the drawing

```plantuml
!include diagrams/sm_battery.puml
```

Every box is a `Define State` (or an entry in a JSON/YAML file), every arrow a
`Define Transition` with its condition. Dashed arrows are the automatic error
route: a failing keyword or a stuck state goes to `FAULT` and collects
diagnostics instead of killing the run.

## Survive the crash

```plantuml
!include diagrams/sm_crash_resume.puml
```

Run 2 is the same command. With `resume=AUTO` (the default) it finds the
checkpoint and continues in the state, with the counters and the registered
variables, that run 1 had reached — at most one transition is lost.

## What else you get

- **No typo at hour 30** — unknown states, dead-end states and bad file options
  are rejected before the run starts.
- **No stuck state** — a per-state `timeout` interrupts even a hanging keyword
  and routes to `on_error`.
- **No endless ping-pong** — `max_visits` and `on_error` cycle detection stop a
  machine with wrong guards, and say why.
- **Readable two-day logs** — every visit is a `STATE CHARGING (visit #37)` block,
  with per-state statistics at the end.
- **Several machines at once** — named machines, one per `THREAD`, controllable
  across tests.

```robotframework
*** Settings ***
Library    StateMachine
```

## The smallest machine

```robotframework
*** Test Cases ***
My First State Machine
    Define State         HELLO    enter=Say Hello
    Define State         WORLD    enter=Say World    final=True
    Define Transition    HELLO    WORLD
    Run State Machine    initial=HELLO

*** Keywords ***
Say Hello
    Log    Hello
Say World
    Log    World
```

A state runs keywords; a transition moves between states, optionally when a
condition holds. Entering a `final` state completes the machine.

## A real machine: battery endurance

```robotframework
*** Test Cases ***
Endurance Test
    Define State    INIT         enter=Setup DUT            timeout=10 min
    Define State    CHARGING     enter=Start Charging       on_error=FAULT
    Define State    DISCHARGING  enter=Start Discharging    on_error=FAULT
    Define State    FAULT        enter=Collect Diagnostics  final=True
    Define State    DONE         final=True
    Define Transition    INIT         CHARGING       condition=$DUT_READY
    Define Transition    CHARGING     DISCHARGING    condition=$SOC >= 80
    Define Transition    DISCHARGING  DONE           condition=$CYCLES >= 500
    Define Transition    DISCHARGING  CHARGING       condition=$SOC <= 20
    Checkpoint Variable  \${CYCLES}
    Run State Machine    initial=INIT    max_duration=48h
    ...                  checkpoint=${OUTPUT DIR}${/}sm.json
```

`$DUT_READY` says the device under test is initialised, `$SOC` is the battery's
state of charge in percent, `$CYCLES` counts completed charge/discharge cycles.

## States

`Define State  name  enter=  during=  exit=  timeout=  on_error=  final=  machine=`

| Argument | Meaning |
|----------|---------|
| `enter` | Keyword run once when the state is entered. |
| `during` | Keyword run on every poll while the state is active. |
| `exit` | Keyword run once when leaving the state via a transition. |
| `timeout` | Maximum duration of one visit (e.g. `30s`, `10 min`); exceeding it routes to `on_error`. |
| `on_error` | State to go to if a keyword fails or the timeout is exceeded. |
| `final` | Entering this state completes the machine. |

Each of `enter`/`during`/`exit` is a keyword name, or a list of keyword name
followed by its arguments:

```robotframework
${enter}=       Create List    Start Charging    fast    ${AMPS}
Define State    CHARGING       enter=${enter}    on_error=FAULT
```

## Transitions

`Define Transition  source  target  condition=  machine=`

The condition is a Python expression evaluated with `Evaluate` semantics on
**every polling round**; use the `$name` syntax to read variables. Guarded
transitions are tried in definition order; an unconditional transition (no
`condition`) is the default branch and must be defined last.

!!! warning "Write `$SOC`, not `${SOC}`"
    `${SOC}` is replaced once, at definition time — the guard would compare a
    frozen number forever. `$SOC` is read fresh on every round.

Keywords feed the guards by updating variables:

```robotframework
*** Keywords ***
Charge Step
    ${soc}=    Evaluate    min($SOC + 9, 100)
    Set Test Variable    ${SOC}    ${soc}
```

## The engine loop

```plantuml
!include diagrams/sm_engine.puml
```

`Run State Machine  initial=  max_duration=  checkpoint=  resume=  poll_interval=  max_visits=  machine=`

| Argument | Meaning |
|----------|---------|
| `initial` | Name of the start state. |
| `max_duration` | Overall limit (e.g. `48h`). |
| `checkpoint` | Path of the persistent checkpoint file. |
| `resume` | `AUTO` (default) resumes when a checkpoint exists; `True` requires one; `False` starts fresh. |
| `poll_interval` | Delay between guard-evaluation rounds (default 1 s). |
| `max_visits` | Fail when the total number of state visits reaches this — a guard against transition ping-pong from wrong conditions. |

State keywords run as normal keywords, so `log.html` shows every visit as a
nested block — `STATE CHARGING (visit #37)` — and the same line goes to the
console as a heartbeat.

## Robustness for multi-day runs

| Feature | Behaviour |
|---------|-----------|
| **Up-front validation** | Unknown initial, transition or `on_error` states, non-final states without outgoing transitions, unknown file options — all rejected before the run starts. No typo discovered at hour 30. |
| **Preemptive state timeout** | enter/during/exit run under a keyword timeout limited to the remaining visit budget: a stuck keyword (even `Sleep 60s`) is interrupted and routed to `on_error`. Code blocking inside C cannot be interrupted. |
| **Error routing** | A keyword failure or state timeout goes to the `on_error` state (typically FAULT, collecting diagnostics). Without `on_error`: checkpoint, then the test fails. The failing state's `exit` keyword is skipped — the state did not complete. |
| **`on_error` cycle detection** | Error routing that revisits a state without a successful transition in between fails with the cycle spelled out (`A -> B -> A`) instead of looping. |
| **`max_visits`** | Bounds total visits, including restored ones; the checkpoint is saved before failing. |
| **Deadline** | `max_duration` exceeded → checkpoint and fail with a "can be resumed" message; resumed runs continue the elapsed count. |
| **Graceful stop** | `Stop State Machine` — callable from any keyword, including a Watchdog in a supervisor thread — finishes the round, checkpoints, returns PASS. |
| **Statistics** | Visits and time-in-state per state are logged at the end (also on failure), queryable with `Get State Statistics`, and persisted in the checkpoint so resumed runs keep accurate totals — including the visit in flight at crash time. |

## Crash-safe checkpoints

After every transition the engine atomically writes the checkpoint: current
state, visit counters, elapsed time (including in-flight visit time) and every
variable registered with `Checkpoint Variable`. A run interrupted at "hour 40"
resumes exactly where it stopped — progress loss is at most one transition:

```robotframework
*** Test Cases ***
Run Is Interrupted
    Checkpoint Variable    \${CYCLES}
    Run Keyword And Expect Error    *max_duration*exceeded*
    ...    Run State Machine    initial=WORK    max_duration=0.6s    checkpoint=${CP}

Run Resumes And Completes
    Run State Machine    initial=WORK    max_duration=1min    checkpoint=${CP}
    # resume=AUTO picks up the checkpoint: state, counters and registered
    # variables are restored, and the machine finishes.
```

On resume the log says, for example,
`Resuming state machine from checkpoint '…': state 'WORK', 612 milliseconds elapsed, saved 2026-08-23 14:13:49.`

- The checkpoint is written **before** any failure propagates (fatal keyword
  error, `max_duration`), so a failing run is resumable too.
- A **stopped** machine keeps its checkpoint; only a **completed** one deletes it.
- Each checkpoint carries a fingerprint of the machine definition: resuming
  with a **changed** machine warns loudly instead of mixing stale counters with
  new logic.
- `Checkpoint Variable` fails fast on values that are not JSON-serialisable;
  offending values at save time are skipped with a warning, and a failing
  checkpoint write only degrades resumability — it never kills the machine.

!!! warning "Two rules for resumable machines"
    1. Restored variables come back as **test** variables — keywords updating a
       checkpointed variable must use `Set Test Variable`, or the restored value
       shadows the update.
    2. A state's `enter` keyword runs **again** on resume — write enter keywords
       so running them twice is harmless.

## Definitions from a file

`Load State Machine  path  machine=` reads states, transitions and checkpoint
variables from a JSON file — or YAML for any other extension, when `pyyaml` is
installed (a clear install hint otherwise). File definitions go through the same
validation as the keywords, can be combined with them, and keep file order for
transitions. Both the compact `SOURCE -> TARGET when CONDITION` form and the
mapping form work.

```json
{
  "states": {
    "INIT":     {"enter": "Setup DUT", "timeout": "30 s"},
    "CHARGING": {"enter": ["Start Charging", "fast"], "during": "Charge Step", "on_error": "FAULT"},
    "DONE":     {"final": true},
    "FAULT":    {"enter": "Collect Diagnostics", "final": true}
  },
  "transitions": [
    "INIT -> CHARGING when $DUT_READY",
    {"source": "CHARGING", "target": "DONE", "condition": "$CYCLES >= 3"}
  ],
  "checkpoint_variables": ["${SOC}", "${CYCLES}"]
}
```

```yaml
# the same machine as YAML
states:
  INIT:     {enter: Setup DUT, timeout: 30 s}
  CHARGING: {enter: [Start Charging, fast], during: Charge Step, on_error: FAULT}
  DONE:     {final: true}
  FAULT:    {enter: Collect Diagnostics, final: true}
transitions:
  - INIT -> CHARGING when $DUT_READY
  - {source: CHARGING, target: DONE, condition: $CYCLES >= 3}
checkpoint_variables: ['${SOC}', '${CYCLES}']
```

## Named and concurrent machines

Every keyword takes `machine=<name>` (default `DEFAULT`). Several fully
isolated machines — own states, transitions, checkpoints, statistics — can live
in one test, sequentially or concurrently with one `THREAD` block per machine:

```robotframework
Define State    EMIT    during=Emit Tick    machine=STIMULATOR
...
THREAD    STIM_SM    True
    Run State Machine    initial=EMIT    machine=STIMULATOR    poll_interval=1 s
END
Run State Machine     initial=WAIT    machine=MAIN    max_duration=48h
Stop State Machine    machine=STIMULATOR
Wait For Thread       STIM_SM
```

- Log messages of named machines are prefixed `[NAME]`.
- Concurrent machines must use **separate checkpoint files** — sharing one is
  rejected at `Run State Machine`.
- Guards of a machine running **inside** a THREAD do not see variables set in
  that same thread ([thread variable scoping](thread/lifecycle.md#variable-scoping));
  machines in the main flow do see worker-fed variables. Control worker-hosted
  machines with `Stop State Machine` from the main flow.

## Suite scope — machines that span tests

The library has **SUITE** scope: definitions live for the whole suite (nested
suites get their own instances). Define once in `Suite Setup` (for example with
`Load State Machine`) and run from any test. A machine running in a SUITE-scoped
`THREAD` (`daemon=False`) stays reachable from later tests: `Stop State Machine`,
`Get Current State` and `Get State Statistics` address the instance the worker
is running.

When the hosting thread's scope ends, the machine detects the cooperative thread
stop — in its poll loop, in its sleep, and even when the stop surfaces as a
keyword failure — and stops gracefully: checkpoint saved, state preserved, no
`on_error` routing.

The flip side: a test that redefines the same machine needs
`Test Teardown    Reset State Machine` (or unique machine names) — duplicate
state definitions fail by design.

## Statistics

```robotframework
${stats}=    Get State Statistics    machine=BATTERY
Should Be True    ${stats['CHARGING']['visits']} >= 3
```

## Composition for a 48-hour run

The library owns only the test flow. The other risks of a two-day run are
covered by independent features — see [Long-running tests](long-running.md#how-the-features-compose-for-a-48-hour-run):
a [Watchdog](watchdog.md) whose `on_timeout=Stop State Machine` catches a silent
stall, [segmented output](segmented-output.md) against log loss on a crash, and
`--timeline` plus the per-visit blocks for reading the log.

## Keyword reference

| Keyword | Purpose |
|---------|---------|
| `Define State` | Define a state and its keywords/timeout/error routing. |
| `Define Transition` | Define a (optionally guarded) transition. |
| `Load State Machine` | Load states/transitions/checkpoint variables from JSON/YAML. |
| `Checkpoint Variable` | Register a (JSON-serialisable) variable to persist in the checkpoint. |
| `Run State Machine` | Run until a final state, stop request, or failure. |
| `Get Current State` | Return the current state name. |
| `Get State Statistics` | Return per-state visit counts and durations. |
| `Stop State Machine` | Request a graceful stop (also from another thread). |
| `Reset State Machine` | Clear one machine (`machine=`) or all — in a teardown when redefining. |

## Pitfalls

| # | Trap | Rule |
|---|------|------|
| 1 | Frozen guards | `$VAR` in conditions, never `${VAR}` |
| 2 | Resume shadowing | update checkpointed variables with `Set Test Variable` |
| 3 | Double enter | enter keywords run again on resume — make them idempotent |
| 4 | "State already defined" | suite scope keeps definitions — `Reset State Machine` in the teardown, or unique machine names |
| 5 | Blind thread guards | worker-hosted machines: control them with `Stop State Machine` from the main flow |
| 6 | Checkpoint clash | concurrent machines need separate checkpoint files (enforced) |
| 7 | Default branch first | an unconditional transition defined first makes later guards unreachable |

A graded set of runnable examples — from a five-line machine up to the
battery-endurance scenario with watchdog supervision and crash recovery — lives
in `demo/statemachine/`. The full keyword reference:
`python -m robot.libdoc StateMachine StateMachine.html`.
