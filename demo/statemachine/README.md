# StateMachine Library Demo

Runnable examples for the `StateMachine` standard library, ordered from a
five-line hello-world machine up to the 48-hour battery endurance scenario
from the documentation (scaled down to seconds). Work through them in order —
each example introduces exactly the concepts the next one builds on.

## Run it

```
python run.py                        # all examples, results in ./results
python run.py 01_first_machine.robot # a single example
```

(or, with the fork installed: `robot --outputdir results --timeline timeline.html .`)

## The learning path

| # | Suite | New concepts |
|---|-------|--------------|
| 1 | `01_first_machine.robot` | `Define State`, `Define Transition`, `Run State Machine`, final states, the unconditional (default) transition |
| 2 | `02_conditions_and_variables.robot` | `during` keywords polled every interval, guarded transitions with `$var` conditions, guard evaluation order, `Get State Statistics` |
| 3 | `03_machine_from_file.robot` + `coffee_machine.json` | `Load State Machine` (JSON, or YAML with pyyaml installed), per-state keyword arguments, `exit` keywords |
| 4 | `04_timeouts_and_errors.robot` | `on_error` recovery routing, per-visit `timeout`, `max_visits` as a guard against transition ping-pong |
| 5 | `05_checkpoint_and_resume.robot` | `Checkpoint Variable`, `checkpoint=`, `max_duration`, `resume=AUTO` — interrupt a run and continue it in the next test |
| 6 | `06_concurrent_machines.robot` | Named machines (`machine=`), running a machine inside a `THREAD` block, `Stop State Machine` from the main flow |
| 7 | `07_battery_endurance.robot` + `battery_machine.json` | Everything combined, plus Watchdog supervision and fault injection — the pattern for real multi-day runs |

## What to open afterwards

| File in `results/` | What to look at |
|--------------------|-----------------|
| `log.html`     | Each state visit as a collapsible `STATE ... (visit n)` block; the SUPERVISOR and STIM_SM threads as their own blocks; per-state statistics at machine end |
| `timeline.html`| Lanes for MainThread / SUPERVISOR / STIM_SM with real interleaving; every bar deep-links back into log.html |
| `report.html`  | Overall results |

## Example 5 in one sentence (the killer feature)

Test 1 aborts on `max_duration` (simulating a crash at "hour 40") and leaves
`resume_checkpoint.json`; test 2 resumes with restored state, counters and
registered variables and completes — that is what makes multi-day runs
restartable.

## Also demonstrated by the runner

- `--timeline` — the per-thread execution timeline
- `--segmentoutput 30s` — crash-safe output segmentation (segments are
  merged back automatically at the end of the run)

## Further reading

- Library reference: `python -m robot.libdoc StateMachine StateMachine.html`
- The feature guide: `docs/statemachine.md` in this repository (*StateMachine*
  on the documentation site)
