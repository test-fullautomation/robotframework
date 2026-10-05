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

``Flow Gate`` is the wait of a ``gate`` node. ``Flow Phase``, ``Flow Loop``
and ``Flow Iteration`` are bookkeeping the builder adds for the checkpoint
(see :mod:`robot.flow.control`). ``Flow Pause``, ``Flow Resume`` and
``Flow Stop`` let a test or a supervisor hold or end the flow it runs in.
"""

import os

from robot.api import logger, SkipExecution
from robot.errors import UnknownAssertionError
from robot.libraries.BuiltIn import BuiltIn
from robot.running.context import EXECUTION_CONTEXTS
from robot.running.runkwregister import RUN_KW_REGISTER
from robot.utils import is_truthy, secs_to_timestr, timestr_to_secs
from robot.version import get_version

from . import control as _control
from .control import CONTROL as _CONTROL


__all__ = ['flow_gate', 'flow_phase', 'flow_loop', 'flow_iteration',
           'flow_pause', 'flow_resume', 'flow_stop']

ROBOT_LIBRARY_VERSION = get_version()
ROBOT_LIBRARY_SCOPE = 'GLOBAL'

RESUME_VARIABLE = '${FLOW_RESUME}'
CHECKPOINT_VARIABLE = '${FLOW_CHECKPOINT}'
CHECKPOINT_EVERY_VARIABLE = '${FLOW_CHECKPOINT_EVERY}'
STEP_VARIABLE = '${FLOW_STEP}'
RIG_VARIABLE = '${FLOW_RIG}'


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

    The time a flow spends paused does not count: a gate paused for an hour
    still has all of its timeout afterwards.

    Returns the passing keyword's return value.
    """
    max_seconds = timestr_to_secs(timeout)
    step = timestr_to_secs(interval)
    if on_timeout not in ('unknown', 'fail'):
        raise ValueError(f"'on_timeout' must be 'unknown' or 'fail', got "
                         f"'{on_timeout}'.")
    builtin = BuiltIn()
    start = _CONTROL.clock()
    attempts = 0
    while True:
        attempts += 1
        status, result = builtin.run_keyword_and_ignore_error(name, *args)
        elapsed = _CONTROL.clock() - start
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
        _CONTROL.sleep(min(step, remaining))


# Registering makes `--dryrun` validate the gated keyword and its arguments,
# and keeps the keyword name and its arguments unresolved until they are run.
RUN_KW_REGISTER.register_run_keyword('robot.flow.keywords', 'flow_gate', 3,
                                     deprecation_warning=False, dry_run=True)


# ------------------------------------------------------------- bookkeeping

def flow_phase(name):
    """Marks the start of the test phase ``name``. Added by the flow builder.

    Records the phase in the flow's checkpoint. When the run continues an
    earlier one (see ``FLOW_RESUME``) and the phase was completed there, the
    test is skipped with a note saying when it ran.
    """
    run = _current_run()
    reason = run.phase_started(name)
    if reason:
        raise SkipExecution(reason)
    _CONTROL.publish('running')


def flow_loop(loop_id, max_loops=None, max_seconds=None, *variables):
    """Starts the loop ``loop_id`` and returns its bounds. Added by the flow builder.

    Returns the iteration limit (``NONE`` without one) and the deadline on the
    flow clock. In a run that continues an earlier one they are what was left
    when that run was interrupted, and the ``variables`` saved then are
    restored.
    """
    builtin = BuiltIn()
    run = _current_run()
    loops = _number(max_loops)
    seconds = timestr_to_secs(max_seconds) if _given(max_seconds) else None
    limit, deadline, restored, message = run.loop_started(
        loop_id, int(loops) if loops is not None else None, seconds
    )
    run.variable_names = [name for name in variables if name]
    run.read_variable = builtin.get_variable_value
    for name, value in restored.items():
        builtin.set_test_variable(name, value)
    if message:
        logger.info(message, also_console=True)
        if restored:
            logger.info(f"Restored variables: {', '.join(sorted(restored))}.")
    return ['NONE' if limit is None else str(limit), deadline]


def flow_iteration(loop_id):
    """Marks the start of an iteration of ``loop_id``. Added by the flow builder.

    The iterations before this one are complete: the checkpoint is updated
    (at most once a second, or every ``checkpoint_every`` iterations).
    """
    run = _CONTROL.run
    if run is not None:
        run.iteration_started(loop_id)
    # A stop asked for during the previous iteration takes effect here, with
    # that iteration counted as done.
    _CONTROL.pause_point(EXECUTION_CONTEXTS.current)


# ------------------------------------------------------ pause, resume, stop

def flow_pause(reason='keyword'):
    """Pauses the flow at its next step boundary.

    The flow, its gates and its ``THREAD`` workers wait until it is resumed
    with ``python -m robot.flow control <store> resume`` (or ``Flow Resume``
    from code that is not part of the paused flow). Loop deadlines, gate
    timeouts and watchdogs do not advance meanwhile.

    Typical use: a Watchdog's ``on_timeout=Flow Pause`` holds the bench for a
    human instead of aborting the run. Needs the run's signal store
    (``ROBOT_FLOW_SIGNALS``), because something has to be able to resume.
    """
    if not _CONTROL.can_resume:
        raise RuntimeError(
            "'Flow Pause' needs a way to resume: set ROBOT_FLOW_SIGNALS to the "
            "run's signal store before starting the flow."
        )
    if _CONTROL.pause(reason):
        logger.info(f'Flow pause requested by {reason}.')


def flow_resume():
    """Resumes a paused flow. Does nothing when it is not paused."""
    seconds = _CONTROL.resume()
    if seconds:
        logger.info(f'Flow resumed after {secs_to_timestr(round(seconds, 1))}.')


def flow_stop(reason='keyword'):
    """Stops the flow at its next step boundary.

    The checkpoint is written, the running test ends with the UNKNOWN status
    and a message naming the checkpoint, the teardown runs and the remaining
    tests are not started. Running the flow again continues it (see
    ``FLOW_RESUME``).
    """
    _CONTROL.stop(reason)
    logger.info(f'Flow stop requested by {reason}.')


# ------------------------------------------------------------------ plumbing

def _current_run():
    """The checkpoint state of the flow being executed, created on first use."""
    builtin = BuiltIn()
    source = str(builtin.get_variable_value('${SUITE SOURCE}') or '')
    run = _CONTROL.run
    if run is None or run.finished or run.source != source:
        run = _start_run(builtin, source)
    return run


def _start_run(builtin, source):
    value = builtin.get_variable_value
    outputdir = str(value('${OUTPUT DIR}') or '')
    path = value(CHECKPOINT_VARIABLE)
    if not path:
        stem = os.path.basename(source) or str(value('${SUITE NAME}'))
        for extension in ('.json', '.flow'):
            if stem.lower().endswith(extension):
                stem = stem[:-len(extension)]
        path = _control.checkpoint_name(stem)
    path = os.path.join(outputdir, str(path))    # an absolute path stays as it is
    every = _number(value(CHECKPOINT_EVERY_VARIABLE))
    rig = value(RIG_VARIABLE)
    if rig:
        _CONTROL.rig = str(rig)
    run = _control.FlowRun(source, str(value('${SUITE NAME}')), path, outputdir,
                           int(every) if every else None)
    _CONTROL.run = run
    mode = str(value(RESUME_VARIABLE) or 'auto').strip().lower()
    if run.load(mode):
        done = ', '.join(phase['name'] for phase in run.completed) or 'none'
        logger.info(f"Flow continues from checkpoint '{run.path}' "
                    f"(completed phases: {done}).", also_console=True)
    if is_truthy(value(STEP_VARIABLE)):
        _CONTROL.enable_step_mode()
    return run


def _given(value):
    return value is not None and str(value).strip().upper() not in ('', 'NONE')


def _number(value):
    if not _given(value):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return float(value)


class _Listener:
    """Tells the checkpoint when a phase and the flow end."""

    ROBOT_LISTENER_API_VERSION = 3

    def end_test(self, data, result):
        run = _CONTROL.run
        if run is not None:
            run.phase_ended(result.name, result.status)

    def end_suite(self, data, result):
        run = _CONTROL.run
        if run is not None and str(data.source or '') == run.source:
            run.finish()
            _CONTROL.publish('stopped' if run.stopped else 'finished')


ROBOT_LIBRARY_LISTENER = _Listener()


# The body runner's pause point, and the control channel of the run: the
# signal store the flows of a run group already share.
_control.install()
if os.environ.get('ROBOT_FLOW_SIGNALS'):
    from .signals import FlowSignals as _FlowSignals
    _CONTROL.start_polling(_FlowSignals()._store)
