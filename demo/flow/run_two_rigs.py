#!/usr/bin/env python
"""Runs 04_rendezvous.flow.json as two rigs in two robot processes.

    python run_two_rigs.py            # RIG_A and RIG_B meet, then run three cycles in lockstep
    python run_two_rigs.py --alone    # only RIG_A: the gate times out, UNKNOWN

The two processes share nothing but the signal store of robot.flow.signals:
this script sets ROBOT_FLOW_SIGNALS to a fresh file of this run, as whoever
starts a run group should. Each rig has its own output directory under
./results/<rig>. The return code of each run encodes its verdict:
failed = rc & 0xF, unknown = (rc >> 4) & 0xF.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.abspath(os.path.join(HERE, '..', '..', 'src', 'robot', 'run.py'))
FLOW = os.path.join(HERE, '04_rendezvous.flow.json')
RESULTS = os.path.join(HERE, 'results')
SIGNALS = os.path.join(RESULTS, 'signals.json')
BENCH = os.path.join(RESULTS, 'bench.json')


def start(rig, peer):
    outdir = os.path.join(RESULTS, rig.lower())
    command = [sys.executable, RUNNER, '--parser', 'robot.flow',
               '--variable', f'RIG:{rig}', '--variable', f'PEER:{peer}',
               '--outputdir', outdir, '--console', 'dotted', FLOW]
    env = dict(os.environ, ROBOT_FLOW_SIGNALS=SIGNALS, FLOW_DEMO_BENCH=BENCH)
    return subprocess.Popen(command, env=env)


def main(alone=False):
    os.makedirs(RESULTS, exist_ok=True)
    for path in (SIGNALS, BENCH):
        if os.path.exists(path):
            os.remove(path)
    runs = {'RIG_A': start('RIG_A', 'RIG_B')}
    if not alone:
        runs['RIG_B'] = start('RIG_B', 'RIG_A')
    for rig, process in runs.items():
        rc = process.wait()
        failed, unknown = rc & 0xF, (rc >> 4) & 0xF
        print(f'{rig}: rc={rc} -> {failed} failed, {unknown} unknown '
              f'({os.path.join(RESULTS, rig.lower(), "log.html")})')
    print(f'signals of this run: {SIGNALS}')
    return max(process.returncode for process in runs.values())


if __name__ == '__main__':
    sys.exit(main(alone='--alone' in sys.argv))
