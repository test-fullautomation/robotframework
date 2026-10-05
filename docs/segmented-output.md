# Segmented output

**A crash costs you the last hour of log — not the whole run.**
{ .lead }

!!! pain "The pain"
    `output.xml` is one XML document that stays open for the whole run. When the
    bench PC loses power at hour 14, the file is cut mid-write and cannot be
    parsed: no `log.html`, no report, no idea what happened in fourteen hours.

!!! fix "The fix"
    One option. At every interval the log is closed into a complete, readable
    piece and a new piece starts. A crash loses at most the current interval;
    everything before it is safe and can be turned back into a report with one
    command.

```plantuml
!include diagrams/segment_timeline.puml
```

```bash
robot --segmentoutput 1h --outputdir results endurance.robot
```

Nothing else changes: at the end of the run the artifacts are the usual
`output.xml`, `log.html`, `report.html` (and `timeline.html` with `--timeline`).

## How it works

The writer tracks its open-element stack. At an interval boundary it

1. closes the whole stack, making the file well-formed,
2. seals it as `output_part_NNN.xml`,
3. opens a fresh file at the live path and replays the stack, marking every
   replayed element (except the root) with `continued="true"`.

Each segment is an ordinary, parseable `output.xml` covering one time slice.

```plantuml
!include diagrams/segment_rotation.puml
```

### Thread outputs rotate too

Every `THREAD` block writes its own file (see
[result merging](thread/merging-timeline.md)), and each thread writer is the
same segmenting writer: long-living threads produce
`output_<THREAD>_part_NNN.xml`. Each writer is owned by one thread and rotates
at that thread's own logging events, so rotation needs **no locking** and never
pauses another thread. A thread that logs nothing for a while does not rotate
during the silence — nothing unsealed is at risk in that window.

### Merging — the continued chain

At the end of a normal run the segments are merged back automatically. The
merger walks the segments in order and appends each segment's children to the
previous one; an element marked `continued="true"` is joined with the element
that was open at the same position when the previous segment was sealed —
recursively, down the whole chain. Statuses of split elements arrive with the
final segment, so the merged document carries the correct results. The
algorithm is root-agnostic: the same code joins `<robot>` segments and
`<thread>` segments.

The merged output is structurally identical to an unsegmented run — log,
report, `rebot` and the timeline need no changes.

## After a crash

The sealed segments are safe; the live file is usually cut mid-write. The
merger repairs a truncated live file (drops a partial trailing tag, closes the
open elements) before joining it:

```bash
python -m robot.output.segmentmerger results/output.xml            # main output
python -m robot.output.segmentmerger results/output_MONITOR.xml    # each thread that rotated
python -m robot.output.threadmerger  results/output.xml            # graft the threads
rebot --outputdir results results/output.xml                       # log.html and report.html
```

Add `--keep` to either merger to keep the part files. Verified by killing live
runs: even with both live files at 0 bytes, all sealed content — including
keywords inside threads — was recovered into a browsable `log.html`.

!!! tip "Try it"
    The endurance demo (`demo/longrun/`) runs for as long as you ask, with a
    monitor thread, and `--variable CRASH_AFTER:90s` simulates a power loss;
    `recover.py` performs the four steps above.

## Implementation map

| Component | Responsibility |
|-----------|----------------|
| `_TrackingWriter` (`output/xmllogger.py`) | Writer proxy: open-element stack, `rotate()`, `maybe_rotate(interval)` with a per-writer timer; files still open at run end are closed well-formed |
| `XmlLogger._maybe_rotate` | Called at keyword start/end and on log messages; each thread rotates only its own writer |
| `output/segmentmerger.py` | Continued-chain merge, truncation repair, CLI; root-agnostic |
| `output/threadmerger.py` | Joins each thread's segments before grafting; ignores `*_part_NNN` files during discovery |
| `output/outputfile.py` | Passes the interval to the XML logger and exposes the sealed segment paths |
| `conf/settings.py` | `--segmentoutput` as a validated, positive time string (`4h`, `30min`, …) |

## Properties and limitations

- **Bounded loss** — a crash costs at most one interval per writer; everything sealed is safe.
- **Zero contention** — rotation piggybacks on each thread's own logging events.
- **Transparent downstream** — the merged output equals an unsegmented run.
- Statistics and the `<errors>` section are written once, in the final segment.
- XML output only: with JSON output the option is ignored with a warning.
- A silent thread does not rotate until its next log event (by design).
- Do not name a `THREAD` literally `part` or `*_part_NNN`: it would collide with
  the sealed-file naming during automatic discovery.
