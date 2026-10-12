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

"""cuongnht flow pause: the pause point of the body runner.

The body runner calls :data:`hook` before every step it is about to run.
Nothing is registered by default, so the cost is one attribute lookup. The
flow runner (:mod:`robot.flow.control`) registers its hook when a flow is
executed: the hook blocks while the run is paused and raises when it is
stopped.

Code that measures time on its own (the Watchdog library) registers a
listener here and is told when a pause starts and how long it lasted, so a
deliberate hold is not counted as a stall.
"""

import threading
import weakref


hook = None     # callable(context, step) or None

_listeners = weakref.WeakSet()
_lock = threading.Lock()


def add_listener(listener):
    """Register an object with ``on_pause()`` and ``on_resume(seconds)``.

    Only a weak reference is kept.
    """
    with _lock:
        _listeners.add(listener)


def notify_pause():
    for listener in _snapshot():
        listener.on_pause()


def notify_resume(seconds):
    for listener in _snapshot():
        listener.on_resume(seconds)


def _snapshot():
    with _lock:
        return list(_listeners)
