import os
import subprocess
import sys
import tempfile
import time
import unittest

from robot.flow import signals
from robot.flow.signals import FileStore, FlowSignals
from robot.utils.asserts import assert_equal, assert_raises_with_msg, assert_true


class MemoryBackend:
    """A backend as ROBOT_FLOW_SIGNALS_BACKEND would name it."""

    stores = {}

    def __init__(self, name='default'):
        self.data = self.stores.setdefault(name, {})

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value):
        self.data[name] = {'value': value, 'time': time.time()}


class _Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'run', 'signals.json')

    def tearDown(self):
        self.dir.cleanup()


class TestFlowSignals(_Base):

    def test_set_and_get_across_instances(self):
        FlowSignals(self.path).set_signal('cycle', '3')
        assert_equal(FlowSignals(self.path).get_signal('cycle'), 3)

    def test_values_that_look_like_numbers_are_numbers(self):
        s = FlowSignals(self.path)
        for given, stored in [('3', 3), ('-1', -1), ('2.5', 2.5), ('ready', 'ready'), (7, 7)]:
            s.set_signal('x', given)
            assert_equal(s.get_signal('x'), stored)

    def test_missing_signal(self):
        s = FlowSignals(self.path)
        assert_raises_with_msg(AssertionError, "Signal 'ack' has not been set.",
                               s.get_signal, 'ack')
        assert_equal(s.get_signal('ack', '0'), 0)
        assert_raises_with_msg(AssertionError, "Signal 'ack' has not been set.",
                               s.signal_should_be, 'ack', '==', '1')

    def test_comparisons(self):
        s = FlowSignals(self.path)
        s.set_signal('n', '5')
        for op, expected in [('==', '5'), ('!=', '4'), ('<', '6'), ('<=', '5'), ('>', '4'), ('>=', '5')]:
            s.signal_should_be('n', op, expected)
        s.signal_should_be('n', '==', '5.04', tolerance='0.05')
        s.set_signal('state', 'ready')
        s.signal_should_be('state', '==', 'ready')
        s.signal_should_be('state', '!=', 'busy')

    def test_failure_says_what_was_read(self):
        s = FlowSignals(self.path)
        s.set_signal('ack', '2')
        try:
            s.signal_should_be('ack', '==', '3')
        except AssertionError as err:
            assert_true(str(err).startswith("Signal 'ack' is 2 (set "), err)
            assert_true(str(err).endswith("ago), expected == 3."), err)
        else:
            raise AssertionError('should have failed')

    def test_order_needs_numbers_and_ops_are_checked(self):
        s = FlowSignals(self.path)
        s.set_signal('state', 'ready')
        assert_raises_with_msg(AssertionError, "Signal 'state' is 'ready': '<' needs numbers.",
                               s.signal_should_be, 'state', '<', '3')
        assert_raises_with_msg(ValueError, "Unknown comparison '=~'; use one of ==, !=, <, <=, >, >=.",
                               s.signal_should_be, 'state', '=~', 'x')

    def test_store_from_environment(self):
        os.environ[signals.STORE_VARIABLE] = self.path
        try:
            FlowSignals().set_signal('a', '1')
        finally:
            del os.environ[signals.STORE_VARIABLE]
        assert_equal(FileStore(self.path).get('a')['value'], 1)

    def test_backend(self):
        name = __name__ + '.MemoryBackend'
        FlowSignals('bench-1', backend=name).set_signal('a', '1')
        assert_equal(FlowSignals('bench-1', backend=name.replace('.MemoryBackend', ':MemoryBackend'))
                     .get_signal('a'), 1)
        os.environ[signals.BACKEND_VARIABLE] = name
        try:
            assert_equal(FlowSignals().get_signal('b', 'none'), 'none')
        finally:
            del os.environ[signals.BACKEND_VARIABLE]


class TestFileStore(_Base):

    def test_stale_lock_is_taken_over(self):
        store = FileStore(self.path)
        store.set('a', 1)
        with open(store._lock_path, 'w', encoding='UTF-8'):
            pass
        old = time.time() - FileStore.STALE_LOCK_S - 1
        os.utime(store._lock_path, (old, old))
        store.set('a', 2)
        assert_equal(store.get('a')['value'], 2)
        assert_true(not os.path.exists(store._lock_path))

    def test_processes_writing_at_once_lose_nothing(self):
        script = ('import sys\n'
                  'from robot.flow.signals import FileStore\n'
                  'store = FileStore(sys.argv[1])\n'
                  'for i in range(25):\n'
                  '    store.set(sys.argv[2] + str(i), i)\n')
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        procs = [subprocess.Popen([sys.executable, '-c', script, self.path, f'p{n}_'], env=env)
                 for n in range(4)]
        for proc in procs:
            assert_equal(proc.wait(timeout=60), 0)
        with open(self.path, encoding='UTF-8') as file:
            import json
            data = json.load(file)
        assert_equal(len(data), 100)
        assert_equal(data['p3_24']['value'], 24)


class TestModuleKeywords(_Base):

    def test_module_library_uses_the_environment(self):
        os.environ[signals.STORE_VARIABLE] = self.path
        signals._default = None
        try:
            signals.set_signal('cycle', '-1')
            signals.signal_should_be('cycle', '<', '0')
            assert_equal(signals.get_signal('cycle'), -1)
        finally:
            del os.environ[signals.STORE_VARIABLE]
            signals._default = None


if __name__ == '__main__':
    unittest.main()
