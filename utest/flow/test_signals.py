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


class StoreContract:
    """What every signal store must do; a custom ROBOT_FLOW_SIGNALS_BACKEND
    class can be checked with it: subclass, make ``store()`` return a new
    instance of the same store."""

    def store(self):
        raise NotImplementedError

    def test_last_value_wins_and_a_late_joiner_sees_it(self):
        first = self.store()
        first.set('cycle', 1)
        first.set('cycle', 2)
        first.set('name', 'bench-1')
        late = self.store()
        assert_equal(late.get('cycle')['value'], 2)
        assert_equal(late.get('name')['value'], 'bench-1')
        assert_equal(late.get('never'), None)
        read = late.read()
        assert_equal(sorted(read), ['cycle', 'name'])
        assert_equal(read['cycle']['value'], 2)

    def test_entries_carry_time_and_increasing_sequence_numbers(self):
        a, b = self.store(), self.store()
        before = time.time() - 1
        a.set('x', 1)
        b.set('y', 2)
        a.set('x', 3)
        x, y = a.get('x'), b.get('y')
        assert_true(x['time'] >= before and y['time'] >= before)
        assert_true(y['seq'] > 0)
        assert_true(x['seq'] > y['seq'], 'the later write has the higher number')
        assert_equal(a.last_seq(), x['seq'])
        assert_equal(b.last_seq(), x['seq'])

    def test_values_keep_their_type(self):
        store = self.store()
        for value in (1, 2.5, 'text', True, {'state': 'running', 'iteration': 3}, None):
            store.set('v', value)
            assert_equal(self.store().get('v')['value'], value)

    def test_threads_writing_at_once_lose_nothing(self):
        import threading
        stores = [self.store() for _ in range(4)]

        def write(store, prefix):
            for i in range(20):
                store.set(f'{prefix}{i}', i)

        threads = [threading.Thread(target=write, args=(store, f't{n}_')) for n, store in enumerate(stores)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        read = self.store().read()
        assert_equal(len(read), 80)
        assert_equal(len({entry['seq'] for entry in read.values()}), 80, 'every write its own number')

    def test_wait_returns_on_a_change_or_after_the_timeout(self):
        import threading
        store = self.store()
        store.set('a', 1)
        seq = store.last_seq()
        started = time.time()
        store.wait(seq, 0.2)
        assert_true(time.time() - started >= 0.15, 'nothing changed: waited it out')
        if not store.supports_wait:
            return
        threading.Timer(0.1, lambda: self.store().set('a', 2)).start()
        started = time.time()
        store.wait(seq, 5)
        assert_true(time.time() - started < 2, 'returned on the change')

    def test_control_commands_through_the_store(self):
        from robot.flow.control import FlowControl
        control = FlowControl()
        control.POLL_S = 0.05
        older = self.store()
        older.set('flow.control', 'pause')          # given before this process started
        control.start_polling(self.store())
        try:
            time.sleep(0.3)
            assert_true(not control.paused, 'a command from before the start is ignored')
            self.store().set('flow.control', 'pause')
            for _ in range(60):
                if control.paused:
                    break
                time.sleep(0.05)
            assert_true(control.paused, 'the pause reached the poller')
            self.store().set('flow.control', 'resume')
            for _ in range(60):
                if not control.paused:
                    break
                time.sleep(0.05)
            assert_true(not control.paused)
        finally:
            control.reset()


class TestMemoryStoreContract(StoreContract, unittest.TestCase):

    def setUp(self):
        signals.MemoryStore._stores.pop('contract', None)

    def store(self):
        return signals.MemoryStore('contract')


class TestFileStoreContract(StoreContract, _Base):

    def store(self):
        return FileStore(self.path)


class TestHttpStoreContract(StoreContract, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from robot.flow.server import SignalServer
        cls.server = SignalServer('127.0.0.1', 0)
        cls.server.start()
        cls.addClassCleanup(cls.server.shutdown)

    def setUp(self):
        self.run = f'run{id(self)}'
        self.server.clear(self.run)

    def store(self):
        from robot.flow.stores import HttpStore
        return HttpStore(f'{self.server.url}/{self.run}')

    def test_open_store_by_url_and_errors(self):
        store = signals.open_store(f'{self.server.url}/{self.run}')
        assert_equal(type(store).__name__, 'HttpStore')
        assert_equal(store.where, f'{self.server.url}/{self.run}')
        assert_raises_with_msg(ValueError, f"Signal store '{self.server.url}' must be http://host:port/<run name>.",
                               signals.open_store, self.server.url)
        unreachable = signals.open_store('http://127.0.0.1:1/nowhere')
        try:
            unreachable.set('a', 1)
        except RuntimeError as err:
            assert_true('cannot be reached' in str(err), str(err))
        else:
            raise AssertionError('no error for a server that is not there')

    def test_the_server_keeps_runs_apart_and_lists_them(self):
        self.store().set('a', 1)
        from robot.flow.stores import HttpStore
        other = HttpStore(f'{self.server.url}/{self.run}-other')
        assert_equal(other.get('a'), None)
        other.set('b', 2)
        runs = self.server.runs()
        assert_equal(runs[self.run]['signals'], 1)
        assert_equal(runs[self.run + '-other']['signals'], 1)
        self.server.clear(self.run)
        assert_equal(self.store().read(), {})

    def test_the_server_keeps_its_state_in_a_file(self):
        import json
        from robot.flow.server import SignalServer
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'signals.json')
            first = SignalServer('127.0.0.1', 0, path)
            first.set('r', 'a', 1)
            first.set('r', 'b', 2)
            seq = first.get('r', 'b')['seq']
            first.shutdown()
            with open(path, encoding='UTF-8') as file:
                assert_equal(json.load(file)['runs']['r']['a']['value'], 1)
            second = SignalServer('127.0.0.1', 0, path)
            assert_equal(second.get('r', 'b')['value'], 2)
            second.set('r', 'c', 3)
            assert_true(second.get('r', 'c')['seq'] > seq, 'the counter goes on')
            second.shutdown()


try:
    import fakeredis
except ImportError:
    fakeredis = None


@unittest.skipIf(fakeredis is None, 'fakeredis is not installed')
class TestRedisStoreContract(StoreContract, unittest.TestCase):

    def setUp(self):
        self.server = fakeredis.FakeServer()
        self.run = f'run{id(self)}'

    def store(self):
        from robot.flow import stores
        store = stores.RedisStore.__new__(stores.RedisStore)
        store._redis = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        store._hash = f'robot.flow:{self.run}'
        store._counter = f'robot.flow:{self.run}:seq'
        store.where = f'fakeredis#{self.run}'
        return store

    def test_url_names_the_run(self):
        from robot.flow import stores
        try:
            store = stores.RedisStore('redis://127.0.0.1:6379/0?ns=run7')
        except ImportError:
            self.skipTest('redis is not installed')
        assert_equal((store._hash, store.where), ('robot.flow:run7', 'redis://127.0.0.1:6379/0#run7'))


@unittest.skipUnless(os.environ.get('ROBOT_FLOW_TEST_MQTT'), 'ROBOT_FLOW_TEST_MQTT names no broker')
class TestMqttStoreContract(StoreContract, unittest.TestCase):

    def setUp(self):
        self.url = os.environ['ROBOT_FLOW_TEST_MQTT'].rstrip('/') + f'/run{id(self)}'
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()

    def store(self):
        from robot.flow.stores import MqttStore
        store = MqttStore(self.url)
        self.stores.append(store)
        return store


class TestOpenStore(_Base):

    def test_paths_and_file_urls_are_files(self):
        for given in (self.path, 'file:///' + self.path.replace('\\', '/') if os.name == 'nt' else 'file://' + self.path):
            store = signals.open_store(given)
            assert_equal(type(store), FileStore)
            assert_equal(os.path.normcase(store.path), os.path.normcase(self.path))
        assert_equal(signals.open_store('').path, os.path.abspath(signals.DEFAULT_STORE))

    def test_unknown_scheme_is_an_error(self):
        assert_raises_with_msg(ValueError, "Signal store 'amqp://host/x': no store for 'amqp://'. "
                               "Use a path, http://, redis:// or mqtt://, or name a class in "
                               "ROBOT_FLOW_SIGNALS_BACKEND.", signals.open_store, 'amqp://host/x')

    def test_a_class_of_the_first_contract_is_completed(self):
        store = signals.open_store('bench-2', backend=__name__ + ':MemoryBackend')
        store.set('a', 1)
        assert_equal(store.get('a')['value'], 1)
        assert_equal(store.read(), {})            # get/set only: nothing to read whole
        assert_equal(store.last_seq(), 0)
        assert_equal(store.where, f"{__name__}:MemoryBackend('bench-2')")
        store.close()


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
