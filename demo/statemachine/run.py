#!/usr/bin/env python
"""Runs the StateMachine demo suites with the fork from this repository.

Usage:
    python run.py            # run everything, results in ./results
    python run.py <robot options and/or suite files>

Works also with embedded Python distributions that ignore PYTHONPATH.
With the fork installed as a package, a plain `robot .` works as well.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, '..', '..', 'src'))
sys.path.insert(0, SRC)

from robot import run_cli    # noqa: E402

args = sys.argv[1:] or [
    '--outputdir', os.path.join(HERE, 'results'),
    '--timeline', 'timeline.html',      # per-thread execution timeline
    '--segmentoutput', '30s',           # crash-safe output segments
    HERE,
]
run_cli(args)
