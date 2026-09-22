# Long-running tests

Endurance tests run for hours or days. Two problems appear at that scale that a
normal test run never hits: a crash late in the run loses everything logged so
far, and the in-memory result model grows without bound. The fork addresses
both.

## Segmented output

`--segmentoutput <time>` periodically seals the output into a well-formed
segment file, so a crash during a very long run loses at most one interval of
log data instead of the whole run:

```bash
robot --segmentoutput 30s --outputdir results my_long_suite.robot
```

- Each writer (the main writer and every per-thread writer) rotates its own
  file into `output_part_NNN.xml` / `output_<name>_part_NNN.xml`.
- Segments are chained (`continued="true"`) and merged back into a single
  `output.xml` automatically at the end of a normal run.
- After a crash, recover the sealed segments manually:

```bash
python -m robot.output.segmentmerger    # merge sealed segments
python -m robot.output.threadmerger      # graft thread outputs (if threads were used)
```

## Bounded memory

Several unbounded collections are capped so a days-long run stays within a fixed
memory budget, without losing information that matters:

| Collection | Cap | Behaviour at the cap |
|------------|-----|----------------------|
| Message cache | `10000` | Oldest cached messages are dropped and counted; the log on disk is unaffected. |
| `<errors>` section | `10000` in memory | Overflow **spills to a sidecar file** (`<output>_errors_spill.jsonl`) and is read back when the section is written, so the errors section stays complete. |
| Per-thread notification queue | `10000` | Oldest entry is dropped (for FIFO and LIFO queues) if the queue is never drained. |

The errors-section spill is the important one: it means WARN/ERROR/UNKNOWN
messages are never silently lost on a long run, they are just moved to disk when
memory would otherwise grow.

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
