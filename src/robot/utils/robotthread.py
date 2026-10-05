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

"""cuongnht add thread: which Python thread executes Robot Framework.

THREAD workers are told apart from the thread that runs the suite. That
thread is not necessarily the process main thread: ``robot.run`` can be
called from any thread, so the execution thread is registered when the
suite starts and everything else compares against it.
"""

import threading

_execution_thread = None


def set_execution_thread(thread=None):
    """Registers the thread running the suite (default: the current thread)."""
    global _execution_thread
    _execution_thread = thread or threading.current_thread()


def get_execution_thread():
    """Returns the registered execution thread, or the process main thread."""
    return _execution_thread or threading.main_thread()


def in_worker_thread():
    """Returns True when called from a THREAD worker instead of the execution thread."""
    return threading.current_thread() is not get_execution_thread()
