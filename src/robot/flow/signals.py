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

Where the signals live:

- By default, a JSON file shared by the processes of the run: its path comes
  from the ``ROBOT_FLOW_SIGNALS`` environment variable, which whoever starts
  the group sets to a file of that run (a fresh file per run means two runs
  can never read each other's signals). Without it, a file in the temporary
  directory, shared by every run on the machine that does not set one.
- ``ROBOT_FLOW_SIGNALS_BACKEND`` names a class (``package.module.Class`` or
  ``package.module:Class``) to use instead, for processes on different
  machines: it is created with the ``ROBOT_FLOW_SIGNALS`` value as its only
  argument (none when that is empty) and needs two methods, ``get(name)``
  returning ``{'value': ..., 'time': epoch seconds}`` or ``None``, and
  ``set(name, value)``.
- ``Library  robot.flow.signals.FlowSignals  store=path  backend=Class``
  chooses them in the flow or suite instead of the environment.

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


class FileStore:
    """Signals in a JSON file, safe for several processes and threads.

    A write takes a lock file next to the store (created exclusively, so one
    writer at a time; one older than ``STALE_LOCK_S`` is left over from a
    killed process and is taken over), reads, updates and replaces the file
    whole. A reader never sees half a file.
    """

    STALE_LOCK_S = 10.0
    LOCK_TIMEOUT_S = 10.0

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self._lock_path = self.path + '.lock'
        self._thread_lock = threading.Lock()

    def get(self, name):
        return self._read().get(name)

    def set(self, name, value):
        with self._thread_lock, self._locked():
            data = self._read()
            data[name] = {'value': value, 'time': time.time(), 'pid': os.getpid()}
            self._replace(data)

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
        try:
            if time.time() - os.path.getmtime(self._lock_path) > self.STALE_LOCK_S:
                os.remove(self._lock_path)
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
        store = store if store is not None else os.environ.get(STORE_VARIABLE, '')
        backend = backend or os.environ.get(BACKEND_VARIABLE, '')
        if backend:
            cls = _import_class(backend)
            self._store = cls(store) if store else cls()
            self._where = f'{backend}({store!r})' if store else backend
        else:
            self._store = FileStore(store or DEFAULT_STORE)
            self._where = self._store.path

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
