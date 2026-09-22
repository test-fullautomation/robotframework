# Result merging and timeline

## How a worker's output reaches log.html

Robot Framework streams results to `output.xml` as it runs. A background worker
cannot write into the middle of that single stream without corrupting it, so
each worker streams to **its own file** and the results are merged at the end.

```plantuml
!include diagrams/merge_flow.puml
```

1. At the position of the `THREAD` keyword the main thread writes a `<thread>`
   **placeholder** into `output.xml`.
2. The worker streams its own keywords to `output_<name>.xml`.
3. At the end of the run the **threadmerger** grafts each thread's body into its
   placeholder. If a thread has no recorded status (e.g. it was abandoned), an
   **UNKNOWN** status is synthesised so the output stays complete and valid.

The result: in `log.html` and `report.html` the worker's keywords appear as a
single coherent `THREAD <name>` block at the point where the thread started,
not scattered or missing.

### Crash recovery

If a run is interrupted, the partial per-thread files can be merged manually:

```bash
python -m robot.output.threadmerger    # graft thread outputs into output.xml
```

## Timeline

`--timeline <file>` writes an interactive execution timeline alongside the log:

```bash
robot --timeline timeline.html --outputdir results my_suite.robot
```

The timeline shows one **lane per thread** (the main thread and every worker),
with bars for each keyword positioned on a real time axis, so the actual
interleaving of concurrent work is visible at a glance. Every bar is
**clickable**: it navigates into `log.html`, expands the tree to the
corresponding keyword and flashes it briefly so you can see where you landed.

!!! tip "Naming threads"
    The timeline lane and the merged block are keyed by the thread name. Reusing
    the same name for several `THREAD` blocks is allowed, but the thread log is
    attached only to the last occurrence — give concurrent or repeated workers
    distinct names when you want each one to show separately.
