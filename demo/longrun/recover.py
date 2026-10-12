#!/usr/bin/env python
"""Rebuilds log.html and report.html after a crashed endurance run.

    python recover.py [results directory, default ./results]

1. every ``<base>_part_NNN.xml`` chain (main output and each thread's output)
   is merged into its live file, which is repaired if it was cut mid-write
2. per-thread outputs are grafted into output.xml
3. rebot writes log.html and report.html

The same three steps happen automatically at the end of a run that finishes
normally; this script is for the run that did not.
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, '..', '..', 'src'))
sys.path.insert(0, SRC)

from robot import rebot_cli                              # noqa: E402
from robot.output.segmentmerger import merge_segments    # noqa: E402
from robot.output.threadmerger import merge_thread_outputs   # noqa: E402


class Console:
    def info(self, msg): print(msg)
    def warn(self, msg): print(f'[ WARN ] {msg}')
    def error(self, msg): print(f'[ ERROR ] {msg}')


def main(results):
    output = os.path.join(results, 'output.xml')
    console = Console()
    bases = {re.sub(r'_part_\d+\.xml$', '.xml', path)
             for path in glob.glob(os.path.join(results, '*_part_*.xml'))}
    # Thread files first, so the main file is merged last and is complete
    # when the threads are grafted into it.
    for base in sorted(bases, key=lambda b: b == output):
        merged = merge_segments(base, logger=console)
        print(f'{os.path.basename(base)}: {merged} segment(s) merged')
    threads = merge_thread_outputs(output, logger=console)
    print(f'{threads} thread output(s) grafted')
    # Log and report only; the merged output.xml stays as it is.
    rebot_cli(['--outputdir', results, output], exit=False)


if __name__ == '__main__':
    main(os.path.abspath(sys.argv[1]) if len(sys.argv) > 1
         else os.path.join(HERE, 'results'))
