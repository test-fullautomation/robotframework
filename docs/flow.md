# Flow files

A *flow file* describes a test plan as a small graph — setup, gates, a bounded
cycle loop, recovery, teardown — and Robot Framework executes it directly as a
suite built at run time. The drawing of the plan is the executable.

```bash
robot --parser robot.flow --variable BLADE:IVI --outputdir out/ivi flows/permanent_run.flow.json
robot --parser robot.flow --dryrun flows/permanent_run.flow.json      # validate without touching the bench
python -m robot.flow validate flows/permanent_run.flow.json           # shape and structure only
python -m robot.flow render   flows/permanent_run.flow.json           # the equivalent .robot text
```

Every `robot` option applies — `--variable`, `--outputdir`, `--dryrun`,
`--segmentoutput`, listeners — because the flow becomes an ordinary
`TestSuite` in memory.

## Workflow, not state machine

A flow is a **workflow**: nodes are things the test *does*, edges mean
*"then"*, the path is prescribed, every loop is bounded, and every run ends
with a verdict. The [StateMachine library](statemachine.md) is the other
model — nodes are what the system *is*, edges mean *"when"* — and the two are
kept apart on purpose. A state machine can be one keyword step inside a flow;
a flow is never run through the state-machine engine.

## The file

```json
{
  "flow":      { "name": "Permanent Run", "version": 1 },
  "imports":   { "libraries": ["bench_keywords.py", ["SignalKeywords", "127.0.0.1:50210"]] },
  "variables": { "BLADE": "IVI" },
  "nodes": [
    { "id": "start",    "kind": "start" },
    { "id": "setup",    "kind": "phase", "role": "setup" },
    { "id": "chamber",  "kind": "gate",    "keyword": "Signal Should Be", "args": ["bench.chamber.state", "==", 1],
                                            "timeout": "300s", "interval": "5s" },
    { "id": "power",    "kind": "keyword", "keyword": "Set Power And Current", "args": ["${BLADE}"] },
    { "id": "cycle",    "kind": "phase", "role": "test", "name": "Cycle" },
    { "id": "loop",     "kind": "loop",    "max_loops": 1000, "max_seconds": "8h", "every": "10s" },
    { "id": "run",      "kind": "keyword", "keyword": "Run Cycle Tests", "args": ["${BLADE}"] },
    { "id": "recover",  "kind": "keyword", "keyword": "Execute Recovery Strategy", "args": ["${BLADE}"] },
    { "id": "teardown", "kind": "phase", "role": "teardown" },
    { "id": "release",  "kind": "keyword", "keyword": "Before Suite Tear Down" },
    { "id": "end",      "kind": "end" }
  ],
  "edges": [
    ["start", "setup"], ["setup", "chamber"], ["chamber", "power"], ["power", "cycle"], ["cycle", "loop"],
    { "from": "loop",    "to": "run",      "label": "body" },
    { "from": "run",     "to": "loop",     "label": "next" },
    { "from": "loop",    "to": "recover",  "label": "on_failure" },
    { "from": "recover", "to": "loop",     "label": "continue" },
    { "from": "loop",    "to": "teardown", "label": "done" },
    ["teardown", "release"], ["release", "end"]
  ]
}
```

A two-element list is an unlabelled *"then"* edge; labels are needed only
where a node has more than one way out. Libraries given as a path (`*.py`)
resolve relative to the flow file, like in `.robot` files; plain names come
from the module search path.

### Node kinds

| kind | attributes | out-edges | meaning |
|------|------------|-----------|---------|
| `start` / `end` | — | 1 / 0 | Exactly one start; at least one end. |
| `phase` | `role`: setup, test, teardown; `name` for tests | 1 | Setup → suite setup, each test phase → one test case, teardown → suite teardown. Without phases the whole flow is one test named after the flow. |
| `keyword` | `keyword`, `args` | 1 | Call a keyword; `${var}` substitution applies. |
| `gate` | `keyword`, `args`, `timeout`, `interval` (2s), `on_timeout`: unknown (default) or fail | 1 | Poll the keyword until it passes. On timeout the message carries the last error and the elapsed time. |
| `sleep` | `duration` | 1 | `Sleep`. |
| `decision` | `condition` (`$var` syntax) | `yes`, `no` | Branch; both branches must re-join at one node. |
| `loop` | `max_loops` and/or `max_seconds`, `every` | `body`, `done`, optional `on_failure` | Bounded repetition. The body ends with an edge labelled `next` back to the loop. |
| `try` | — | `body`, `on_failure`, `done` | Failure routing without a loop. |

A recovery region ends with `continue` back to its loop or try node, or with
`abort` to the node after it (or an end node); `abort` re-raises the failure
after the recovery ran.

### Rules

The validator rejects, naming the node, anything that is not a structured
workflow: a second way out of an action node, decision branches that do not
re-join, a loop body that does not return with `next`, an edge into a loop
body from outside, an unbounded loop, an unreachable node. These are exactly
the conditions under which the graph compiles losslessly to `IF`, `WHILE` and
`TRY`.

## What runs

The builder emits a `robot.running.TestSuite`; `python -m robot.flow render`
prints it as `.robot` text so what a reviewer reads is what executes:

```robotframework
*** Test Cases ***
Cycle
    ${flow_deadline_loop}=    Evaluate    time.time() + 28800.0
    WHILE    time.time() < ${flow_deadline_loop}    limit=1000    on_limit=pass
        TRY
            Run Cycle Tests    ${BLADE}
        EXCEPT    AS    ${flow_error}
            Execute Recovery Strategy    ${BLADE}
        END
        Sleep    10s
    END
```

| Flow | Robot |
|------|-------|
| setup / teardown phase | generated user keywords `Flow Setup` / `Flow Teardown` as suite setup and teardown — they may hold loops and branches |
| test phase | a test case |
| `gate` | `Flow Gate` from `robot.flow.keywords`, built on retrying the keyword |
| `decision` | `IF` / `ELSE` |
| `loop` | `WHILE` with `limit=max_loops on_limit=pass`; `max_seconds` is a deadline in the condition, so reaching either bound ends the loop with **PASS** |
| `on_failure` | `TRY` / `EXCEPT AS ${flow_error}`; `abort` adds `Fail ${flow_error}` after the recovery |

## Verdicts

| What happened | Status |
|---------------|--------|
| A keyword asserted and failed | FAIL |
| A gate timed out | **UNKNOWN** — the bench or a peer was not ready; nothing was tested. `on_timeout: fail` per gate when a silent peer *is* the product failing |
| A setup gate timed out | UNKNOWN for every test (`Setup unknown:`); teardown still runs |
| A keyword in the flow does not exist or has wrong arguments | UNKNOWN, and `--dryrun` reports it per node — including the keyword behind a gate |
| A loop reached `max_loops` or `max_seconds` | PASS — reaching the bound is the plan |

The [return code](unknown-status.md#return-code) separates the two counts, so
an orchestrator can tell "bench never became ready" from "product failed"
without opening the log.

## Two flows

A flow has no inbox; two flows communicate through a shared observable both
can read and write. Across processes that is a published signal: one flow
*sets* it with a `keyword` node, the other *waits* on it with a `gate`. Inside
one process it is a [THREAD notification](thread/synchronisation.md). Nothing
in the runner locks or shares state, so a flow stays restartable and the
bench state stays in the services that own it.
