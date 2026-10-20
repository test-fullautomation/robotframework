#!/usr/bin/env python
"""Runs the flow demos with the fork from this repository.

Usage:
    python run.py            # every flow in this folder, results in ./results
    python run.py <robot options and/or flow files>

Works also with embedded Python distributions that ignore PYTHONPATH.
With the fork installed as a package, `robot --parser robot.flow .` works too.

Run alone, 04_rendezvous waits for a peer that never comes and ends UNKNOWN -
that is the point. Use run_two_rigs.py to run it as a pair.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, '..', '..', 'src'))
sys.path.insert(0, SRC)
RESULTS = os.path.join(HERE, 'results')
os.makedirs(RESULTS, exist_ok=True)
# A fresh signal store and bench per run: robot.flow.signals reads
# ROBOT_FLOW_SIGNALS, the simulated bench FLOW_DEMO_BENCH.
for variable, name in (('ROBOT_FLOW_SIGNALS', 'signals.json'),
                       ('FLOW_DEMO_BENCH', 'bench.json')):
    path = os.environ.setdefault(variable, os.path.join(RESULTS, name))
    if os.path.exists(path):
        os.remove(path)

from robot import run_cli    # noqa: E402

args = sys.argv[1:]
if not any(os.path.exists(arg) for arg in args):
    # No flow file or folder given, only options (or nothing): run every
    # flow in this folder.
    args = ['--timeline', 'timeline.html'] + args + [HERE]
if '--outputdir' not in args and '-d' not in args:
    args = ['--outputdir', RESULTS] + args     # one flow too: results in ./results
run_cli(['--parser', 'robot.flow'] + args)
