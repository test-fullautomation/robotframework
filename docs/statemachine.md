# StateMachine library

The `StateMachine` library drives execution as a finite state machine. It is
built for **very long, condition-driven tests** — battery endurance, soak and
hardware-in-the-loop scenarios that step between states for hours or days and
must survive interruption.

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

## States

`Define State  name  enter=  during=  exit=  timeout=  on_error=  final=`

| Argument | Meaning |
|----------|---------|
| `enter` | Keyword run once when the state is entered. |
| `during` | Keyword run on every poll while the state is active. |
| `exit` | Keyword run once when leaving the state via a transition. |
| `timeout` | Maximum duration of one visit (e.g. `30s`, `10 min`); exceeding it routes to `on_error`. |
| `on_error` | State to go to if a keyword fails or the timeout is exceeded. |
| `final` | Entering this state completes the machine. |

Each of `enter`/`during`/`exit` is a keyword name, or a list of keyword name
followed by its arguments.

## Transitions

`Define Transition  source  target  condition=`

The condition is an expression evaluated with `Evaluate` semantics; use the
`$name` syntax to read variables. Guarded transitions are tried in definition
order; an unconditional transition (no `condition`) is the default branch and
must be defined last.

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
| `poll_interval` | Delay between guard-evaluation rounds. |
| `max_visits` | Fail when the total number of state visits reaches this — a guard against transition ping-pong from wrong conditions. |

## Safety nets for long runs

- **`on_error` routing** — a failing state keyword sends the machine to a
  recovery state instead of aborting the run.
- **`timeout`** — a visit that takes too long is routed to `on_error`,
  interrupting even a keyword that is still running (via Robot's keyword-timeout
  machinery).
- **`max_visits`** — catches a mis-written pair of always-true guards that would
  otherwise bounce between two states until `max_duration`.
- **cycle detection** on `on_error` routing.

## Crash-safe checkpoints

Registered variables and the current state are written to an atomic JSON
checkpoint (including in-flight visit times, with the machine definition
fingerprinted). A run interrupted at "hour 40" resumes exactly where it stopped:

```robotframework
*** Test Cases ***
Run Is Interrupted
    Checkpoint Variable    \${CYCLES}
    Run State Machine    initial=WORK    max_duration=0.6s    checkpoint=${CP}
    # ... aborts on max_duration, checkpoint written ...

Run Resumes And Completes
    Run State Machine    initial=WORK    max_duration=1min    checkpoint=${CP}
    # resume=AUTO picks up the checkpoint: state, counters and registered
    # variables are restored, and the machine finishes.
```

Restored variables come back as **test** variables. When the machine runs in a
suite-scoped thread, drive it from the main flow with `Stop State Machine`
rather than a same-thread variable guard (see the
[THREAD lifecycle note](thread/lifecycle.md)).

## Definitions from a file

`Load State Machine  path  machine=` reads states, transitions and checkpoint
variables from a JSON file (or YAML, when `pyyaml` is installed):

```json
{
  "states": {
    "INIT":     {"enter": "Setup DUT", "timeout": "30 s"},
    "CHARGING": {"enter": "Start Charging", "during": "Charge Step", "on_error": "FAULT"},
    "DONE":     {"final": true},
    "FAULT":    {"enter": "Collect Diagnostics", "final": true}
  },
  "transitions": [
    "INIT -> CHARGING when $DUT_READY",
    "CHARGING -> DONE when $CYCLES >= 3"
  ],
  "checkpoint_variables": ["${SOC}", "${CYCLES}"]
}
```

## Named and concurrent machines

Every keyword takes an optional `machine=` argument, so several independent
machines can be defined and run at once — for example a stimulator machine
inside a `THREAD` feeding data while a main machine waits for it. Inspect and
control them with `Get Current State`, `Get State Statistics`,
`Stop State Machine` and `Reset State Machine`.

## Keyword reference

| Keyword | Purpose |
|---------|---------|
| `Define State` | Define a state and its keywords/timeout/error routing. |
| `Define Transition` | Define a (optionally guarded) transition. |
| `Load State Machine` | Load states/transitions/checkpoint variables from JSON/YAML. |
| `Checkpoint Variable` | Register a variable to persist in the checkpoint. |
| `Run State Machine` | Run until a final state, stop request, or failure. |
| `Get Current State` | Return the current state name. |
| `Get State Statistics` | Return per-state visit counts and durations. |
| `Stop State Machine` | Request a graceful stop. |
| `Reset State Machine` | Clear machine state (suite teardown). |

A full, graded set of runnable examples (from a five-line machine up to the
battery-endurance scenario with watchdog supervision and crash recovery) lives
in `demo/statemachine/` in the repository.
