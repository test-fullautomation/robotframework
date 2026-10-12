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
robot --parser robot.flow --flowreport flow.html flows/endurance_cycle.flow.json   # run it and draw the plan with the results
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
| `keyword` | `keyword`, `args`, `assign`, optional `retry`, `timeout` | 1 | Call a keyword; `${var}` substitution applies. `assign` (`"${VERSION}"`) stores the return value for a later decision. `retry` runs it again when it fails, `timeout` limits each attempt (see [Retry and timeout per step](#retry-and-timeout-per-step)). |
| `gate` | `keyword`, `args`, `timeout`, `interval` (2s), `on_timeout`: unknown (default) or fail | 1 | Poll the keyword until it passes. On timeout the message carries the last error and the elapsed time. |
| `sleep` | `duration` | 1 | `Sleep`. |
| `decision` | `condition` (`$var` syntax) | `yes`, `no` | Branch; both branches must re-join at one node. |
| `loop` | `max_loops` and/or `max_seconds`, `every` | `body`, `done`, optional `on_failure` | Bounded repetition. The body ends with an edge labelled `next` back to the loop. A bound may be a `${variable}` (see below). |
| `try` | — | `body`, `on_failure`, `done` | Failure routing without a loop. |
| `flow` | `file`, `args` (an object: parameter → value) | 1 | Call another flow file — a sub-flow — as one step. |
| `parallel` | `timeout`, `stop_others` (true) | `branch` (two or more), `done`, optional `on_failure` | Run the branches at the same time and join them before going on. Each branch ends with an edge labelled `join` back to the node (see [Parallel regions](#parallel-regions)). |

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

### Retry and timeout per step

A gate waits for the bench; a `keyword` node that *acts* on it sometimes needs
a second chance too -- a reading that is noise once in a while, a command the
device drops under load. The policy is declared on the node, not coded into
the keyword:

```json
{ "id": "read", "kind": "keyword", "keyword": "Read Sensor", "args": ["ch1"], "assign": "${LEVEL}",
  "retry": { "max_attempts": 5, "interval": "2s", "backoff": 2, "on_exhausted": "unknown" },
  "timeout": "30s" }
```

| attribute | meaning |
|-----------|---------|
| `retry.max_attempts` | How many times the keyword may run (a number alone, `"retry": 3`, is this). |
| `retry.interval` | The wait before the next attempt; default `1s`. Paused time does not count. |
| `retry.backoff` | The factor the wait grows by after each attempt; default 1. With `2s` and `2`: 2 s, 4 s, 8 s ... |
| `retry.on_exhausted` | The verdict when the last attempt fails too: `fail` (default, the keyword's own failure) or `unknown` (the bench did not cooperate; nothing was tested). |
| `timeout` | A keyword timeout on each attempt: an attempt that exceeds it is interrupted and counts as failed -- retried under a `retry` policy, otherwise the step fails. |

In the log the node is one `Flow Retry` step whose children are the attempts,
with a message per failed attempt saying when the next one comes and why
this one failed;
the flow report shows the number of attempts on the box, like a gate's. The
step's error names the attempts and carries the last error
(`'Read Sensor' failed after 5 attempts. Last error: ...`). With one attempt
the keyword's own failure is kept as it is; a timed-out step fails with
`'Read Sensor' timed out after 30 seconds`, an ordinary failure the node's
`on_failure` recovery can route. A skip, a test timeout, a syntax error or an
exit-on-failure is never retried. `max_attempts`, `interval` and `timeout` may
be `${variables}`, sized per run like loop bounds.

### Parallel regions

"Ramp the chamber while the PSU sweeps, then measure": two things happen at
the same time, and the plan goes on when both are done. A `parallel` node
draws that as one box with a branch per thing:

```json
{ "id": "ramp", "kind": "parallel", "label": "Ramp and sweep", "timeout": "10 min" },
{ "id": "chamber", "kind": "keyword", "keyword": "Set Chamber", "args": [-40] },
{ "id": "cold",    "kind": "gate",    "keyword": "Chamber At",  "args": [-40], "timeout": "5 min" },
{ "id": "psu",     "kind": "keyword", "keyword": "Sweep PSU",   "args": [8, 16, 0.5] },
{ "id": "measure", "kind": "keyword", "keyword": "Measure" }
```

```json
{ "from": "ramp", "to": "chamber", "label": "branch" }, ["chamber", "cold"], { "from": "cold", "to": "ramp", "label": "join" },
{ "from": "ramp", "to": "psu",     "label": "branch" },                      { "from": "psu",  "to": "ramp", "label": "join" },
{ "from": "ramp", "to": "measure", "label": "done" }
```

- **Edges.** `branch` is the one label a node may carry several times: one
  per branch, to its first node. A branch is an ordinary sequence --
  keywords, gates, sleeps, decisions, loops, try regions, sub-flows -- and
  its last node returns to the parallel node with `join`. `done` leaves the
  region (or `next` / `continue` / `abort` when the region is the last node
  of an enclosing body), and `on_failure` is a recovery region as for a loop
  or try. A node belongs to one branch, a branch holds no phase, and a
  region cannot be inside a branch of another (nor inside a sub-flow called
  from one): the validator says so, naming the node.
- **`timeout`** bounds the whole region on the flow clock: every branch must
  have joined by then. **`stop_others`** (default `true`) stops the other
  branches at their next step when one fails; `false` lets them run to their
  end and reports everything.
- **Each branch runs on a copy of the variables** as they were when the
  region started, like a `THREAD` body: an `assign` inside a branch is the
  branch's own. Data the plan needs after the join goes through
  [signals](#two-flows) (`Set Signal` in the branch, `Get Signal` after) --
  the same mechanism two flows use, now inside one process.
- **A region is one step of its enclosing body**, not a restart unit: inside
  a checkpointed loop the iteration is the unit, so an interrupted region runs
  again as a whole with its iteration.
- Node policies apply inside branches (`retry`, `timeout`, gates), with the
  `THREAD` caveat that on Linux and macOS keyword timeouts do not work in
  worker threads.

What runs: `Flow Parallel Start` names the branches' threads
(`flow.<region>.<branch>`, numbered from the region's second run on, so a
region in a loop keeps every iteration's logs), a `THREAD` block per branch
runs `Flow Branch` on a generated user keyword holding the branch's steps,
and `Flow Join` waits for them in slices on the flow clock. In `log.html`
each branch is one thread block, and the timeline shows them side by side.

| What happened in the region | The join |
|---|---|
| every branch passed | PASS, `Region 'ramp': 2 branches joined after 3 minutes 10 seconds.` in the log |
| a branch failed | FAIL with the branch's error: `Branch 'psu' of 'ramp' failed: PSU current 3.2 A, expected < 3 A.`; the others are stopped at their next step (`stop_others`), the log says which |
| a gate timed out inside a branch | that branch is UNKNOWN, and so is the join: `Branch 'chamber' of 'ramp' is unknown: Gate ...` -- the worst status wins, UNKNOWN over FAIL, one line per branch |
| the region's `timeout` passed | FAIL naming the branches still running, which are stopped; a branch that does not reach a step boundary within 10 seconds is abandoned and the message says so |
| the flow is paused | the join and the branches wait at their next step; the region's timeout does not advance |
| the flow is stopped | the branches are stopped first, then the flow ends as usual |
| `on_failure` | the recovery sees the join's failure, like a `try` node's; `continue` goes on with `done`, `abort` re-raises after the recovery |

### Bounds from variables

`max_loops`, `max_seconds`, `every`, a gate's `timeout` and `interval`, a
keyword's `timeout` and `retry` values, a parallel region's `timeout`, and a
sleep's `duration` may be a single `${variable}` instead of a value. The
default comes from the file's `variables`; a run overrides it on the command
line, so the same plan runs as a one-hour smoke test and as the 48-hour
endurance run:

```json
"variables": { "MAX_TIME": "48h", "CYCLES": 2000 },
"nodes": [ { "id": "loop", "kind": "loop", "max_loops": "${CYCLES}", "max_seconds": "${MAX_TIME}" } ]
```

```bash
robot --parser robot.flow --variable MAX_TIME:1h --variable CYCLES:50 plan.flow.json
```

The value is checked when the loop starts (a run with `CYCLES:many` ends
there with an error); `--dryrun` cannot check it. The loop is still bounded
by construction: the file names the bound, the run only sizes it. A
checkpoint saves what is left of the sized bounds, so a restart continues
with them. In the VS Code extension, *Run with Variables…* asks for the
values.

### Rules

The validator rejects, naming the node, anything that is not a structured
workflow: a second way out of an action node, decision branches that do not
re-join, a loop body that does not return with `next`, a branch of a parallel
region that does not return with `join`, an edge into a loop body from
outside, an unbounded loop, a region inside a branch of another, an
unreachable node. These are exactly the conditions under which the graph
compiles losslessly to `IF`, `WHILE`, `TRY` and `THREAD`.

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
| `keyword` with `retry` or `timeout` | `Flow Retry` from `robot.flow.keywords`: the attempts are its children, each under Robot's keyword timeout when the node has one; a node without a policy is the plain keyword call |
| `decision` | `IF` / `ELSE` |
| `loop` | `WHILE` with `limit=max_loops on_limit=pass`; `max_seconds` is a deadline on the flow clock in the condition, so reaching either bound ends the loop with **PASS** |
| checkpoint | `Flow Phase` first in every test; for a loop directly in a test phase `Flow Loop` before the `WHILE` (it returns the bounds). The iterations are counted by the runner itself as each one starts, without a keyword in the log. See [Pause, resume, stop and restart](#pause-resume-stop-and-restart). |
| `on_failure` | `TRY` / `EXCEPT AS ${flow_error}`; `abort` adds `Fail ${flow_error}` after the recovery |
| `parallel` | `Flow Parallel Start` (returns the threads' names), one `THREAD` block per branch running `Flow Branch` on the generated user keyword `Flow Branch: <region>/<branch>` that holds the branch's steps, then `Flow Join`; with `on_failure` the region is inside the `TRY` |

A flow's loops keep memory flat: once an iteration is in `output.xml` its
results leave the model (only the last one stays), so a million cycles cost
the same memory as ten. Suites get the same with `--pruneresults`; see
[Bounded memory](long-running.md#bounded-memory).

## Verdicts

| What happened | Status |
|---------------|--------|
| A keyword asserted and failed | FAIL |
| A gate timed out | **UNKNOWN** — the bench or a peer was not ready; nothing was tested. `on_timeout: fail` per gate when a silent peer *is* the product failing |
| A keyword failed on every attempt of its `retry` | FAIL with the last error and the number of attempts; `on_exhausted: unknown` per node when giving up means the bench did not cooperate |
| A keyword attempt exceeded the node's `timeout` | The attempt is interrupted and counts as failed: retried under a `retry` policy, else FAIL (`timed out after ...`) |
| A branch of a parallel region failed | FAIL of the join, naming the branch and its error; the other branches are stopped at their next step (`stop_others`) |
| A gate timed out inside a branch | UNKNOWN for that branch and for the join: the worst status of the branches wins (UNKNOWN over FAIL) |
| A parallel region did not join within its `timeout` | FAIL naming the branches still running |
| A setup gate timed out | UNKNOWN for every test (`Setup unknown:`); teardown still runs |
| A keyword in the flow does not exist or has wrong arguments | UNKNOWN, and `--dryrun` reports it per node — including the keyword behind a gate |
| A loop reached `max_loops` or `max_seconds` | PASS — reaching the bound is the plan |

The [return code](unknown-status.md#return-code) separates the two counts, so
an orchestrator can tell "bench never became ready" from "product failed"
without opening the log.

## The flow report

`log.html` shows a loop as its iterations, one after the other; a plan of
four nodes that cycled 600 times is 600 blocks to scroll. The *flow report*
shows the plan **as it was drawn** -- lanes for the phases, boxes for the
nodes, the edges between them -- with what the run did written on it: how
often each node ran and with which outcome, how often each edge was taken,
how many attempts a gate or a retried keyword needed, where a failed cycle went through the
recovery, where a stopped run stopped and what a restart would do. Every box
links into `log.html` at the keyword, so the log stays the record and the
report is the way in.

```bash
robot --parser robot.flow --flowreport flow.html --outputdir out plan.flow.json
rebot --flowreport flow.html --log log.html out/output.xml       # from an existing output
python -m robot.flow report out/output.xml --output flow.html     # the same, standalone
```

The report is built from `output.xml` and the flow file the suite came from
(its `source`; `--flow <file>` names it when the output was moved). The
builder makes the suite again from the file and walks it next to the result,
so each keyword in the log is attributed to its node of the plan. That
attribution is positional: **the flow file must be the one the run used**.
A changed file gives a report that cannot be trusted; a report of a run that
spans a checkpoint restart is best read per output, or from the output that
`rebot` merged.

A node's record (select it) lists the runs with status and time, the slowest
one, the first and the last failure with their errors, and the iterations of
a loop as a strip of cycles that each open the log at that cycle. Sub-flows
are drawn as a box containing their own plan; they start folded, except when
something inside them failed, and open with their `+` or with *Expand all*.

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

Where they live: the *store* of the run, named by **`ROBOT_FLOW_SIGNALS`**,
which whoever starts the group sets for every member. A store keeps the
last value of each name with the time it was set and a sequence number, so
a flow that joins late reads what was set before it came. Whatever the
store, it is also the run's control channel (pause, resume, stop, set,
snapshot, status), and the same `python -m robot.flow control <store> ...`
works on it.

| `ROBOT_FLOW_SIGNALS` | the store |
|---|---|
| a path, e.g. `results/<run>/signals.json` | **a JSON file** the processes of the run share on one machine (the default; created on first write; writes are locked, readers never see half a file; a lock left behind by a killed process is taken over after 10 seconds). Unset: `robot_flow_signals.json` in the temporary directory, shared by every run on the machine that sets none. |
| `http://<host>:8765/<run>` | **the signal server**: `python -m robot.flow server` on any machine of the bench; see below. |
| `redis://<host>:6379/0?ns=<run>` | a **Redis** server already on the bench (`pip install redis`). The run's signals are one hash, the sequence numbers one counter. |
| `mqtt://[user:pw@]<host>:1883/<run>` | an **MQTT broker** (`pip install paho-mqtt`): each signal is the retained message of `<run>/<name>`. The broker assigns no sequence numbers, so the publishers' clocks order the writes: keep the machines on NTP. |

A fresh store per run (a new file, a new `<run>` name) means two runs can
never read each other's signals.

- **`ROBOT_FLOW_SIGNALS_BACKEND`** -- any other system (a bus the bench
  already has, a database): a class (`package.module:Class`) created with
  the `ROBOT_FLOW_SIGNALS` value. Subclass `robot.flow.signals.SignalStore`,
  implement `get`, `set` and `read`, and prove it with the store contract
  test; [Signal stores](signal-stores.md#a-store-of-your-own) shows a worked
  class.
- In a flow or suite, `Library    robot.flow.signals.FlowSignals    store=...    backend=...`
  sets both there.

### Flows on several machines

A distributed system is tested by flows on several machines meeting through
one store. Nothing has to be installed for it: start the signal server on
any machine of the bench and point every member at it, with its rig name.

```bash
# on bench PC "hub"
python -m robot.flow server --port 8765            # --file signals.json keeps the state across restarts

# on every machine of the run, with the run's own name in the URL
export ROBOT_FLOW_SIGNALS=http://hub:8765/run42
robot --parser robot.flow --variable FLOW_RIG:RIG_A plan_a.flow.json     # on PC A
robot --parser robot.flow --variable FLOW_RIG:RIG_B plan_b.flow.json     # on PC B

# from anywhere
python -m robot.flow control http://hub:8765/run42 status
python -m robot.flow control http://hub:8765/run42 pause --rig RIG_B
```

The server orders every write with one sequence number, so a command given
before a member started is not for it however the clocks of the two
machines differ. [Signal stores](signal-stores.md) has the server's HTTP
interface, Redis, MQTT and RabbitMQ (through its MQTT plugin), and how to
write and prove a store of your own.

The library is for coordination -- cycle numbers, acknowledgements, "ready".
The bench's own values (a supply voltage, a DUT temperature) stay in the
services that own them, and a flow waits on those with their own keywords.
The runner itself still locks nothing: a flow stays restartable.

## Pause, resume, stop and restart

A long plan gets interrupted: a cable has to be swapped, the bench PC reboots,
the run has to give the rig back on Friday and take it again on Monday. Two
features cover this, and `stop` joins them.

| | Pause / resume | Checkpoint / restart |
|-|----------------|----------------------|
| When | the flow is running and somebody says *hold* | the run ended without finishing: stopped, crashed, power lost |
| Process | the same one keeps running | a new `robot` process, minutes or days later |
| Unit | between two steps, between two polls of a gate | the loop iteration |
| Bench | untouched; the flow waits | set up again by the setup phase, which runs again |
| Verdict | not affected | finished phases show as SKIP, the rest as usual |

### Pausing a running flow

```bash
python -m robot.flow control results/run42/signals.json pause
python -m robot.flow control results/run42/signals.json resume
python -m robot.flow control results/run42/signals.json status
python -m robot.flow control results/run42/signals.json stop
```

The control channel is the signal store of the run (`ROBOT_FLOW_SIGNALS`), so
nothing new has to be opened or installed, and every flow that shares the
store pauses together: two rigs in lockstep stop at their next boundary and
neither times out on the other. `--rig RIG_A` addresses one process, the one
started with `--variable FLOW_RIG:RIG_A` (or `ROBOT_FLOW_RIG`). A flow started
without `ROBOT_FLOW_SIGNALS` has no control channel. A command given before a
process started is not for that process and is ignored by it.

- **Where a flow pauses.** Between two steps, at the start of a loop iteration
  and between two polls of a gate. A keyword that has started always
  finishes: a pause asked for during a 20 second measurement takes effect
  when the measurement returns. `THREAD` workers pause at their own next
  keyword boundary.
- **Paused time does not count.** Loop deadlines (`max_seconds`) and gate
  timeouts are measured on the *flow clock*, which stands still while the
  flow is paused: a 15 second gate held for an hour still has its 15 seconds.
  Every [Watchdog](watchdog.md) is paused with the flow, so a deliberate hold
  is not a stall. A fixed `sleep` node is the exception: it is a keyword, and
  runs to its end.
- **In the log.** `Flow paused by operator at iteration 37 of loop 'loop'.`
  and `Flow resumed after 22 minutes 4 seconds.` appear where the flow stood.
- **From a test.** `Flow Pause`, `Flow Resume` and `Flow Stop` are keywords of
  `robot.flow.keywords`. A Watchdog's `on_timeout=Flow Pause` holds the bench
  for a human instead of aborting the run.
- **Step mode.** `--variable FLOW_STEP:yes` pauses before every node of the
  test phases and waits for `resume` (or Enter, when the run has a console):
  a way to walk a new plan through the bench one step at a time.

### Changing a running flow: `set` and `snapshot`

```bash
python -m robot.flow control results/run42/signals.json set VOLTS=14.2 MODE=EMC
python -m robot.flow control results/run42/signals.json snapshot before the swap
```

`set` gives a running flow new values: at its next step boundary the flow
sets them as suite variables and logs `Variable ${VOLTS} set to '14.2' by
operator (suite scope).` on the console too. Keyword arguments and decision
conditions use the new value from then on. A loop's `max_loops` /
`max_seconds` are read when the loop starts, so a set changes the next loop,
not the one running. `--rig` addresses one process, as for `pause`.

`snapshot` writes the flow's state as `<flow>.snapshot-<name>.json` next to
the checkpoint, at the next step boundary, and the flow goes on. The file is
a checkpoint plus a `snapshot` section (when and where it was taken, and the
JSON-safe variables in scope, for inspection), it is **kept** when the run
ends, and a new run continues from it like from a checkpoint:

```bash
robot --parser robot.flow --variable FLOW_CHECKPOINT:results/run42/plan.snapshot-before_the_swap.json \
      --variable VOLTS:13.5 --outputdir results/run42-replay plan.flow.json
```

A value given with `--variable` wins over the one the snapshot (or any
checkpoint) saved for the same name, and the log says `Kept from the command
line instead of the checkpoint: ${VOLTS}.` -- that is how a bad cycle is
replayed with a changed value. The run writes its own `plan.checkpoint.json`
next to the snapshot; the snapshot stays as it was. The flow report lists
the snapshots of a run at their loop, linked into the log. From inside a
plan, the keyword `Flow Snapshot    name` does the same, for example in a
recovery region, so the state at a failure is kept.

A snapshot is not a core dump: the bench is not in it. A restart from it
re-establishes the bench with the setup phase and continues the plan after
the last completed iteration, as after a stop.

### Stopping

`stop` ends the run in an orderly way. The flow leaves at its next step
boundary; an iteration that was already running is finished first when the
stop arrives during its last step, and done again after the restart otherwise.
The checkpoint is written, the test ends **UNKNOWN** with
`Stopped by operator at iteration 38 of loop 'loop'; resumable from
results/run42/plan.checkpoint.json.`, the remaining tests are not started and
the teardown runs, so the bench is released. No recovery region catches a
stop, and a stop cannot be taken back: a `resume` or `pause` given after it
does not cancel it. UNKNOWN is the honest verdict: nothing failed and the plan did not
complete, and the [return code](unknown-status.md#return-code) keeps the two
apart for whatever started the run.

### The checkpoint

While a flow runs, a small JSON file records the test phases that finished,
and for the loops directly in a test phase the number of completed
iterations, what is left of `max_loops` and `max_seconds`, and the values of
the variables the phase assigns:

```json
{
  "flow": "plan.flow.json", "fingerprint": "sha1 of the flow file",
  "position": { "phase": "Cycle", "loop": "loop", "iteration": 37 },
  "remaining": { "max_loops": 963, "max_seconds": 21540.7 },
  "variables": { "${n}": 37 },
  "completed_phases": [ { "name": "Precheck", "status": "PASS", "finished": "2026-09-30 08:14:11" } ],
  "saved": "2026-09-30 14:03:19"
}
```

- **Where.** `<output directory>/<flow file name>.checkpoint.json`
  (`plan.flow.json` gives `plan.checkpoint.json`). `"checkpoint": "path"` in
  the `flow` section or `--variable FLOW_CHECKPOINT:path` names another file;
  a relative path is taken from the output directory. A restart into a new
  output directory therefore needs `FLOW_CHECKPOINT` pointing at the old file.
- **When.** At the start and end of every test phase, at the start of a loop
  iteration (at most once a second, so fast loops are not slowed down; a
  crash can then cost up to a second of iterations), and on `stop`.
  `"checkpoint_every": 100` or `--variable FLOW_CHECKPOINT_EVERY:100` writes
  every hundredth iteration instead, `1` every iteration. Each write leaves a
  line in the iteration (`Iteration 1200 of loop 'loop'; checkpoint
  written.`), so the log shows the progress at that pace and nothing more:
  counting an iteration costs no keyword call.
- **Written whole, and to the disk.** The file is written to a temporary name,
  flushed to the disk (`fsync`) and renamed into place: a reader never sees
  half of it, and a power loss right after a write does not lose it.
- **Kept or deleted.** A run that reaches its end deletes its checkpoint. A
  stopped or crashed run leaves it.
- **Not wanted.** `"checkpoint": false` in the `flow` section builds the flow
  without it (and without the three bookkeeping keywords it adds).
- Values that JSON cannot hold are left out with a warning; the run goes on.

### Restarting

```bash
robot --parser robot.flow --variable FLOW_CHECKPOINT:results/run42/plan.checkpoint.json \
      --outputdir results/run42b plan.flow.json
```

| `FLOW_RESUME` | Behaviour |
|---------------|-----------|
| `auto` (default) | continue from a checkpoint that belongs to this flow file; start from the beginning when there is none; warn and start from the beginning when it belongs to another version of the file |
| `always` | continue, or end UNKNOWN when there is no matching checkpoint |
| `never` | ignore a checkpoint and overwrite it |

| Part of the plan | On restart |
|------------------|------------|
| setup phase | runs again: the state of the bench lives outside the flow, and the setup is how it is established |
| test phases that finished | **SKIP** with `Completed with status PASS in the run that ended 2026-09-30 08:14:11 (see results/run42).` Visible, not run again. |
| the interrupted test phase | the steps before its loop run again; the loop continues after the completed iterations with what is left of its bounds, and the saved variables are restored when the loop starts |
| later phases, teardown | as usual |

The unit of a restart is the loop iteration: the interrupted one is done
again, so **iterations must be safe to repeat**. Only loops directly in a
test phase are tracked. A loop inside a decision, a `try`, another loop, a
sub-flow or a branch of a parallel region starts from its beginning, like any
other step; a parallel region itself is one step of its iteration and runs
again as a whole. A flow file that
changed since the checkpoint does not match it any more (the fingerprint is
that of the main file; sub-flow files are not part of it).

Reading the two outputs together gives the verdict of the plan; `rebot` merges
them into one report.

### After a crash

A killed process, a bench PC that rebooted, a power cut: nothing orderly
happened, so there is no `Stopped by operator` message and no teardown. What
is left is the checkpoint, which is as recent as the last iteration boundary
(or at most one second old; `checkpoint_every: 1` makes it exact), and the
output file, cut wherever the process died. The restart is the same command
as after a stop and behaves the same: the setup phase runs again, finished
phases SKIP, the loop continues after its last *completed* iteration, so the
interrupted one is done again.

A cut `output.xml` cannot be read by `rebot`. For endurance runs, run with
`--segmentoutput 1h` (or shorter): every sealed segment is a complete output
of its hour and `python -m robot.output.segmentmerger` joins them, so the
log of the crashed run is lost only for the last, unsealed interval. See
[Segmented output](segmented-output.md).

This path is tested: the unit test `utest/flow/test_crash_recovery.py` kills a
running flow without warning and checks that the continued run covers the
plan exactly once, except the interrupted iteration.
