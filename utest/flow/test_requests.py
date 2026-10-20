"""`set NAME=value` and `snapshot [name]` sent to a running flow through its
signal store, and a restart from the snapshot where the command line wins over
the restored variable."""
import io
import json
import os
import tempfile
import time
import unittest

from robot import run
from robot.api import ExecutionResult
from robot.flow.control import FlowControl, SET, SNAPSHOT, read_checkpoint

CYCLES = 8

# The library asks for the set and the snapshot from inside the flow, at the
# third cycle, the way an operator would from outside: by writing to the store.
LIBRARY = """
from robot.flow.signals import FlowSignals

def tick(number, mode, store):
    number = int(number) + 1
    print('*INFO* tick %d in mode %s' % (number, mode))
    if number == 3:
        backend = FlowSignals(store=store)._store
        backend.set('flow.set.*.MODE', 'changed')
        backend.set('flow.snapshot', 'third cycle')
    return number
"""

FLOW = {
    'flow': {'name': 'Requests', 'version': 1, 'checkpoint_every': 1},
    'imports': {'libraries': ['requests_lib.py']},
    'variables': {'MODE': 'normal', 'DONE': 0, 'STORE': ''},
    'nodes': [
        {'id': 'start', 'kind': 'start'},
        {'id': 'cycle', 'kind': 'phase', 'role': 'test', 'name': 'Cycle'},
        {'id': 'loop', 'kind': 'loop', 'max_loops': CYCLES, 'every': '0.3s'},
        {'id': 'tick', 'kind': 'keyword', 'keyword': 'Tick', 'args': ['${DONE}', '${MODE}', '${STORE}'],
         'assign': '${DONE}'},
        {'id': 'end', 'kind': 'end'},
    ],
    'edges': [['start', 'cycle'], ['cycle', 'loop'],
              {'from': 'loop', 'to': 'tick', 'label': 'body'}, {'from': 'tick', 'to': 'loop', 'label': 'next'},
              {'from': 'loop', 'to': 'end', 'label': 'done'}],
}


def messages(suite):
    found = []

    def walk(item):
        for child in getattr(item, 'body', []):
            if child.type == 'MESSAGE':
                found.append(child.message)
            else:
                walk(child)
    for test in suite.tests:
        walk(test)
    return found


class TestRequestsFromTheStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.flow = os.path.join(self.tmp.name, 'requests.flow.json')
        with open(self.flow, 'w', encoding='UTF-8') as file:
            json.dump(FLOW, file)
        with open(os.path.join(self.tmp.name, 'requests_lib.py'), 'w', encoding='UTF-8') as file:
            file.write(LIBRARY)
        self.store = os.path.join(self.tmp.name, 'signals.json')
        self.environ = dict(os.environ)
        os.environ['ROBOT_FLOW_SIGNALS'] = self.store
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.environ)))

    def _run(self, outdir, *variables):
        rc = run(self.flow, parser='robot.flow', outputdir=os.path.join(self.tmp.name, outdir),
                 output='output.xml', log=None, report=None, stdout=io.StringIO(),
                 variable=[f'STORE:{self.store}', *variables])
        return rc, ExecutionResult(os.path.join(self.tmp.name, outdir, 'output.xml')).suite

    def test_set_and_snapshot_reach_the_running_flow(self):
        rc, suite = self._run('run1')
        self.assertEqual(rc, 0)
        notes = messages(suite)
        ticks = [m for m in notes if m.startswith('tick ')]
        self.assertEqual(len(ticks), CYCLES)
        self.assertIn("Variable ${MODE} set to 'changed' by operator (suite scope).", notes)
        changed = [m for m in ticks if m.endswith('mode changed')]
        self.assertTrue(changed, 'later cycles saw the new value')
        self.assertTrue(all(m.endswith('mode normal') for m in ticks[:3]), ticks[:3])
        snapshots = [f for f in os.listdir(os.path.join(self.tmp.name, 'run1')) if '.snapshot-' in f]
        self.assertEqual(snapshots, ['requests.snapshot-third_cycle.json'])
        self.assertTrue(any(m.startswith("Snapshot 'requests.snapshot-third_cycle.json' written at iteration")
                            for m in notes), notes)
        data, problem = read_checkpoint(os.path.join(self.tmp.name, 'run1', snapshots[0]))
        self.assertIsNone(problem)
        self.assertEqual(data['snapshot']['name'], 'third_cycle')
        self.assertEqual(data['position']['loop'], 'loop')
        self.assertGreaterEqual(data['position']['iteration'], 2)
        self.assertLess(data['position']['iteration'], CYCLES)
        self.assertIn('${MODE}', data['snapshot']['scope'])
        self.assertNotIn('${CURDIR}', data['snapshot']['scope'])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'run1', 'requests.checkpoint.json')),
                         'the run finished: its checkpoint is gone, the snapshot stays')
        self.snapshot = os.path.join(self.tmp.name, 'run1', snapshots[0])
        self._restart_from_snapshot(data['position']['iteration'])

    def _restart_from_snapshot(self, completed):
        # The snapshot saved ${DONE} (assigned in the phase); the command line
        # gives it a value of its own, which wins.
        rc, suite = self._run('run2', f'FLOW_CHECKPOINT:{self.snapshot}', 'FLOW_RESUME:always',
                              'DONE:100')
        self.assertEqual(rc, 0)
        notes = messages(suite)
        self.assertTrue(any(m.startswith("Continuing from snapshot 'third_cycle'") for m in notes), notes)
        self.assertTrue(any(m.startswith(f"Loop 'loop' continues after {completed} completed") for m in notes))
        self.assertIn('Kept from the command line instead of the checkpoint: ${DONE}.', notes)
        ticks = [m for m in notes if m.startswith('tick ')]
        self.assertEqual(len(ticks), CYCLES - completed)
        self.assertTrue(ticks[0].startswith('tick 101 '), ticks[0])
        self.assertTrue(os.path.exists(self.snapshot), 'the snapshot is kept')
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'run1', 'requests.checkpoint.json')))


class TestRequestsThroughTheServer(TestRequestsFromTheStore):
    """The same run with the signal server as the store: the flow's own
    requests, the poller and the restart all go over HTTP."""

    def setUp(self):
        super().setUp()
        from robot.flow.server import SignalServer
        self.server = SignalServer('127.0.0.1', 0)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        self.store = f'{self.server.url}/requests-{id(self)}'
        os.environ['ROBOT_FLOW_SIGNALS'] = self.store

    def test_the_flow_published_its_state_to_the_server(self):
        rc, suite = self._run('run1')
        self.assertEqual(rc, 0)
        states = {k: v for k, v in self.server.read(f'requests-{id(self)}').items() if k.startswith('flow.state.')}
        self.assertEqual(len(states), 1)
        self.assertEqual(list(states.values())[0]['value']['state'], 'finished')


class FakeStore:

    def __init__(self):
        self.data = {}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value):
        self.data[name] = {'value': value, 'time': time.time()}

    def _read(self):
        return dict(self.data)


class TestRequestPlumbing(unittest.TestCase):

    def setUp(self):
        self.control = FlowControl()
        self.control.POLL_S = 0.02
        self.store = FakeStore()

    def tearDown(self):
        self.control.reset()

    def _wait(self, condition):
        for _ in range(200):
            if condition():
                return
            time.sleep(0.01)
        self.fail('timeout')

    def test_requests_are_queued_once_for_this_process_and_rig(self):
        self.store.data['flow.set.*.OLD'] = {'value': '1', 'time': time.time() - 60}   # before the start
        self.control.rig = 'RIG_A'
        self.control.start_polling(self.store)
        self.store.set('flow.set.*.A', '1')
        self.store.set('flow.set.RIG_A.B', '2')
        self.store.set('flow.set.RIG_B.C', '3')
        self.store.set('flow.snapshot.RIG_A', 'mine')
        self.store.set('flow.snapshot.RIG_B', 'theirs')
        self._wait(lambda: len(self.control._pending) >= 3)
        time.sleep(0.1)
        self.assertEqual(sorted(self.control._pending),
                         [(SET, 'A', '1'), (SET, 'B', '2'), (SNAPSHOT, 'mine')])

    def test_requests_are_applied_on_the_flow_thread_at_the_pause_point(self):
        applied = []
        self.control._set_variable = lambda name, value: applied.append((name, value))
        self.control._take_snapshot = lambda name: applied.append(('snapshot', name))
        self.control.request(SET, 'X', '5')
        self.control.request(SNAPSHOT, 'now')
        self.control.pause_point()
        self.assertEqual(applied, [('X', '5'), ('snapshot', 'now')])
        self.assertEqual(self.control._pending, [])


if __name__ == '__main__':
    unittest.main()
