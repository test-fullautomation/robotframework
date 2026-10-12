#  Copyright 2008-2015 Nokia Networks
#  Copyright 2016-     Robot Framework Foundation
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Signals between flows: how the processes of a run group coordinate.

One flow *sets* a signal with a ``keyword`` node, another *waits* for it with
a ``gate``::

    { "id": "announce", "kind": "keyword", "keyword": "Set Signal", "args": ["cycle", "${n}"] }
    { "id": "checked",  "kind": "gate",    "keyword": "Signal Should Be",
      "args": ["ack", "==", "${n}"], "timeout": "30s" }

with ``"imports": { "libraries": ["robot.flow.signals"] }`` in both flows.

This is for *coordination* -- cycle numbers, acknowledgements, "I am
ready" -- not for the bench's own values, which stay in the services that
own them.

Where the signals live -- the *store* of the run, named by the
``ROBOT_FLOW_SIGNALS`` environment variable, which whoever starts the group
sets for every member (a fresh store per run means two runs can never read
each other's signals):

- a **path**: a JSON file shared by the processes of the run on one machine
  (the default; unset, a file in the temporary directory shared by every run
  on the machine that sets none);
- ``http://host:port/<run>``: the signal server of ``python -m robot.flow
  server`` on any machine of the bench, for flows on several machines, with
  nothing to install;
- ``redis://host:port/0?ns=<run>`` or ``mqtt://host:port/<run>``: a Redis
  server or an MQTT broker already on the bench (the ``redis`` and
  ``paho-mqtt`` packages are imported when such a store is used);
- ``ROBOT_FLOW_SIGNALS_BACKEND`` names a class (``package.module.Class`` or
  ``package.module:Class``) for any other system: a :class:`SignalStore`
  subclass created with the ``ROBOT_FLOW_SIGNALS`` value as its only argument
  (none when that is empty). ``utest/flow/test_signals.py`` has the contract
  such a class must meet.
- ``Library  robot.flow.signals.FlowSignals  store=...  backend=Class``
  chooses them in the flow or suite instead of the environment.

The same store is the run's control channel (pause, resume, stop, set,
snapshot, status): see :mod:`robot.flow.control`.

Values are numbers when they look like numbers (``Set Signal  cycle  3``
stores ``3``), strings otherwise; ``Signal Should Be`` compares numbers as
numbers.
"""

import importlib
import json
import os
import tempfile
import threading
import time

from robot.api import logger
# Private names: the module is a library, and its public functions are keywords.
from robot.utils import secs_to_timestr as _secs_to_timestr
from robot.version import get_version as _get_version


STORE_VARIABLE = 'ROBOT_FLOW_SIGNALS'
BACKEND_VARIABLE = 'ROBOT_FLOW_SIGNALS_BACKEND'
DEFAULT_STORE = os.path.join(tempfile.gettempdir(), 'robot_flow_signals.json')
_OPERATORS = ('==', '!=', '<', '<=', '>', '>=')
# URL schemes with a shipped store; anything else is a path or a custom class.
_SCHEMES = {'http': 'robot.flow.stores:HttpStore', 'https': 'robot.flow.stores:HttpStore',
            'redis': 'robot.flow.stores:RedisStore', 'rediss': 'robot.flow.stores:RedisStore',
            'mqtt': 'robot.flow.stores:MqttStore', 'mqtts': 'robot.flow.stores:MqttStore'}


class SignalStore:
    """Where the signals of a run live. Subclass it for another system.

    A store keeps *the last value of each name*: it is shared state, not a
    message stream, so a process that joins late reads what was set before
    it came. An entry is a dict ``{'value': ..., 'time': epoch seconds,
    'seq': int}``; ``seq`` is assigned by the store and strictly increases
    over every write to it by every process, which is what the control
    channel uses to tell "given since this process started" without trusting
    the clocks of two machines. :meth:`entry` builds one.

    Required: :meth:`get`, :meth:`set`, :meth:`read`. Optional:
    :meth:`wait` with ``supports_wait = True`` when the system can block
    until something changes (a long poll, a subscription), and
    :meth:`close`. ``where`` is the store's description for the log.
    """

    supports_wait = False
    where = ''

    def get(self, name):
        """The entry of ``name``, or ``None``."""
        raise NotImplementedError

    def set(self, name, value):
        """Store ``value`` (a JSON-safe scalar or dict) under ``name``."""
        raise NotImplementedError

    def read(self):
        """Every entry, as a dict name -> entry."""
        raise NotImplementedError

    def last_seq(self):
        """The newest sequence number in the store (0 when it is empty)."""
        return max((_seq(entry) for entry in self.read().values()), default=0)

    def wait(self, seq, timeout):
        """Return when an entry newer than ``seq`` exists, or after ``timeout``
        seconds. The default only sleeps; a store that can do better says
        ``supports_wait = True``."""
        time.sleep(timeout)

    def close(self):
        pass

    @staticmethod
    def entry(value, seq):
        return {'value': value, 'time': time.time(), 'seq': int(seq), 'pid': os.getpid()}


def _seq(entry):
    try:
        return int((entry or {}).get('seq') or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


class MemoryStore(SignalStore):
    """Signals in this process only: the reference store, for tests and for
    a run whose members are threads. ``name`` tells stores apart."""

    _stores = {}
    _lock = threading.Lock()

    def __init__(self, name='default'):
        with self._lock:
            self._data, self._seq = self._stores.setdefault(name, ({}, [0]))
        self.where = f'memory:{name}'

    def get(self, name):
        return self._data.get(name)

    def set(self, name, value):
        with self._lock:
            self._seq[0] += 1
            self._data[name] = self.entry(value, self._seq[0])

    def read(self):
        return dict(self._data)


class FileStore(SignalStore):
    """Signals in a JSON file, safe for several processes and threads.

    A write takes a lock file next to the store (created exclusively, so one
    writer at a time; one older than ``STALE_LOCK_S`` is left over from a
    killed process and is taken over), reads, updates and replaces the file
    whole. A reader never sees half a file. The sequence number is one more
    than the newest in the file, under the lock.
    """

    STALE_LOCK_S = 10.0
    LOCK_TIMEOUT_S = 10.0

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.where = self.path
        self._lock_path = self.path + '.lock'
        self._thread_lock = threading.Lock()

    def get(self, name):
        return self._read().get(name)

    def set(self, name, value):
        with self._thread_lock, self._locked():
            data = self._read()
            seq = max((_seq(entry) for entry in data.values()), default=0) + 1
            data[name] = self.entry(value, seq)
            self._replace(data)

    def read(self):
        return self._read()

    def _read(self):
        for attempt in range(50):
            try:
                with open(self.path, encoding='UTF-8') as file:
                    return json.load(file)
            except FileNotFoundError:
                return {}
            except (PermissionError, ValueError):
                # Being replaced (Windows) or, rarely, read mid-write elsewhere.
                time.sleep(0.01)
        raise RuntimeError(f"Cannot read signal store '{self.path}'.")

    def _replace(self, data):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp = f'{self.path}.{os.getpid()}.tmp'
        with open(temp, 'w', encoding='UTF-8') as file:
            json.dump(data, file)
        for attempt in range(50):
            try:
                os.replace(temp, self.path)
                return
            except PermissionError:
                time.sleep(0.01)
        os.remove(temp)
        raise RuntimeError(f"Cannot write signal store '{self.path}'.")

    def _locked(self):
        store = self

        class Lock:
            def __enter__(self):
                os.makedirs(os.path.dirname(store.path) or '.', exist_ok=True)
                deadline = time.time() + store.LOCK_TIMEOUT_S
                while True:
                    try:
                        os.close(os.open(store._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                        return self
                    except FileExistsError:
                        store._break_stale_lock()
                    except PermissionError:
                        pass
                    if time.time() > deadline:
                        raise RuntimeError(f"Signal store '{store.path}' stays locked "
                                           f"({store._lock_path}).")
                    time.sleep(0.005)

            def __exit__(self, *exc):
                try:
                    os.remove(store._lock_path)
                except OSError:
                    pass

        return Lock()

    def _break_stale_lock(self):
        # Taken over by a rename, which only one of several waiters wins: a
        # plain remove could take away the fresh lock another waiter had
        # just created after the same check.
        try:
            if time.time() - os.path.getmtime(self._lock_path) <= self.STALE_LOCK_S:
                return
            stale = f'{self._lock_path}.{os.getpid()}.stale'
            os.replace(self._lock_path, stale)
            os.remove(stale)
        except OSError:
            pass


class FlowSignals:
    """Signals between flows: ``Set Signal``, ``Get Signal`` and ``Signal Should Be``.

    See the module documentation for where they live. ``store`` is the file
    (or the backend's argument), ``backend`` a class as
    ``package.module.Class``; both default to the environment.
    """

    ROBOT_LIBRARY_SCOPE = 'GLOBAL'
    ROBOT_LIBRARY_VERSION = _get_version()

    def __init__(self, store=None, backend=None):
        self._store = open_store(store, backend)
        self._where = self._store.where

    def set_signal(self, name, value):
        """Set signal ``name`` to ``value``, for the other flows of the run.

        A value that looks like a number is stored as one.
        """
        value = _number(value)
        self._store.set(name, value)
        logger.info(f"Signal '{name}' = {value!r} ({self._where}).")

    def get_signal(self, name, default=None):
        """The value of signal ``name``; ``default`` when it has not been set,
        or a failure without one."""
        entry = self._store.get(name)
        if entry is None:
            if default is None:
                raise AssertionError(f"Signal '{name}' has not been set.")
            return _number(default)
        return entry['value']

    def signal_should_be(self, name, op, expected, tolerance=0):
        """Passes when signal ``name`` compares to ``expected`` with ``op``
        (``==`` ``!=`` ``<`` ``<=`` ``>`` ``>=``).

        Made to be polled by a gate: until the signal is set, or while the
        comparison fails, it fails saying what it read. Numbers compare as
        numbers, ``==`` and ``!=`` within ``tolerance``; other values only
        with ``==`` and ``!=``, as text.
        """
        if op not in _OPERATORS:
            raise ValueError(f"Unknown comparison '{op}'; use one of {', '.join(_OPERATORS)}.")
        entry = self._store.get(name)
        if entry is None:
            raise AssertionError(f"Signal '{name}' has not been set.")
        value, expected = entry['value'], _number(expected)
        if _is_number(value) and _is_number(expected):
            tolerance = float(tolerance)
            ok = {'==': abs(value - expected) <= tolerance,
                  '!=': abs(value - expected) > tolerance,
                  '<': value < expected, '<=': value <= expected,
                  '>': value > expected, '>=': value >= expected}[op]
        elif op in ('==', '!='):
            ok = (str(value) == str(expected)) == (op == '==')
        else:
            raise AssertionError(f"Signal '{name}' is {value!r}: '{op}' needs numbers.")
        if not ok:
            age = _secs_to_timestr(max(0.0, time.time() - entry.get('time', time.time())), compact=True)
            raise AssertionError(f"Signal '{name}' is {value!r} (set {age} ago), "
                                 f"expected {op} {expected!r}.")


def _number(value):
    if isinstance(value, str):
        text = value.strip()
        for convert in (int, float):
            try:
                return convert(text)
            except ValueError:
                pass
    return value


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def open_store(store=None, backend=None):
    """The :class:`SignalStore` for ``store``: a path, a URL with a known
    scheme, or whatever the class ``backend`` (``package.module:Class``; the
    ``ROBOT_FLOW_SIGNALS_BACKEND`` environment variable when not given)
    takes. Both default to the environment; no store at all is the file in
    the temporary directory."""
    store = store if store is not None else os.environ.get(STORE_VARIABLE, '')
    store = str(store).strip()
    backend = backend or os.environ.get(BACKEND_VARIABLE, '')
    if backend:
        cls = _import_class(backend)
        instance = cls(store) if store else cls()
        return _adapted(instance, f'{backend}({store!r})' if store else backend)
    scheme = _scheme(store)
    if scheme in _SCHEMES:
        return _import_class(_SCHEMES[scheme])(store)
    if scheme == 'file':
        from urllib.request import url2pathname
        store = url2pathname(store[len('file:'):].lstrip('/') if os.name == 'nt'
                             else store[len('file:'):])
    elif scheme:
        raise ValueError(f"Signal store '{store}': no store for '{scheme}://'. Use a "
                         f"path, http://, redis:// or mqtt://, or name a class in "
                         f"{BACKEND_VARIABLE}.")
    return FileStore(store or DEFAULT_STORE)


def _scheme(store):
    head, sep, _ = store.partition('://')
    # 'C:\\path' has no '://'; a one-letter scheme would be a drive anyway.
    return head.lower() if sep and len(head) > 1 and head.isalpha() else ''


def _adapted(instance, where):
    """A custom class with only ``get`` and ``set`` (the first contract) still
    works for signals; the control channel needs ``read`` for its requests."""
    if not getattr(instance, 'where', ''):
        try:
            instance.where = where
        except AttributeError:
            pass
    if not hasattr(instance, 'read'):
        legacy = getattr(instance, '_read', None)
        try:
            instance.read = legacy if legacy else (lambda: {})
        except AttributeError:
            pass
    for name in ('last_seq', 'wait', 'close'):
        if not hasattr(instance, name):
            try:
                setattr(instance, name, getattr(SignalStore, name).__get__(instance))
            except AttributeError:
                pass
    return instance


def _import_class(name):
    module, _, cls = name.replace(':', '.').rpartition('.')
    if not module:
        raise ValueError(f"Signal backend '{name}' is not 'package.module.Class'.")
    return getattr(importlib.import_module(module), cls)


# ``Library  robot.flow.signals``: the module is the library, backed by one
# FlowSignals made from the environment when a keyword first runs.

ROBOT_LIBRARY_SCOPE = 'GLOBAL'
ROBOT_LIBRARY_VERSION = _get_version()
_default = None
_default_lock = threading.Lock()


def _signals():
    global _default
    with _default_lock:
        if _default is None:
            _default = FlowSignals()
        return _default


def set_signal(name, value):
    """Set signal ``name`` to ``value``, for the other flows of the run.

    A value that looks like a number is stored as one. The store is the file
    in ``ROBOT_FLOW_SIGNALS`` (see the module documentation).
    """
    _signals().set_signal(name, value)


def get_signal(name, default=None):
    """The value of signal ``name``; ``default`` when it has not been set,
    or a failure without one."""
    return _signals().get_signal(name, default)


def signal_should_be(name, op, expected, tolerance=0):
    """Passes when signal ``name`` compares to ``expected`` with ``op``
    (``==`` ``!=`` ``<`` ``<=`` ``>`` ``>=``).

    Made to be polled by a gate: until the signal is set, or while the
    comparison fails, it fails saying what it read. Numbers compare as
    numbers, ``==`` and ``!=`` within ``tolerance``.
    """
    _signals().signal_should_be(name, op, expected, tolerance)
