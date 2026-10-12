# Flow Runner Demo

Runnable flow files, ordered from a three-node hello-world to two robot
processes meeting at a gate. Every flow is executed with the fork's
`--parser robot.flow`, which turns the file into a suite at run time. The
bench they drive is simulated in `bench_keywords.py` (a climate chamber,
power, cycle tests, an ECU to flash), so everything runs offline. Flows that
talk to each other use the runner's own library, `robot.flow.signals`;
`run.py` and `run_two_rigs.py` point it at a fresh `results/signals.json`
for every run (`ROBOT_FLOW_SIGNALS`).

## Run it

```
python run.py                              # every flow, results in ./results
python run.py 02_endurance_cycle.flow.json   # one flow
python run.py --dryrun 03_flash_campaign.flow.json   # check keywords and arguments without running them (libraries are still imported)
python run_two_rigs.py                   # 04 as two processes that meet at the gate
python run_two_rigs.py --alone           # 04 with no peer: the gate times out, verdict UNKNOWN
```

Two more commands are worth showing:

```
python -m robot.flow validate 03_flash_campaign.flow.json   # shape and structure only
python -m robot.flow render   03_flash_campaign.flow.json   # the equivalent .robot text
```

(Both need the checkout's `src` on `PYTHONPATH`, or the fork installed;
`run.py` finds it by itself.)

## The learning path

| # | Flow | New concepts |
|---|------|--------------|
| 1 | `01_hello.flow.json` | Nodes and edges; `keyword` and `sleep`; a flow without phases is one test named after the flow |
| 2 | `02_endurance_cycle.flow.json` | Phases (setup / test / teardown); a `gate` on the chamber with a real timeout instead of a guessed `Sleep`; a bounded `loop` (`max_loops`, `max_seconds`, `every`); `on_failure` recovery with `continue` — cycle 3 fails on purpose, recovery runs, the loop carries on |
| 3 | `03_flash_campaign.flow.json` | `assign` a keyword's return value; a `decision` that makes re-runs idempotent; a `try` region whose recovery ends with `abort` — the golden image is always restored and the verdict keeps the original flash error |
| 4 | `04_rendezvous.flow.json` | Two flows communicating through `robot.flow.signals`: each announces itself (`Set Signal`) and gates on its peer (`Signal Should Be`), then they run three cycles in **lockstep** — each publishes its cycle number and waits until the peer has reached it. One file serves both rigs via `--variable RIG:` / `PEER:` |

| 5 | `05_parallel_ramp.flow.json` | A `parallel` region: the chamber ramps while the supply comes up, as two `branch`es that `join` before the measurement; then a region whose `flash` branch fails and stops the cycling branch (a `loop` as a branch), with `on_failure` restoring the golden image. Each branch is a `THREAD` in `log.html`; the flow report draws the branches side by side |

### What to try with the flash campaign

```
python run.py --variable IMAGE:fw-2.1 03_flash_campaign.flow.json   # flashes and verifies: PASS
python run.py --variable IMAGE:fw-2.1 03_flash_campaign.flow.json   # already current: the decision skips the flash
python run.py --variable IMAGE:fw-bad 03_flash_campaign.flow.json   # checksum fails, golden image restored, verdict FAIL "Flash failed: ..."
```

## What to open afterwards

| File in `results/` | What to look at |
|--------------------|-----------------|
| `log.html`     | The generated structure as Robot shows it: `Flow Setup`, the `WHILE` with its iterations, `TRY` / `EXCEPT`, `IF` / `ELSE`; each `Flow Gate` with its attempts |
| `report.html`  | Verdicts: `04` run alone is **UNKNOWN**, not FAIL — the peer was missing, nothing was tested |
| `timeline.html`| The cycle history over time |
| `rig_a/`, `rig_b/`| One result set per rig after `run_two_rigs.py` |
| `signals.json` | The signals of the last run: value, time and writing process of each |

## The one idea

A flow is a **workflow**: nodes are things the test *does*, edges mean
*"then"*, every loop is bounded, every run ends with a verdict. It is not the
StateMachine library (nodes are what the system *is*, edges mean *"when"*),
and it is never run through that engine — see `../statemachine/` for the
other model, and the docs page *Flow files* for the rules the validator
enforces.
