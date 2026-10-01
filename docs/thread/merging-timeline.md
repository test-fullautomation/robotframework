# Result merging and timeline

**Every thread's work in one report — and a map of who ran when.**
{ .lead }

!!! pain "The pain"
    Two threads cannot write into one `output.xml` at once. Without help, a
    background thread's keywords, messages and failures simply never appear in
    `log.html`. And even when they do, a tree cannot show what ran *at the same
    time* — which is exactly what you need when a parallel test misbehaves.

!!! fix "The fix"
    Each thread writes its own file while it runs — no locks, nothing slowed
    down — and at the end of the run its log is grafted back into `output.xml`
    at the place the `THREAD` started. `--timeline` then draws one lane per
    thread on a real time axis, and every bar is a link into `log.html`.

```plantuml
!include diagrams/timeline_lanes.puml
```

<p class="figure-caption">What <code>--timeline</code> shows: the real interleaving of the main flow and two workers. Click a bar to land on that keyword in log.html.</p>

## How a worker's output reaches log.html

Robot Framework streams results to a single, deeply nested `output.xml` as it
runs. Two threads cannot write into one XML element tree at the same time, so
each worker streams to **its own file** — lock-free during execution — and the
files are grafted back into the main document at the end of the run.

```plantuml
!include diagrams/merge_flow.puml
```

The design rests on two artifacts that exist when execution finishes:

| Artifact | Written by | Content |
|----------|-----------|---------|
| `<thread name="X" daemon="…">` placeholder in `output.xml` | the main thread, when the `THREAD` starts | an empty anchor at the exact position of the block |
| `output_X.xml` | worker thread X | a root `<thread>` with the complete body: keywords, control structures, messages, real timestamps, the final `<status>` |

Each thread owns its writer (a class-level dictionary routes by thread name), so
the hot logging path needs no locks. Because the result is one ordinary
`output.xml`, everything downstream — `log.html`, `report.html`, `rebot`, xUnit,
the timeline — works unchanged.

### The graft operation

```xml
<!-- output.xml, before -->               <!-- output_X.xml -->
<test id="s1-t1" name="Demo">             <thread name="X" ...>
  <kw name="Log">...</kw>                   <kw name="Log">...</kw>
  <thread name="X" daemon="True">           <for flavor="IN RANGE">...</for>
    <status status="PASS" .../>  <- provisional   <status status="PASS" starttime="..." endtime="..."/>  <- real
    <doc/>                                  </thread>
  </thread>
</test>

<!-- output.xml, after the merge -->
<thread name="X" daemon="True">
  <kw name="Log">...</kw>
  <for flavor="IN RANGE">...</for>
  <doc/>
  <status status="PASS" starttime="..." endtime="..."/>
</thread>
```

The children of the thread file's root move under the placeholder, and the
provisional status is replaced by the real one.

- **Idempotent** — a placeholder is recognised by having only `<status>`/`<doc>`
  children, so re-running the merger on an already-merged file does nothing.
- **Truncated thread files** (crashed runs, daemons still running at the end)
  are recovered by appending the missing closing tags.
- **No recorded status** (e.g. an abandoned thread) → an **UNKNOWN** status is
  synthesised, so the output stays complete and valid.
- **Statistics** are computed from test statuses, not thread contents — grafting
  never changes pass/fail counts.

### One block in log.html, not interspersed

`log.html` renders a tree, not a timeline: order on screen is document order,
nesting is element nesting, timestamps are labels. Each `THREAD` therefore
appears as **one collapsible block** (like a FOR loop) at the position where it
was started, with real per-event timestamps inside. Interleaving thread keywords
between main-flow keywords would corrupt parentage, status roll-up and the
schema — the true wall-clock interleaving is what the timeline shows.

### Manual merge and crash recovery

Nothing to configure: merging runs automatically when the output is closed.
After a crashed run (or before running `rebot` on one):

```bash
python -m robot.output.threadmerger results/output.xml     # --keep keeps the thread files
rebot --outputdir results results/output.xml
```

With [segmented output](../segmented-output.md) the thread merger first joins
each thread's `_part_NNN` segments.

## Execution timeline

`--timeline <file>` writes a complementary view from the same merged output:
one horizontal **lane per thread** (plus `MainThread`), keywords drawn as bars
positioned by their real start and end timestamps on a shared axis, coloured by
status, and each thread's lifetime as a translucent span. `log.html` stays the
source of detail; the timeline is the map.

```bash
robot --timeline timeline.html --outputdir results tests/        # during the run
rebot --timeline timeline.html output.xml                        # from an existing output
python -m robot.timeline output.xml -o timeline.html --log log.html
```

```python
from robot.timeline import generate_timeline
path, count = generate_timeline('output.xml')     # count: number of tests drawn
```

### Click-through to log.html

Every bar is **clickable**: it navigates into `log.html`, expands the tree to
the corresponding element and flashes it so you can see where you landed.

- The timeline computes the same positional ids `log.html` uses
  (`s1-t1-k2-k3` = suite 1 → test 1 → 2nd body item → its 3rd child), by
  replicating the log model's rules: test and suite ids come from `output.xml`;
  messages consume no numbers; IF/TRY roots are flattened so their branches take
  the positions; THREAD nodes count as regular positions. The mapping was
  verified 1:1 against the real log model.
- Each click appends an incrementing `~n` suffix to the link, so the hash
  changes even when the same bar is clicked twice; `log.html` strips it before
  resolving the element. `log.html` listens for hash changes (not only on page
  load) and shows a visible highlight.

### Rules of use

- Ids are positional: regenerate the timeline whenever `log.html` is
  regenerated. `--timeline` does this automatically; multi-source `rebot` merges
  are refused because positions would not match.
- Only top-level bars per lane are drawn — nested keywords stay in `log.html` to
  keep the map readable. Nested THREADs still get their own lanes.
- `timeline.html` is self-contained and links relatively — keep it next to
  `log.html`.

!!! tip "Naming threads"
    The merged block and the timeline lane are keyed by the thread name. Using
    the same name twice is allowed, but the second run overwrites the first
    file; the merger warns and attaches the surviving log to the last
    placeholder. Give concurrent or repeated workers distinct names.
