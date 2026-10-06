import json
import os
import tempfile
import threading
import time
import unittest

from robot.errors import DataError, ExecutionFailed
from robot.flow.control import (FlowControl, FlowRun, checkpoint_name, fingerprint,
                                json_safe, read_checkpoint)
from robot.running import pausepoint


class FakeContext:
    dry_run = False
    in_teardown = False


class FakeStore:

    def __init__(self):
        self.data = {}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value):
        self.data[name] = {'value': value, 'time': time.time()}


class TestClockAndPause(unittest.TestCase):

    def setUp(self):
        self.control = FlowControl()
        self.control.SLICE_S = 0.01

    def tearDown(self):
        self.control.reset()

    def test_clock_stands_still_while_paused(self):
        self.control.pause()
        before = self.control.clock()
        time.sleep(0.1)
        self.assertAlmostEqual(self.control.clock(), before, delta=0.05)
        seconds = self.control.resume()
        self.assertGreaterEqual(seconds, 0.08)
        # The clock is behind wall time by what was spent paused.
        self.assertAlmostEqual(time.time() - self.control.clock(), seconds, delta=0.05)

    def test_pause_and_resume_are_idempotent(self):
        self.assertTrue(self.control.pause())
        self.assertFalse(self.control.pause())
        time.sleep(0.05)
        self.assertGreater(self.control.resume(), 0)
        self.assertEqual(self.control.resume(), 0.0)

    def test_pause_point_blocks_until_resumed(self):
        self.control.pause()
        passed = threading.Event()

        def waiter():
            self.control.pause_point(FakeContext())
            passed.set()

        thread = threading.Thread(target=waiter)
        thread.start()
        self.assertFalse(passed.wait(0.1))
        self.control.resume()
        self.assertTrue(passed.wait(2))
        thread.join()

    def test_listeners_are_told_about_a_pause(self):
        calls = []

        class Listener:
            def on_pause(self):
                calls.append('pause')

            def on_resume(self, seconds):
                calls.append(('resume', seconds > 0))

        listener = Listener()
        pausepoint.add_listener(listener)
        self.control.pause()
        time.sleep(0.05)
        self.control.resume()
        self.assertEqual(calls, ['pause', ('resume', True)])

    def test_sleep_does_not_count_paused_time(self):
        self.control.pause()
        threading.Timer(0.15, self.control.resume).start()
        started = time.time()
        self.control.sleep(0.05)
        self.assertGreaterEqual(time.time() - started, 0.16)


class TestStop(unittest.TestCase):

    def setUp(self):
        self.control = FlowControl()
        self.control.SLICE_S = 0.01

    def tearDown(self):
        self.control.reset()

    def test_stop_raises_once_at_the_pause_point(self):
        self.control.stop('operator')
        with self.assertRaises(ExecutionFailed) as cm:
            self.control.pause_point(FakeContext())
        error = cm.exception
        self.assertEqual(str(error), 'Stopped by operator.')
        # Uncatchable, fatal, and no verdict about the product.
        self.assertTrue(error.syntax and error.exit and error.unknown)
        self.assertTrue(self.control.stopped)
        self.control.pause_point(FakeContext())      # the teardown is not disturbed

    def test_stop_is_not_raised_in_a_teardown(self):
        context = FakeContext()
        context.in_teardown = True
        self.control.stop()
        self.control.pause_point(context)
        self.assertTrue(self.control.stop_requested)

    def test_stop_ends_a_pause(self):
        self.control.pause()
        threading.Timer(0.05, self.control.stop).start()
        with self.assertRaises(ExecutionFailed):
            self.control.pause_point(FakeContext())
        self.assertFalse(self.control.paused)

    def test_worker_threads_do_not_raise_the_stop(self):
        self.control.stop()
        errors = []

        def worker():
            try:
                self.control.pause_point(FakeContext())
            except ExecutionFailed as err:
                errors.append(err)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(errors, [])

    def test_stop_message_names_position_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = FlowRun(None, 'Plan', os.path.join(tmp, 'plan.checkpoint.json'))
            run.phase_started('Cycle')
            run.loop_started('loop', max_loops=10)
            run.iteration_started('loop')
            run.iteration_started('loop')
            self.control.run = run
            self.control.stop('operator')
            with self.assertRaises(ExecutionFailed) as cm:
                self.control.pause_point(FakeContext())
            self.assertEqual(
                str(cm.exception),
                f"Stopped by operator at iteration 2 of loop 'loop'; "
                f"resumable from {run.path}."
            )
            data, problem = read_checkpoint(run.path)
            self.assertIsNone(problem)
            self.assertEqual(data['position']['iteration'], 1)
            self.assertEqual(data['remaining'], {'max_loops': 9})


class TestControlChannel(unittest.TestCase):

    def setUp(self):
        self.control = FlowControl()
        self.control.POLL_S = self.control.SLICE_S = 0.01
        self.store = FakeStore()

    def tearDown(self):
        self.control.reset()

    def _wait(self, condition):
        deadline = time.time() + 2
        while not condition():
            self.assertLess(time.time(), deadline, 'condition not met in time')
            time.sleep(0.005)

    def test_commands_from_the_store(self):
        self.control.start_polling(self.store)
        self.store.set('flow.control', 'pause')
        self._wait(lambda: self.control.paused)
        self.assertEqual(self.store.get(f'flow.state.{os.getpid()}')['value']['state'],
                         'paused')
        self.store.set('flow.control', 'resume')
        self._wait(lambda: not self.control.paused)
        self.store.set('flow.control', 'stop')
        self._wait(lambda: self.control.stop_requested)

    def test_a_stop_is_not_lost_when_another_command_follows_it(self):
        # Both commands arrive before the flow looks at the store again.
        self.store.set('flow.stop', 'stop')
        self.store.set('flow.control', 'stop')
        self.store.set('flow.control', 'resume')
        self.control.start_polling(self.store)
        self._wait(lambda: self.control.stop_requested)
        self.assertFalse(self.control.paused)

    def test_a_stop_for_another_rig_or_an_earlier_run_is_ignored(self):
        self.control.rig = 'RIG_A'
        self.store.set('flow.stop.RIG_B', 'stop')
        self.store.data['flow.stop'] = {'value': 'stop', 'time': time.time() - 60}
        self.control.start_polling(self.store)
        time.sleep(0.1)
        self.assertFalse(self.control.stop_requested)
        self.store.set('flow.stop.RIG_A', 'stop')
        self._wait(lambda: self.control.stop_requested)

    def test_commands_given_before_the_start_are_ignored(self):
        self.store.data['flow.control'] = {'value': 'stop', 'time': time.time() - 60}
        self.control.start_polling(self.store)
        time.sleep(0.1)
        self.assertFalse(self.control.stop_requested)

    def test_a_command_for_another_rig_is_ignored(self):
        self.control.rig = 'RIG_A'
        self.control.start_polling(self.store)
        self.store.set('flow.control.RIG_B', 'pause')
        time.sleep(0.1)
        self.assertFalse(self.control.paused)
        self.store.set('flow.control.RIG_A', 'pause')
        self._wait(lambda: self.control.paused)

    def test_step_mode_needs_a_way_to_resume(self):
        self.control._start_console = lambda: False
        with self.assertRaises(DataError):
            self.control.enable_step_mode()
        self.control.start_polling(self.store)
        self.control.enable_step_mode()
        self.assertTrue(self.control.step_mode)


class TestFlowRun(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.flow = os.path.join(self.tmp.name, 'plan.flow.json')
        with open(self.flow, 'w', encoding='UTF-8') as file:
            file.write('{"flow": {"name": "Plan"}}')
        self.path = os.path.join(self.tmp.name, 'out', 'plan.checkpoint.json')
        self.now = 1000.0

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, **config):
        return FlowRun(self.flow, 'Plan', self.path, outputdir='out',
                       clock=lambda: self.now, **config)

    def interrupted(self):
        """A run stopped in iteration 4 of 10, 100 s into a 600 s loop."""
        run = self.run_(every=1)
        values = {'${n}': 0, '${handle}': object()}
        self.assertIsNone(run.phase_started('Precheck'))
        run.phase_ended('Precheck', 'PASS')
        self.assertIsNone(run.phase_started('Cycle'))
        limit, deadline, variables, message = run.loop_started('loop', 10, 600.0)
        self.assertEqual((limit, deadline, variables, message), (10, 1600.0, {}, None))
        run.variable_names = ['${n}', '${handle}', '${unset}']
        run.read_variable = values.get
        for count in range(4):
            values['${n}'] = count
            run.iteration_started('loop')
            self.now += 25
        values['${n}'] = 99                 # changed inside the interrupted iteration
        self.assertEqual(run.stop(), self.path)
        return run

    def test_checkpoint_content(self):
        self.interrupted()
        data, problem = read_checkpoint(self.path)
        self.assertIsNone(problem)
        self.assertEqual(data['fingerprint'], fingerprint(self.flow))
        self.assertEqual(data['position'], {'phase': 'Cycle', 'loop': 'loop',
                                            'iteration': 3, 'done_loops': []})
        self.assertEqual(data['remaining'], {'max_loops': 7, 'max_seconds': 500.0})
        # As they were when the interrupted iteration began; no unserialisable value.
        self.assertEqual(data['variables'], {'${n}': 3})
        self.assertEqual([p['name'] for p in data['completed_phases']], ['Precheck'])
        self.assertEqual(data['completed_phases'][0]['status'], 'PASS')

    def test_restart_continues_with_what_is_left(self):
        self.interrupted()
        self.now = 5000.0
        run = self.run_()
        self.assertTrue(run.load('auto'))
        reason = run.phase_started('Precheck')
        self.assertIn('Completed with status PASS', reason)
        self.assertIn('(see out)', reason)
        self.assertIsNone(run.phase_started('Cycle'))
        limit, deadline, variables, message = run.loop_started('loop', 10, 600.0)
        self.assertEqual((limit, deadline, variables), (7, 5500.0, {'${n}': 3}))
        self.assertIn("Loop 'loop' continues after 3 completed iteration(s)", message)
        self.assertIn('7 iteration(s) and 8 minutes 20 seconds left', message)
        run.iteration_started('loop')
        self.assertEqual(run.iteration, 3)
        self.assertEqual(run.position_text(), "at iteration 4 of loop 'loop'")
        # The next phase starts from the beginning.
        run.phase_ended('Cycle', 'PASS')
        self.assertIsNone(run.phase_started('Report'))
        self.assertEqual(run.loop_started('other', 5)[:3], (5, float('inf'), {}))

    def test_finished_loops_and_exhausted_bounds_do_not_run_again(self):
        run = self.run_(every=1)
        run.phase_started('Cycle')
        run.loop_started('first', 2)
        run.iteration_started('first')
        run.iteration_started('first')
        run.loop_started('second', 3)
        for _ in range(4):                  # the fourth call: all three are complete
            run.iteration_started('second')
        run.stop()
        again = self.run_()
        again.load()
        again.phase_started('Cycle')
        limit, deadline, _, message = again.loop_started('first', 2)
        self.assertEqual((limit, deadline), (1, -1.0))
        self.assertIn('ran to its end in the earlier run', message)
        self.assertEqual(again.loop_started('second', 3)[:2], (1, -1.0))

    def test_finished_run_deletes_the_checkpoint_and_a_stopped_one_keeps_it(self):
        run = self.run_()
        run.phase_started('Cycle')
        self.assertTrue(os.path.exists(self.path))
        run.phase_ended('Cycle', 'FAIL')
        run.finish()
        self.assertFalse(os.path.exists(self.path))
        self.interrupted().finish()
        self.assertTrue(os.path.exists(self.path))

    def test_stopped_phase_is_not_completed(self):
        run = self.interrupted()
        run.phase_ended('Cycle', 'UNKNOWN')
        data, _ = read_checkpoint(self.path)
        self.assertEqual([p['name'] for p in data['completed_phases']], ['Precheck'])

    def test_changed_flow_file(self):
        self.interrupted()
        with open(self.flow, 'a', encoding='UTF-8') as file:
            file.write(' ')
        self.assertFalse(self.run_().load('auto'))      # warns and starts again
        with self.assertRaises(DataError) as cm:
            self.run_().load('always')
        self.assertIn('belongs to another version of the flow file', str(cm.exception))

    def test_resume_modes(self):
        self.assertFalse(self.run_().load('auto'))
        with self.assertRaises(DataError) as cm:
            self.run_().load('always')
        self.assertIn('No checkpoint at', str(cm.exception))
        self.interrupted()
        self.assertFalse(self.run_().load('never'))
        self.assertTrue(self.run_().load('always'))
        with self.assertRaises(DataError):
            self.run_().load('sometimes')

    def test_unreadable_checkpoint(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w', encoding='UTF-8') as file:
            file.write('{"checkpoint": 1, "flow"')
        data, problem = read_checkpoint(self.path)
        self.assertIsNone(data)
        self.assertIn('cannot be read', problem)
        with open(self.path, 'w', encoding='UTF-8') as file:
            json.dump({'checkpoint': 99}, file)
        self.assertIn('unknown format', read_checkpoint(self.path)[1])

    def test_writes_are_throttled_without_checkpoint_every(self):
        run = self.run_()
        run.phase_started('Cycle')
        run.loop_started('loop', 1000)
        run.iteration_started('loop')
        first = os.path.getmtime(self.path), read_checkpoint(self.path)[0]['saved']
        for _ in range(50):
            run.iteration_started('loop')
        data = read_checkpoint(self.path)[0]
        self.assertLess(data['position']['iteration'], 50)
        self.assertEqual((os.path.getmtime(self.path), data['saved']), first)


class TestCommandLine(unittest.TestCase):

    def test_control_writes_the_command_and_status_reads_it(self):
        import contextlib
        import io
        from robot.flow.__main__ import main
        from robot.flow.signals import FileStore
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, 'signals.json')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['control', store, 'status']), 0)
                self.assertEqual(main(['control', store, 'pause']), 0)
                self.assertEqual(main(['control', store, 'stop', '--rig', 'RIG_A']), 0)
                FileStore(store).set('flow.state.RIG_A', {'state': 'paused', 'phase': 'Cycle',
                                                          'loop': 'loop', 'iteration': 36})
                self.assertEqual(main(['control', store, 'status']), 0)
            self.assertEqual(FileStore(store).get('flow.control')['value'], 'pause')
            self.assertEqual(FileStore(store).get('flow.control.RIG_A')['value'], 'stop')
            self.assertEqual(FileStore(store).get('flow.stop.RIG_A')['value'], 'stop')
            self.assertIsNone(FileStore(store).get('flow.stop'))
            text = out.getvalue()
            self.assertIn('No flow has used', text)
            self.assertIn('pause -> all flows of', text)
            self.assertIn('stop -> rig RIG_A of', text)
            self.assertIn('last command for all flows: pause', text)
            self.assertIn('last command for RIG_A: stop', text)
            self.assertIn("RIG_A: paused, phase 'Cycle', loop 'loop' iteration 37", text)


class TestHelpers(unittest.TestCase):

    def test_checkpoint_name(self):
        self.assertEqual(checkpoint_name('plan'), 'plan.checkpoint.json')
        self.assertEqual(checkpoint_name('My Plan / v2'), 'My_Plan_v2.checkpoint.json')

    def test_json_safe(self):
        for value in (1, 1.5, 'x', True, None, [1, 'a'], {'a': [1]}):
            self.assertTrue(json_safe(value), value)
        for value in (object(), {1: 'int key'}, (1, 2), {'a'}):
            self.assertFalse(json_safe(value), value)


if __name__ == '__main__':
    unittest.main()
