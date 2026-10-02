# Flow files

**Your test plan is already a flowchart. Now the flowchart runs.**
{ .lead }

!!! pain "The pain"
    A bench run is a plan: wait for the chamber, power the rig, loop the cycle
    tests for eight hours, recover when a cycle fails, release the bench. The
    team draws that plan as a flowchart and reviews it — then someone rewrites
    it by hand as `.robot` keywords, where the plan disappears into `WHILE`,
    `TRY` and `Wait Until Keyword Succeeds`. The drawing and the test drift
    apart, a bench that never became ready shows up as a product FAIL, and two
    rigs that must run in step need glue code nobody wants to own.

!!! fix "The fix"
    Write the flowchart down once as a *flow file* — nodes and edges — and run
    it: `robot --parser robot.flow plan.flow.json`. Robot Framework builds the
    suite from the graph at run time. No `.robot` file to maintain, nothing to
    drift.

## 1. Map your test case from a diagram to a test suite

```plantuml
!include diagrams/flow_pipeline.puml
```

The boxes and arrows you draw are the nodes and edges of the file. Phases
become the suite setup, the test cases and the suite teardown; a loop becomes a
bounded `WHILE`; a failure route becomes `TRY`/`EXCEPT`; a wait becomes a
*gate* with a real timeout. This is a typical endurance run as a flow —
and exactly what executes:

```plantuml
!include diagrams/flow_endurance_cycle.puml
```

- **Bounded by construction** — every loop has `max_loops` and/or `max_seconds`;
  an unbounded loop is rejected before the run.
- **Gates, not guessed sleeps** — "wait until the chamber is at set point, at
  most 30 minutes" instead of `Sleep 30 min`.
- **Recovery that keeps going** — a failed cycle runs the recovery and the loop
  carries on (`continue`), or stops with the original error (`abort`).
- **Teardown always runs** — the bench is released even when setup failed.

## 2. Run two flows as two processes — synchronised as one test

Two rigs, two `robot` processes, one flow file for both (`--variable RIG:`).
They meet before they start, then move in **lockstep**: each publishes the cycle
it finished and waits until the other has caught up. They share nothing but a
small signal file of the run — the runner itself locks nothing and holds no
bench state.

```plantuml
!include diagrams/flow_two_processes.puml
```

```bash
export ROBOT_FLOW_SIGNALS=results/run42/signals.json     # one fresh file per run
robot --parser robot.flow --variable RIG:RIG_A  --variable PEER:RIG_B --outputdir results/rig_a  pair.flow.json &
robot --parser robot.flow --variable RIG:RIG_B --variable PEER:RIG_A  --outputdir results/rig_b pair.flow.json
```

If one rig dies, the other does not hang and does not report a false failure:
its gate times out and the verdict is **UNKNOWN**, with the last value it read.
See [Two flows](#two-flows) for the keywords and patterns.

## 3. Know it is right before you touch the bench

- `python -m robot.flow validate plan.flow.json` checks the structure and names
  the offending node: branches that never re-join, a loop body that never
  returns, an unreachable box.
- `robot --parser robot.flow --dryrun plan.flow.json` checks every keyword and
  its arguments — including the keyword behind each gate — without running
  any of them. It does import the libraries, so module-level code and library
  constructors run: keep bench access out of those.
- `python -m robot.flow render plan.flow.json` prints the equivalent `.robot`
  text: what the reviewer reads is what runs.
- Verdicts are honest: an assertion that fails is **FAIL**; a bench or a peer
  that never became ready is **UNKNOWN**; the return code carries both counts.

## Quick start

```bash
robot --parser robot.flow --variable RIG:RIG_A --outputdir out/rig_a flows/endurance_cycle.flow.json
robot --parser robot.flow --dryrun flows/endurance_cycle.flow.json      # check keywords and arguments without running them
python -m robot.flow validate flows/endurance_cycle.flow.json           # shape and structure only
python -m robot.flow render   flows/endurance_cycle.flow.json           # the equivalent .robot text
```

Every `robot` option applies — `--variable`, `--outputdir`, `--dryrun`,
`--segmentoutput`, listeners — because the flow becomes an ordinary
`TestSuite` in memory.

## Editor support

Flow files have a JSON Schema: completion of kinds, attributes and edge
labels, hover help, and errors underlined while you type — including typos
such as `max_loop`, which the runner itself would silently ignore. It ships
as `robot/flow/flow.schema.json` and can be regenerated with:

```bash
python -m robot.flow schema --output flow.schema.json
```

In VS Code, map it to every flow file of a project (`.vscode/settings.json`):

```json
{
  "json.schemas": [
    { "fileMatch": ["*.flow.json"], "url": "./path/to/robot/flow/flow.schema.json" }
  ]
}
```

or point a single file at it with a top-level `"$schema": "<path or URL>"`.
The schema checks the *shape* of a file; whether the graph is structured
(branches re-join, loop bodies return, every node is reachable) is still
checked by `python -m robot.flow validate` and `--dryrun`.

## Writing a flow in Python (experimental)

`robot.flow.api` builds exactly the dictionary a flow file holds, from Python
blocks that have the shape of the plan:

```python
from robot.flow.api import Flow

with Flow('Lab Cycle', libraries=['checks.py'], variables={'MODE': 'EMC'}) as f:
    with f.test('Cycles'):
        with f.loop('cycles', max_loops=3, every='1s') as loop:
            f.keyword('measure', 'Measure Current', assign='${I}')
            with f.decision('emc', "$MODE == 'EMC'") as d:
                with d.yes():
                    f.keyword('burst', 'Run Emc Burst')
                with d.no():
                    f.keyword('climate', 'Run Climate Step')
            with loop.on_failure('continue'):
                f.keyword('recover', 'Recover Dut')
f.save('lab_cycle.flow.json')
```

The edges and their labels (`body`, `next`, `done`, `on_failure`, `continue`,
`abort`, `yes`, `no`) follow from the blocks. The result is validated like a
flow file and runs, draws and reports the same.

What it gives: completion and checks in any Python IDE, functions and loops
to produce repetitive or lab-specific parts, and a debugger for the code that
builds the flow. What it does not change: the steps still run as Robot
keywords, so stepping through a *run* works as for any flow; and a flow
written in Python cannot be edited back in a visual editor — keep the
generated `.flow.json` as the file the tools share.

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
  "flow":      { "name": "Endurance Cycle", "version": 1 },
  "imports":   { "libraries": ["bench_keywords.py", ["BenchSignals", "127.0.0.1:9000"]] },
  "variables": { "RIG": "RIG_A" },
  "nodes": [
    { "id": "start",    "kind": "start" },
    { "id": "setup",    "kind": "phase", "role": "setup" },
    { "id": "chamber",  "kind": "gate",    "keyword": "Signal Should Be", "args": ["bench.chamber.state", "==", 1],
                                            "timeout": "300s", "interval": "5s" },
    { "id": "power",    "kind": "keyword", "keyword": "Power On DUT", "args": ["${RIG}"] },
    { "id": "cycle",    "kind": "phase", "role": "test", "name": "Cycle" },
    { "id": "loop",     "kind": "loop",    "max_loops": 1000, "max_seconds": "8h", "every": "10s" },
    { "id": "run",      "kind": "keyword", "keyword": "Run Cycle Tests", "args": ["${RIG}"] },
    { "id": "recover",  "kind": "keyword", "keyword": "Recover DUT", "args": ["${RIG}"] },
    { "id": "teardown", "kind": "phase", "role": "teardown" },
    { "id": "release",  "kind": "keyword", "keyword": "Release Bench" },
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
| `keyword` | `keyword`, `args`, `assign` | 1 | Call a keyword; `${var}` substitution applies. `assign` (`"${VERSION}"`) stores the return value for a later decision. |
| `gate` | `keyword`, `args`, `timeout`, `interval` (2s), `on_timeout`: unknown (default) or fail | 1 | Poll the keyword until it passes. On timeout the message carries the last error and the elapsed time. |
| `sleep` | `duration` | 1 | `Sleep`. |
| `decision` | `condition` (`$var` syntax) | `yes`, `no` | Branch; both branches must re-join at one node. |
| `loop` | `max_loops` and/or `max_seconds`, `every` | `body`, `done`, optional `on_failure` | Bounded repetition. The body ends with an edge labelled `next` back to the loop. |
| `try` | — | `body`, `on_failure`, `done` | Failure routing without a loop. |
| `flow` | `file`, `args` (an object: parameter → value) | 1 | Call another flow file — a sub-flow — as one step. |

A recovery region ends with `continue` back to its loop or try node, or with
`abort` to the node after it (or an end node); `abort` re-raises the failure
after the recovery ran. Loops and try regions nest: a loop that is itself the
last node of an enclosing body leaves with `next` instead of `done`.

### Sub-flows

A bigger plan stays readable when a region becomes a file of its own and the
plan calls it as one box:

```json
{ "id": "power", "kind": "flow", "file": "sub/safe_power_on.flow.json", "args": { "VOLTS": "${V}" } }
```

- A sub-flow is an ordinary flow file **without phases**; its `variables` are its
  **parameters**, with their values as defaults. `args` passes values by name;
  an unknown name is an error naming the calling node.
- The file resolves relative to the calling file; its imports join the suite,
  their relative paths kept valid (a file imported twice counts once).
- Each sub-flow file becomes **one keyword**, `Flow: <its name>`, called once per
  box: in the log it is one step that opens to show the sub-flow's steps.
- Sub-flows may call sub-flows; a cycle is rejected, naming the chain of files.
- A failure inside a sub-flow fails that step — the caller's `on_failure`
  recovery catches it like any other step.
- A sub-flow returns nothing (`assign` is rejected); share results through
  signals or suite variables.

`python -m robot.flow validate` checks every sub-flow file it reaches.

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
            Run Cycle Tests    ${RIG}
        EXCEPT    AS    ${flow_error}
            Recover DUT    ${RIG}
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
can read and write. One flow *sets* a signal with a `keyword` node, the other
*waits* for it with a `gate`. Inside one process that can be a
[THREAD notification](thread/synchronisation.md); across processes -- the
members of a run group -- the runner ships a small library for it,
`robot.flow.signals`:

```json
"imports": { "libraries": ["robot.flow.signals"] },
"nodes": [
  { "id": "announce", "kind": "keyword", "keyword": "Set Signal", "args": ["cycle", "${n}"] },
  { "id": "checked",  "kind": "gate",    "keyword": "Signal Should Be",
    "args": ["ack", "==", "${n}"], "timeout": "30s", "on_timeout": "fail" }
]
```

| Keyword | |
|---------|-|
| `Set Signal    name    value` | publish a value for the other flows of the run; one that looks like a number is stored as one |
| `Get Signal    name    [default]` | its value; without a default, fails while it has not been set |
| `Signal Should Be    name    op    expected    [tolerance]` | made for gates: `==` `!=` `<` `<=` `>` `>=`, numbers as numbers; fails saying what it read |

- Numbers compare as numbers, `==` and `!=` within `tolerance`; other values
  compare as text and only with `==` and `!=`.
- A failing comparison says what was read and how old it is —
  `Signal 'RIG_B.cycle' is 2 (set 1s 200ms ago), expected >= 3.` — and an unset signal
  says so (`Signal 'RIG_B.ready' has not been set.`), which is exactly what a
  timed-out gate reports as its last error.

### Patterns

**Rendezvous** — each flow announces itself and waits for the other; one file
serves both sides through `--variable RIG:` / `PEER:`:

```json
{ "id": "announce", "kind": "keyword", "keyword": "Set Signal",       "args": ["${RIG}.ready", 1] },
{ "id": "meet",     "kind": "gate",    "keyword": "Signal Should Be", "args": ["${PEER}.ready", "==", 1], "timeout": "5min" }
```

**Lockstep** — the rendezvous once per cycle: publish the cycle just finished,
wait until the peer has reached it. A monotonic counter needs no reset:

```json
{ "id": "run",  "kind": "keyword", "keyword": "Run Cycle Tests",  "args": ["${RIG}"], "assign": "${n}" },
{ "id": "tell", "kind": "keyword", "keyword": "Set Signal",       "args": ["${RIG}.cycle", "${n}"] },
{ "id": "sync", "kind": "gate",    "keyword": "Signal Should Be", "args": ["${PEER}.cycle", ">=", "${n}"], "timeout": "2min" }
```

If the peer dies, the gate times out and the verdict is UNKNOWN, not FAIL.
Never retract a flag the peer may not have read yet — mark the end with a new
signal (`${RIG}.done`) instead.

Where they live:

- **`ROBOT_FLOW_SIGNALS`** -- a JSON file the processes of the run share
  (created on first write; writes are locked, readers never see half a file;
  a lock left behind by a killed process is taken over after 10 seconds).
  Whoever starts the group sets it to a file *of that run*, so two runs can
  never read each other's signals, for example `results/<run>/signals.json`.
  Unset, it is `robot_flow_signals.json` in the
  temporary directory, shared by every run on the machine that sets none.
- **`ROBOT_FLOW_SIGNALS_BACKEND`** -- a class (`package.module.Class` or `package.module:Class`) for
  flows on different machines: created with the `ROBOT_FLOW_SIGNALS` value,
  it needs `get(name)` (returning `{"value": ..., "time": ...}` or `None`)
  and `set(name, value)`.
- In a flow or suite, `Library    robot.flow.signals.FlowSignals    store=...    backend=...`
  sets both there.

The library is for coordination -- cycle numbers, acknowledgements, "ready".
The bench's own values (a supply voltage, a DUT temperature) stay in the
services that own them, and a flow waits on those with their own keywords.
The runner itself still locks nothing: a flow stays restartable.
