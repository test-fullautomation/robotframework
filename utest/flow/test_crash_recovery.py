"""A flow killed in the middle of its loop continues from its checkpoint.

The first run is a separate process killed without warning (as a crash or a
power loss would) once its checkpoint shows a few completed iterations; the
second run continues in this process. Every iteration leaves a mark in a file,
so the two runs together must cover the plan exactly once -- except the
iteration that was interrupted, which is done again.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from robot import run
from robot.api import ExecutionResult
from robot.flow.control import read_checkpoint

SRC = os.path.join(os.path.dirname(__file__), '..', '..', 'src')
CYCLES = 12

LIBRARY = """
import time

def tick(number, marks):
    with open(marks, 'a', encoding='UTF-8') as file:
        file.write(str(number) + chr(10))
    time.sleep(0.15)
    return int(number) + 1
"""

FLOW = {
    'flow': {'name': 'Crash', 'version': 1, 'checkpoint_every': 1},
    'imports': {'libraries': ['crash_lib.py']},
    'variables': {'MARKS': '', 'DONE': 1},
    'nodes': [
        {'id': 'start', 'kind': 'start'},
        {'id': 'cycle', 'kind': 'phase', 'role': 'test', 'name': 'Cycle'},
        {'id': 'loop', 'kind': 'loop', 'max_loops': CYCLES},
        {'id': 'tick', 'kind': 'keyword', 'keyword': 'Tick', 'args': ['${DONE}', '${MARKS}'],
         'assign': '${DONE}'},
        {'id': 'after', 'kind': 'phase', 'role': 'test', 'name': 'After'},
        {'id': 'note', 'kind': 'keyword', 'keyword': 'Log', 'args': ['after the loop']},
        {'id': 'end', 'kind': 'end'},
    ],
    'edges': [
        ['start', 'cycle'], ['cycle', 'loop'],
        {'from': 'loop', 'to': 'tick', 'label': 'body'}, {'from': 'tick', 'to': 'loop', 'label': 'next'},
        {'from': 'loop', 'to': 'after', 'label': 'done'}, ['after', 'note'], ['note', 'end'],
    ],
}


class TestCrashRecovery(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.flow = os.path.join(self.tmp.name, 'crash.flow.json')
        with open(self.flow, 'w', encoding='UTF-8') as file:
            json.dump(FLOW, file)
        with open(os.path.join(self.tmp.name, 'crash_lib.py'), 'w', encoding='UTF-8') as file:
            file.write(LIBRARY)
        self.marks = os.path.join(self.tmp.name, 'marks.txt')
        self.checkpoint = os.path.join(self.tmp.name, 'crash.checkpoint.json')

    def _marks(self):
        if not os.path.exists(self.marks):
            return []
        with open(self.marks, encoding='UTF-8') as file:
            return [int(line) for line in file if line.strip()]

    def _kill_after(self, iterations):
        env = dict(os.environ, PYTHONPATH=SRC + os.pathsep + os.environ.get('PYTHONPATH', ''))
        env.pop('PYTHONIOENCODING', None)
        command = [sys.executable, '-m', 'robot', '--parser', 'robot.flow', '--output', 'output.xml',
                   '--log', 'NONE', '--report', 'NONE', '--console', 'none',
                   '--outputdir', os.path.join(self.tmp.name, 'run1'),
                   '--variable', f'FLOW_CHECKPOINT:{self.checkpoint}',
                   '--variable', f'MARKS:{self.marks}', self.flow]
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                data, _ = read_checkpoint(self.checkpoint)
                position = (data or {}).get('position') or {}
                if position.get('loop') == 'loop' and position.get('iteration', 0) >= iterations:
                    break
                if process.poll() is not None:
                    self.fail(f'the first run ended on its own with rc {process.returncode}')
                time.sleep(0.02)
            else:
                self.fail('the checkpoint never showed the loop running')
        finally:
            process.kill()      # no warning, no teardown: a crash
            process.wait(30)
        return position['iteration']

    def test_killed_run_continues_from_its_checkpoint(self):
        completed = self._kill_after(4)
        data, problem = read_checkpoint(self.checkpoint)
        self.assertIsNone(problem, problem)
        self.assertEqual(data['position']['loop'], 'loop')
        self.assertGreaterEqual(data['position']['iteration'], 4)
        self.assertLess(data['position']['iteration'], CYCLES)
        before = self._marks()
        self.assertGreaterEqual(len(before), completed)

        out2 = os.path.join(self.tmp.name, 'run2')
        rc = run(self.flow, parser='robot.flow', outputdir=out2, output='output.xml',
                 log=None, report=None, stdout=io.StringIO(),
                 variable=[f'FLOW_CHECKPOINT:{self.checkpoint}', 'FLOW_RESUME:always',
                           f'MARKS:{self.marks}'])
        self.assertEqual(rc, 0, 'the continued run passes')
        marks = self._marks()
        self.assertEqual(sorted(set(marks)), list(range(1, CYCLES + 1)), 'every cycle ran')
        self.assertLessEqual(len(marks) - CYCLES, 1, 'at most the interrupted cycle ran twice')
        self.assertEqual(marks[:len(before)], before, 'the first run\'s marks stay')
        self.assertIn(marks[len(before)], (before[-1], before[-1] + 1),
                      'the second run went on where the first one stopped')
        suite = ExecutionResult(os.path.join(out2, 'output.xml')).suite
        cycle, after = suite.tests
        self.assertEqual((cycle.status, after.status), ('PASS', 'PASS'))
        notes = [m.message for m in cycle.body[1].messages]    # logged by Flow Loop
        self.assertTrue(any(m.startswith("Loop 'loop' continues after ") for m in notes), notes)
        self.assertFalse(os.path.exists(self.checkpoint), 'a run that reached its end removes it')


if __name__ == '__main__':
    unittest.main()
