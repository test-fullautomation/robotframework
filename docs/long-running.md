# Long-running tests

**Everything a two-day test needs, and nothing it has to be rewritten for.**
{ .lead }

!!! pain "The pain"
    Endurance tests hit problems a ten-minute run never sees: a crash late in
    the run loses everything logged so far, a keyword hangs silently on dead
    hardware, memory creeps up over days, and a two-day log is unreadable.

!!! fix "The fix"
    A set of independent features, each covering one risk, that snap together.
    Keyword names and files are the only contracts between them — use one, or
    all.

```plantuml
!include diagrams/compose_48h.puml
```

## How the features compose for a 48-hour run

| Risk | Covered by | Coupling |
|------|------------|----------|
| Cyclic, condition-driven flow; a crash at hour 40 must resume, not restart | [StateMachine](statemachine.md) with checkpoints | — |
| A bounded plan: setup, gates, cycle loop, recovery, teardown | [Flow files](flow.md) | — |
| Stuck keyword or silent hang | [Watchdog](watchdog.md) in a `THREAD` block | supervised code calls `Feed Watchdog`; `on_timeout=` names the reaction keyword |
| Log data lost on a crash | [Segmented output](segmented-output.md) | none — core output feature |
| Unreadable two-day log | `--splitlog`, state-visit blocks, the [timeline](thread/merging-timeline.md#execution-timeline) | none |
| Memory growth over days | bounded memory (below) | none — core fix |
| "Bench not ready" reported as a product failure | [UNKNOWN status](unknown-status.md) and the return code | none |

## Where things live

| Feature | Key modules | Entry points |
|---------|-------------|--------------|
| THREAD reporting (merge + timeline) | `robot/output/xmllogger.py`, `robot/output/threadmerger.py`, `robot/timeline.py` | merge automatic at run end; `robot --timeline t.html`; `python -m robot.output.threadmerger`; `python -m robot.timeline` |
| StateMachine | `robot/libraries/StateMachine.py` | `Library    StateMachine` |
| Watchdog | `robot/libraries/Watchdog.py` | `Library    Watchdog` + a `THREAD` block |
| Segmented output | `robot/output/xmllogger.py`, `robot/output/segmentmerger.py` | `robot --segmentoutput 4h`; `python -m robot.output.segmentmerger` |
| Flow files | `robot/flow/` | `robot --parser robot.flow`; `python -m robot.flow` |

## Segmented output

`--segmentoutput <time>` periodically seals the output into well-formed
segment files, so a crash loses at most one interval of log data; segments are
merged back automatically at the end of a normal run. See
[Segmented output](segmented-output.md) for the mechanism, crash recovery and
limitations.

## Bounded memory

Execution memory stays flat regardless of run length. Robot Framework streams
`output.xml` to disk and never keeps keyword results in memory, but four
accumulators used to grow without bound during very long runs. They are now
bounded in the core:

| Accumulator | Behaviour now | What is lost |
|-------------|---------------|--------------|
| Framework message replay cache | capped at 10 000; a listener registering late gets the cache plus a "N further messages were not cached" note | nothing from any file — it was only a replay buffer |
| `<errors>` section collection | first 10 000 kept in memory, overflow **spilled** to a sidecar file (`output_errors_spill.jsonl`) and folded back into the section at run end | nothing — the section stays complete |
| Thread notification queues | drop-oldest at 10 000 per queue (FIFO and LIFO); a one-time WARN names the queue nobody consumes | only dead signals — a full queue already means a missing `Wait Thread Notification` |
| Debug-file per-thread bookkeeping | released at thread end | nothing |

All limits are class attributes, tunable in one place
(`Logger.message_cache_limit`, `XmlLogger.max_errors`, `PriorityQueue.max_items`).

WARN, ERROR and UNKNOWN messages are therefore never silently lost on a long
run — they are moved to disk when memory would otherwise grow.

### Keeping report generation proportionate

What remains proportional to run length is the report-generation step
(parsing the finished `output.xml` into `log.html`). Bound it at the source and
at rebot time:

- tag noisy inner keywords with `robot:flatten`, and run with `--loglevel INFO`;
- at rebot time use `--flattenkeywords`, `--removekeywords passed` and `--splitlog`;
- or generate per-segment logs from the `--segmentoutput` parts.

## Return code on long runs

Because the [return code encodes the UNKNOWN count](unknown-status.md#return-code)
as well as the failed count, a long unattended run that ends only with UNKNOWN
results (a broken environment, abandoned threads) still exits non-zero and is
caught by CI, rather than appearing green.

## Reporting stays valid

The output shortening that keeps `output.xml` small at higher log levels only
suppresses individual below-level `BuiltIn.Log` keywords, and only when nothing
visible is logged inside them — all structural elements are always written. This
guarantees `output.xml` is well-formed and re-processable (by `rebot`, mergers
and log generation) no matter the log level, which matters when a long run is
post-processed days later.

## Runnable examples

- `demo/longrun/` — an endurance run of configurable length with a monitor
  thread, segmented output, and a simulated power loss plus `recover.py`.
- `demo/statemachine/` — seven graded StateMachine examples up to the 48-hour
  battery endurance scenario.
- `demo/flow/` — flow files from a three-node hello-world to two processes
  meeting at a gate.
