#!/usr/bin/env python
"""Runs the endurance demo with the fork from this repository.

Usage:
    python run.py                                 # 10 minutes, results in ./results
    python run.py --variable DURATION:8h          # the real thing
    python run.py --variable CRASH_AFTER:90s      # power loss after 90 s; then: python recover.py
    python run.py <any robot options>

Segments are sealed every 30 seconds (--segmentoutput 30s), so a crash loses
at most half a minute of log. Works also with embedded Python distributions
that ignore PYTHONPATH.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, '..', '..', 'src'))
sys.path.insert(0, SRC)

from robot import run_cli    # noqa: E402

run_cli(['--outputdir', os.path.join(HERE, 'results'),
         '--segmentoutput', '30s',
         '--timeline', 'timeline.html',
         *sys.argv[1:],
         os.path.join(HERE, 'endurance.robot')])
