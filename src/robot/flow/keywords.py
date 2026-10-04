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

"""Keywords the flow builder generates calls to.

This module is imported as a library into every suite built from a flow file.
It deliberately contains only what the runner itself needs; waits that know
about a bench (signals, services, devices) belong to application libraries
and are referenced from the flow by name.
"""

import time

from robot.api import logger
from robot.errors import UnknownAssertionError
from robot.libraries.BuiltIn import BuiltIn
from robot.running.runkwregister import RUN_KW_REGISTER
from robot.utils import secs_to_timestr, timestr_to_secs
from robot.version import get_version


ROBOT_LIBRARY_VERSION = get_version()
ROBOT_LIBRARY_SCOPE = 'GLOBAL'


def flow_gate(timeout, interval, on_timeout, name, *args):
    """Waits until the keyword ``name`` passes, polling every ``interval``.

    The keyword is run with the given ``args`` until it passes or ``timeout``
    (a Robot time string) has elapsed. When it does not pass in time, the
    gate fails with a message that carries the *last error* and *how long
    the wait lasted*.

    ``on_timeout`` decides the status of that failure:

    - ``unknown``: the environment was not ready and nothing was tested; the
      step and its test get the UNKNOWN status.
    - ``fail``: a peer that stays silent is a product failure; ordinary FAIL.

    Returns the passing keyword's return value.
    """
    max_seconds = timestr_to_secs(timeout)
    step = timestr_to_secs(interval)
    if on_timeout not in ('unknown', 'fail'):
        raise ValueError(f"'on_timeout' must be 'unknown' or 'fail', got "
                         f"'{on_timeout}'.")
    builtin = BuiltIn()
    start = time.time()
    attempts = 0
    while True:
        attempts += 1
        status, result = builtin.run_keyword_and_ignore_error(name, *args)
        elapsed = time.time() - start
        if status == 'PASS':
            logger.info(f"Gate '{name}' passed after {attempts} attempt(s) and "
                        f"{secs_to_timestr(elapsed)}.")
            return result
        remaining = max_seconds - elapsed
        if remaining <= 0:
            message = (f"Gate '{name}' did not pass within "
                       f"{secs_to_timestr(max_seconds)} ({attempts} attempts, "
                       f"waited {secs_to_timestr(elapsed)}). Last error: {result}")
            if on_timeout == 'fail':
                raise AssertionError(message)
            raise UnknownAssertionError(message)
        time.sleep(min(step, remaining))


# Registering makes `--dryrun` validate the gated keyword and its arguments,
# and keeps the keyword name and its arguments unresolved until they are run.
RUN_KW_REGISTER.register_run_keyword('robot.flow.keywords', 'flow_gate', 3,
                                     deprecation_warning=False, dry_run=True)
