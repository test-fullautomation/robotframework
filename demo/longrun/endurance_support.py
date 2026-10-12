"""Keywords behind the endurance demo: a fake bench, a progress monitor and
a simulated power loss."""

import glob
import math
import os
import random
import time

from robot.api import logger
from robot.libraries.BuiltIn import BuiltIn
from robot.utils import secs_to_timestr, timestr_to_secs


START = time.time()
STATE = {'cycles': 0}


def deadline_after(duration):
    """Returns the epoch time ``duration`` (a Robot time string) from now."""
    return time.time() + timestr_to_secs(duration)


def measure(cycle):
    """Returns one cycle of measurements. Deterministic per cycle, with a slow
    drift so long runs show a trend in the log."""
    cycle = int(cycle)
    STATE['cycles'] = cycle
    rnd = random.Random(cycle)
    drift = 0.002 * cycle
    return {
        'voltage': round(12.0 + 0.4 * math.sin(cycle / 7.0) + rnd.uniform(-0.1, 0.1), 3),
        'current': round(4.5 + 0.5 * math.cos(cycle / 11.0) + rnd.uniform(-0.05, 0.05), 3),
        'temperature': round(45 + 20 * math.sin(cycle / 200.0) + drift + rnd.uniform(-1, 1), 1),
        'drift': round(drift * 1000, 1),
    }


def log_snapshot(cycle):
    """Logs a large message, the kind a diagnostics dump produces."""
    lines = [f'register[{i:03d}] = 0x{random.Random(cycle * 1000 + i).getrandbits(32):08X}'
             for i in range(200)]
    logger.info(f'Diagnostics snapshot after cycle {cycle}:\n' + '\n'.join(lines))


def monitor_until(deadline, every='5 s'):
    """Runs in the MONITOR thread: reports progress and the size of the output
    files (live file plus sealed segments) until ``deadline``."""
    step = timestr_to_secs(every)
    output = BuiltIn().get_variable_value('${OUTPUT FILE}')
    base, ext = os.path.splitext(output)
    while True:
        remaining = float(deadline) - time.time()
        elapsed = time.time() - START
        parts = sorted(glob.glob(f'{glob.escape(base)}*_part_*{ext}'))
        live = [p for p in glob.glob(f'{glob.escape(base)}*{ext}') if p not in parts]
        size = sum(os.path.getsize(p) for p in parts + live if os.path.exists(p))
        logger.info(f'+{secs_to_timestr(elapsed, compact=True)}: {STATE["cycles"]} cycles, '
                    f'{len(parts)} sealed segment(s), {size / 1024:.0f} KB of output on disk')
        if remaining <= 0:
            return
        time.sleep(min(step, remaining))


def maybe_crash(after=None):
    """Simulates a power loss: exits the process without any clean-up once
    ``after`` (a Robot time string) has elapsed since the suite started."""
    if after in (None, '', 'None', 'NONE'):
        return
    if time.time() - START >= timestr_to_secs(after):
        logger.warn(f'Simulated power loss after {after}. Nothing is flushed - '
                    f'run recover.py to rebuild the log from the sealed segments.')
        os._exit(1)
