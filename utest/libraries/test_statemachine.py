import json
import os
import re
import tempfile
import time
import unittest

import robot.libraries.StateMachine as sm_module
from robot.errors import TimeoutError as RobotTimeoutError
from robot.libraries.StateMachine import StateMachine


class FakeBuiltIn:
    """Stands in for BuiltIn: variables + keyword registry, no RF context."""
    variables = {}
    keywords = {}

    def get_variable_value(self, name, default=None):
        return self.variables.get(name, default)

    def set_test_variable(self, name, value):
        self.variables[name] = value

    def evaluate(self, expression):
        ns = {name.strip('${}'): value for name, value in self.variables.items()}
        return eval(re.sub(r'\$(\w+)', r'\1', expression), {}, ns)

    def run_keyword_and_ignore_error(self, name):
        try:
            self.keywords[name]()
        except RobotTimeoutError:
            # Like the real BuiltIn keyword: timeouts are never ignored.
            # Swallowing the asynchronously raised timeout would leave the
            # timeout runner waiting for it forever.
            raise
        except Exception as err:
            return 'FAIL', str(err)
        return 'PASS', None


class FakeLogger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def info(self, msg, also_console=False):
        self.infos.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def debug(self, msg):
        pass


class StateMachineTestCase(unittest.TestCase):

    def setUp(self):
        FakeBuiltIn.variables = {}
        FakeBuiltIn.keywords = {}
        self._real_builtin = sm_module.BuiltIn
        self._real_logger = sm_module.logger
        sm_module.BuiltIn = FakeBuiltIn
        sm_module.logger = self.logger = FakeLogger()
        self.sm = StateMachine()

    def tearDown(self):
        sm_module.BuiltIn = self._real_builtin
        sm_module.logger = self._real_logger

    def define_simple_machine(self, condition='$N >= 3'):
        FakeBuiltIn.variables['${N}'] = 0

        def work():
            FakeBuiltIn.variables['${N}'] += 1

        FakeBuiltIn.keywords['Work'] = work
        self.sm.define_state('INIT')
        self.sm.define_state('WORK', during='Work')
        self.sm.define_state('DONE', final=True)
        self.sm.define_transition('INIT', 'WORK')
        self.sm.define_transition('WORK', 'DONE', condition=condition)


class TestDefinitionAndValidation(StateMachineTestCase):

    def test_duplicate_state_rejected(self):
        self.sm.define_state('A', final=True)
        with self.assertRaisesRegex(RuntimeError, "already defined"):
            self.sm.define_state('A')

    def test_unknown_initial_state(self):
        self.sm.define_state('A', final=True)
        with self.assertRaisesRegex(RuntimeError, "not defined"):
            self.sm.run_state_machine('NOPE')

    def test_transition_with_unknown_state(self):
        self.sm.define_state('A', final=True)
        self.sm.define_transition('A', 'MISSING')
        with self.assertRaisesRegex(RuntimeError, "undefined *state 'MISSING'"):
            self.sm.run_state_machine('A')

    def test_unknown_on_error_state(self):
        self.sm.define_state('A', on_error='MISSING', final=True)
        with self.assertRaisesRegex(RuntimeError, "undefined on_error"):
            self.sm.run_state_machine('A')

    def test_dead_end_state_rejected(self):
        self.sm.define_state('A')    # not final, no outgoing transitions
        with self.assertRaisesRegex(RuntimeError, "no outgoing transitions"):
            self.sm.run_state_machine('A')


class TestEngine(StateMachineTestCase):

    def test_runs_to_final_state(self):
        self.define_simple_machine()
        self.sm.run_state_machine('INIT', poll_interval='0.01 s')
        self.assertEqual(self.sm.get_current_state(), 'DONE')
        self.assertEqual(FakeBuiltIn.variables['${N}'], 3)

    def test_transitions_evaluated_in_definition_order(self):
        FakeBuiltIn.variables['${N}'] = 10
        self.sm.define_state('A')
        self.sm.define_state('FIRST', final=True)
        self.sm.define_state('SECOND', final=True)
        self.sm.define_transition('A', 'FIRST', condition='$N > 5')
        self.sm.define_transition('A', 'SECOND', condition='$N > 1')
        self.sm.run_state_machine('A', poll_interval='0.01 s')
        self.assertEqual(self.sm.get_current_state(), 'FIRST')

    def test_failure_routes_to_on_error_state(self):
        def boom():
            raise AssertionError('simulated fault')

        FakeBuiltIn.keywords['Boom'] = boom
        self.sm.define_state('A', enter='Boom', on_error='FAULT')
        self.sm.define_state('FAULT', final=True)
        self.sm.define_state('B', final=True)
        self.sm.define_transition('A', 'B')
        self.sm.run_state_machine('A', poll_interval='0.01 s')
        self.assertEqual(self.sm.get_current_state(), 'FAULT')
        self.assertTrue(any('simulated fault' in w for w in self.logger.warnings))

    def test_failure_without_on_error_fails(self):
        def boom():
            raise AssertionError('simulated fault')

        FakeBuiltIn.keywords['Boom'] = boom
        self.sm.define_state('A', enter='Boom')
        self.sm.define_state('B', final=True)
        self.sm.define_transition('A', 'B')
        with self.assertRaisesRegex(AssertionError, 'simulated fault'):
            self.sm.run_state_machine('A', poll_interval='0.01 s')

    def test_max_duration_exceeded(self):
        self.define_simple_machine(condition='$N >= 999999')
        with self.assertRaisesRegex(AssertionError, 'max_duration'):
            self.sm.run_state_machine('INIT', max_duration='0.1 s',
                                      poll_interval='0.02 s')

    def test_stuck_keyword_interrupted_by_state_timeout(self):
        def stuck():
            # Python-level loop: interruptible by RF's timeout machinery,
            # like RF's own Sleep keyword that sleeps in small chunks.
            end = time.time() + 30
            while time.time() < end:
                time.sleep(0.01)

        FakeBuiltIn.keywords['Stuck'] = stuck
        self.sm.define_state('A', enter='Stuck', timeout='0.2 s',
                             on_error='FAULT')
        self.sm.define_state('FAULT', final=True)
        self.sm.define_state('B', final=True)
        self.sm.define_transition('A', 'B')
        start = time.time()
        self.sm.run_state_machine('A', poll_interval='0.01 s')
        self.assertLess(time.time() - start, 10)    # nowhere near 30 s
        self.assertEqual(self.sm.get_current_state(), 'FAULT')
        self.assertTrue(any('timeout' in w.lower() for w in self.logger.warnings))


class TestCheckpoint(StateMachineTestCase):

    def setUp(self):
        super().setUp()
        fd, self.checkpoint = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        os.remove(self.checkpoint)

    def tearDown(self):
        if os.path.exists(self.checkpoint):
            os.remove(self.checkpoint)
        super().tearDown()

    def test_checkpoint_saved_and_removed_on_success(self):
        self.define_simple_machine()
        self.sm.checkpoint_variable('${N}')
        self.sm.run_state_machine('INIT', checkpoint=self.checkpoint,
                                  poll_interval='0.01 s')
        self.assertFalse(os.path.exists(self.checkpoint))

    def test_resume_restores_state_and_variables(self):
        self.define_simple_machine(condition='$N >= 999999')
        self.sm.checkpoint_variable('${N}')
        with self.assertRaisesRegex(AssertionError, 'max_duration'):
            self.sm.run_state_machine('INIT', max_duration='0.1 s',
                                      checkpoint=self.checkpoint,
                                      poll_interval='0.02 s')
        self.assertTrue(os.path.exists(self.checkpoint))
        with open(self.checkpoint, encoding='UTF-8') as f:
            data = json.load(f)
        self.assertEqual(data['state'], 'WORK')
        self.assertIn('machine', data)
        progress = data['variables']['${N}']
        self.assertGreater(progress, 0)
        # New engine instance resumes and completes with a reachable guard.
        FakeBuiltIn.variables['${N}'] = 0    # will be restored from checkpoint
        sm2 = StateMachine()
        FakeBuiltIn.keywords['Work'] = FakeBuiltIn.keywords['Work']
        sm2.define_state('INIT')
        sm2.define_state('WORK', during='Work')
        sm2.define_state('DONE', final=True)
        sm2.define_transition('INIT', 'WORK')
        sm2.define_transition('WORK', 'DONE',
                              condition=f'$N >= {progress + 2}')
        sm2.checkpoint_variable('${N}')
        sm2.run_state_machine('INIT', checkpoint=self.checkpoint,
                              poll_interval='0.01 s')
        self.assertEqual(sm2.get_current_state(), 'DONE')
        self.assertGreaterEqual(FakeBuiltIn.variables['${N}'], progress + 2)

    def test_resume_true_requires_checkpoint(self):
        self.define_simple_machine()
        with self.assertRaisesRegex(RuntimeError, 'does not exist'):
            self.sm.run_state_machine('INIT', checkpoint=self.checkpoint,
                                      resume=True)

    def test_changed_machine_definition_warns_on_resume(self):
        self.define_simple_machine(condition='$N >= 999999')
        with self.assertRaisesRegex(AssertionError, 'max_duration'):
            self.sm.run_state_machine('INIT', max_duration='0.1 s',
                                      checkpoint=self.checkpoint,
                                      poll_interval='0.02 s')
        sm2 = StateMachine()
        sm2.define_state('INIT')
        sm2.define_state('WORK', during='Work')
        sm2.define_state('DONE', final=True)
        sm2.define_state('EXTRA', final=True)    # definition differs
        sm2.define_transition('INIT', 'WORK')
        sm2.define_transition('WORK', 'DONE', condition='$N >= 1')
        sm2.run_state_machine('INIT', checkpoint=self.checkpoint,
                              poll_interval='0.01 s')
        self.assertTrue(any('different state machine definition' in w
                            for w in self.logger.warnings))

    def test_unserializable_checkpoint_variable_fails_fast(self):
        FakeBuiltIn.variables['${OBJ}'] = object()
        with self.assertRaisesRegex(RuntimeError, 'not JSON serializable'):
            self.sm.checkpoint_variable('${OBJ}')

    def test_unserializable_value_at_save_time_warns_not_crashes(self):
        self.define_simple_machine()
        self.sm.checkpoint_variable('${N}')
        self.sm._checkpoint_variables.append('${LATE}')
        FakeBuiltIn.variables['${LATE}'] = object()
        self.sm.run_state_machine('INIT', checkpoint=self.checkpoint,
                                  poll_interval='0.01 s')
        self.assertEqual(self.sm.get_current_state(), 'DONE')
        self.assertTrue(any('not JSON serializable' in w
                            for w in self.logger.warnings))


if __name__ == '__main__':
    unittest.main()
