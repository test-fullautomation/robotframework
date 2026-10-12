# Endurance Run Demo

One suite that runs as long as you tell it to — ten minutes by default, eight
hours if you say so — to demonstrate what the fork does for long runs:
**segmented output**, a **per-thread output** for the monitor thread, the
**timeline**, and **recovery after a crash**.

## Run it

```
python run.py                              # 10 min, results in ./results
python run.py --variable DURATION:8h       # the real thing
python run.py --variable DURATION:2h --variable CYCLE:5s
```

While it runs, look at `results/`: every 30 seconds the live `output.xml` is
sealed into `output_part_NNN.xml` (and the monitor thread's file into
`output_MONITOR_part_NNN.xml`) and a fresh live file starts. The MONITOR thread
logs, every 5 seconds, how many cycles are done and how much output is on
disk. At a normal end everything is merged back into a single `output.xml`
and `log.html` / `timeline.html` are written as usual.

## Crash it, then recover

```
python run.py --variable CRASH_AFTER:90s   # os._exit(1) mid-cycle, nothing flushed
python recover.py                          # merge sealed segments, graft the thread, rebot
```

`recover.py` repairs the live file that was cut mid-write, merges every
`_part_NNN` chain in order, grafts the thread output, and runs `rebot`. Open
`results/log.html`: all cycles up to the last sealed segment are there, the
MONITOR thread too, and the test is shown as it was when the power went — not
lost.

## What the suite does

| Piece | Why it is there |
|-------|-----------------|
| `WHILE time.time() < ${deadline}` | a run bounded by wall-clock time, not by iteration count |
| `Run Cycle` — measurements, two range checks | ordinary keywords, five log lines per cycle |
| a WARN every 25 cycles | warnings go to the errors section — bounded in memory, spilled to disk on very long runs |
| a 200-line diagnostics dump every 100 cycles | makes the output grow, so segmentation has something to show |
| `THREAD MONITOR` | a second writer: its output is segmented and merged independently |
| `Maybe Crash` | `os._exit(1)` at a chosen time — no flush, no teardown, like a power loss |

## Related

- `docs/long-running.md` in the fork's documentation site — segmented output,
  bounded memory, return codes on long runs.
- `../statemachine/` — the same long-run safeguards driving a state machine.
- `../flow/` — a long run described as a flow file (`02_permanent_run`).
