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

``Flow Gate`` is the wait of a ``gate`` node; ``Flow Retry`` runs a ``keyword``
node that has a ``retry`` policy or a ``timeout``. ``Flow Parallel Start``,
``Flow Branch`` and ``Flow Join`` run a ``parallel`` node: the branches in
``THREAD`` blocks, joined before the plan goes on. ``Flow Phase`` and
``Flow Loop`` are bookkeeping the builder adds for the checkpoint (see
:mod:`robot.flow.control`; the iterations are counted by the WHILE runner's
hook, without a keyword). ``Flow Pause``, ``Flow Resume`` and
``Flow Stop`` let a test or a supervisor hold or end the flow it runs in.
"""

import os
import threading
import time

from robot.api import logger, SkipExecution
from robot.errors import DataError, ExecutionFailed, UnknownAssertionError
from robot.libraries.BuiltIn import BuiltIn
from robot.running.bodyrunner import THREAD_STOP_MESSAGE
from robot.running.context import EXECUTION_CONTEXTS
from robot.running.runkwregister import RUN_KW_REGISTER
from robot.running.timeouts import KeywordTimeout
from robot.utils import is_truthy, secs_to_timestr, timestr_to_secs
from robot.version import get_version

from . import control as _control
from .control import CONTROL as _CONTROL


__all__ = ['flow_gate', 'flow_retry', 'flow_parallel_start', 'flow_branch', 'flow_join',
           'flow_phase', 'flow_loop', 'flow_pause', 'flow_resume', 'flow_stop',
           'flow_snapshot']

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


def flow_retry(max_attempts, interval, backoff, on_exhausted, timeout, name, *args):
    """Runs the keyword ``name`` under a retry policy and a time limit per attempt.

    The keyword is run up to ``max_attempts`` times until it passes, waiting
    ``interval`` between attempts; the wait grows by the factor ``backoff``
    each time (``1``: a constant wait). ``timeout`` (``NONE`` for none) is a
    Robot keyword timeout on each attempt: an attempt that exceeds it is
    interrupted and counts as failed. The flow clock is used for the waits,
    so a paused flow does not spend them.

    When the last attempt fails too, the step fails with the last error and
    the number of attempts; ``on_exhausted`` decides its status: ``fail``
    (the keyword's own verdict) or ``unknown`` (the bench did not cooperate,
    nothing was tested). With a single attempt and no time limit exceeded,
    the keyword's own failure is raised unchanged.

    A failure that must not be retried -- a test timeout, a syntax error,
    an exit-on-failure, a skip -- is raised at once.

    Returns the passing attempt's return value. The builder generates calls
    of this keyword for ``keyword`` nodes with ``retry`` or ``timeout``.
    """
    attempts = _positive_int('max_attempts', max_attempts, name)
    try:
        wait = timestr_to_secs(interval)
        factor = float(backoff)
        limit = timestr_to_secs(timeout) if _given(timeout) else None
    except ValueError as err:
        raise DataError(f"Retry policy of '{name}' is invalid: {err}")
    if on_exhausted not in ('unknown', 'fail'):
        raise DataError(f"'on_exhausted' must be 'unknown' or 'fail', got "
                        f"'{on_exhausted}'.")
    builtin = BuiltIn()
    context = EXECUTION_CONTEXTS.current
    for attempt in range(1, attempts + 1):
        result, error, timed_out = _attempt(builtin, context, limit, name, args)
        if error is None:
            if attempt > 1:
                logger.info(f"'{name}' passed on attempt {attempt} of {attempts}.")
            return result
        if attempt < attempts:
            logger.info(f"Attempt {attempt} of {attempts} of '{name}' "
                        f"{'timed out' if timed_out else 'failed'}, next attempt in "
                        f"{secs_to_timestr(wait)}: {error}")
            _CONTROL.sleep(wait)
            wait *= factor
            continue
        if attempts == 1 and not timed_out:
            raise error
        what = (f"timed out after {secs_to_timestr(limit)}" if timed_out and attempts == 1
                else f"failed after {attempts} attempts")
        message = f"'{name}' {what}. Last error: {error}"
        if on_exhausted == 'fail':
            raise AssertionError(message)
        raise UnknownAssertionError(message)


RUN_KW_REGISTER.register_run_keyword('robot.flow.keywords', 'flow_retry', 5,
                                     deprecation_warning=False, dry_run=True)

def _attempt(builtin, context, limit, name, args):
    """One attempt of ``name``: ``(return value, None, False)`` when it passed,
    else ``(None, failure, whether it was this attempt's own time limit)``."""
    if limit is None:
        timeout = None
        run = lambda: builtin.run_keyword(name, *args)
    else:
        # The same keyword timeout a user keyword's [Timeout] sets: the
        # keyword runner applies it to the attempt and interrupts it.
        timeout = KeywordTimeout(limit)

        def run():
            with context.keyword_timeout(timeout):
                return builtin.run_keyword(name, *args)
    occurred = context.timeout_occurred
    try:
        return run(), None, False
    except ExecutionFailed as err:
        if err.keyword_timeout and timeout is not None and timeout.timed_out():
            # Our own limit, not an enclosing one: the attempt is over and the
            # flow goes on, so a teardown must not see a timeout that was handled.
            context.timeout_occurred = occurred
            return None, err, True
        if err.dont_continue or err.skip:
            raise
        return None, err, False


def _positive_int(attr, value, name):
    try:
        number = _number(value)
        if number is None or number < 1 or int(number) != number:
            raise ValueError
    except ValueError:
        raise DataError(f"'{attr}' of '{name}' must be a positive integer, got {value!r}.")
    return int(number)


# ---------------------------------------------------------- parallel regions

JOIN_SLICE_S = 0.2      # how often the join looks at the branches
STOP_GRACE_S = 10.0     # how long a branch asked to stop gets to reach its next step
STOPPED, ABANDONED = 'STOPPED', 'ABANDONED'


class _Region:
    """One run of a parallel region: its branches' threads and their verdicts."""

    def __init__(self, id, branches, names, deadline, stop_others):
        self.id = id
        self.branches = list(branches)      # branch ids, in plan order
        self.names = list(names)            # the threads, in the same order
        self.deadline = deadline            # on the flow clock, or None
        self.stop_others = stop_others
        self.started = _CONTROL.clock()
        self.results = {}                   # thread name -> (status, message)
        self.stopping = False
        self.lock = threading.Lock()

    def branch(self, name):
        return self.branches[self.names.index(name)]

    def record(self, name, status, message=''):
        with self.lock:
            self.results[name] = (status, message)

    def result(self, name):
        with self.lock:
            return self.results.get(name)


_REGIONS = {}       # region id -> its current run
_BY_THREAD = {}     # thread name -> the run it belongs to
_RUNS = {}          # region id -> how often it started in this process
_REGIONS_LOCK = threading.Lock()


def flow_parallel_start(region, timeout, stop_others, *branches):
    """Starts the parallel region ``region``: returns the name of each branch's thread.

    The builder adds this before the ``THREAD`` blocks of a ``parallel`` node
    and assigns the names it returns to them. The names are unique in the
    process (``flow.<region>.<branch>``, numbered from the second run of the
    region on), so a region inside a loop keeps every iteration's branch logs.

    ``timeout`` (``NONE`` for none) is how long the branches have to join, on
    the flow clock; ``stop_others`` whether a failed branch stops the others.
    """
    if len(branches) < 2:
        raise DataError(f"Region '{region}' needs at least two branches.")
    try:
        seconds = timestr_to_secs(timeout) if _given(timeout) else None
    except ValueError:
        raise DataError(f"'timeout' of region '{region}' must be a valid time, "
                        f"got {timeout!r}.")
    deadline = _CONTROL.clock() + seconds if seconds is not None else None
    with _REGIONS_LOCK:
        number = _RUNS[region] = _RUNS.get(region, 0) + 1
        suffix = f'.{number}' if number > 1 else ''
        names = [f'flow.{region}.{branch}{suffix}' for branch in branches]
        run = _Region(region, branches, names, deadline, is_truthy(stop_others))
        _REGIONS[region] = run
        for name in names:
            _BY_THREAD[name] = run
    within = f' to join within {secs_to_timestr(seconds)}' if seconds is not None else ''
    logger.info(f"Region '{region}' starts branches {', '.join(branches)}{within}.")
    return names


def flow_branch(name, keyword, *args):
    """Runs one branch of a parallel region and records how it ended.

    ``name`` is the branch's thread (from ``Flow Parallel Start``), ``keyword``
    the generated user keyword holding the branch's steps. How the branch
    ended is recorded for ``Flow Join``, which gives the region its verdict;
    the branch's own failure is logged here and not raised again, so a
    failed branch is not also a warning of the run (a ``THREAD`` failure
    would be). A branch stopped at the region's request says so.
    """
    run = _BY_THREAD.get(name)
    branch = run.branch(name) if run is not None else name
    region = run.id if run is not None else '?'
    try:
        BuiltIn().run_keyword(keyword, *args)
    except ExecutionFailed as err:
        if err.dont_continue and err.message != THREAD_STOP_MESSAGE:
            raise           # a fatal error of the run, not of this branch
        if err.message == THREAD_STOP_MESSAGE:
            status, message = STOPPED, f"Branch '{branch}' of '{region}' was stopped."
        else:
            status, message = err.status, err.message
        if run is not None:
            run.record(name, status, message)
        error = ExecutionFailed(message, unknown=status != 'FAIL')
        # The join reports it; the thread does not add a warning of its own.
        error.collected = True
        raise error
    if run is not None:
        run.record(name, 'PASS')


RUN_KW_REGISTER.register_run_keyword('robot.flow.keywords', 'flow_branch', 1,
                                     deprecation_warning=False, dry_run=True)


def flow_join(region):
    """Waits until every branch of the parallel region ``region`` has ended.

    The wait is in slices on the flow clock and at the pause point: a pause
    holds the join and the branches, a stop stops the branches first and
    then ends the flow as usual. With ``stop_others`` a failed branch stops
    the others at their next step. When all have joined the step passes, or
    fails naming each failed branch with its error; the status is the worst
    of the branches' (UNKNOWN over FAIL). A region that does not join within
    its ``timeout`` fails naming the branches still running, which are
    stopped; a branch that does not stop within the grace is abandoned.
    """
    run = _REGIONS.get(region)
    if run is None:
        raise DataError(f"Region '{region}' has not been started "
                        f"('Flow Parallel Start' did not run).")
    context = EXECUTION_CONTEXTS.current
    abandoned = []
    timed_out = False
    while True:
        running = _running(context, run)
        failed = [name for name in run.names
                  if (run.result(name) or ('',))[0] in ('FAIL', 'UNKNOWN')]
        if failed and run.stop_others and running and not run.stopping:
            run.stopping = True
            logger.info(f"Branch '{run.branch(failed[0])}' of '{region}' failed; "
                        f"stopping the other branch(es): "
                        f"{', '.join(run.branch(name) for name in running)}.")
            _request_stop(context, running)
        if not running:
            break
        if _CONTROL.stop_requested:
            abandoned += _stop_branches(context, run, running)
            running = []
        try:
            _CONTROL.pause_point(context)
        except ExecutionFailed:
            _stop_branches(context, run, _running(context, run))
            raise
        if run.deadline is not None and _CONTROL.clock() >= run.deadline:
            timed_out = True
            abandoned += _stop_branches(context, run, running)
            break
        time.sleep(JOIN_SLICE_S)
    elapsed = secs_to_timestr(round(_CONTROL.clock() - run.started, 1))
    lines, unknown = [], False
    if timed_out:
        still = [run.branch(name) for name in running]
        lines.append(f"Region '{region}' did not join within "
                     f"{secs_to_timestr(run.deadline - run.started)}; still running: "
                     f"{', '.join(still)}.")
    for name, branch in zip(run.names, run.branches):
        status, message = run.result(name) or (None, '')
        if name in abandoned:
            lines.append(f"Branch '{branch}' of '{region}' did not stop within "
                         f"{secs_to_timestr(STOP_GRACE_S)} and was abandoned.")
        elif status == 'FAIL':
            lines.append(f"Branch '{branch}' of '{region}' failed: {message}")
        elif status == 'UNKNOWN':
            unknown = True
            lines.append(f"Branch '{branch}' of '{region}' is unknown: {message}")
        elif status is None and not timed_out:
            unknown = True
            lines.append(f"Branch '{branch}' of '{region}' ended without a verdict.")
    if not lines:
        logger.info(f"Region '{region}': {len(run.names)} branches joined after {elapsed}.")
        return
    message = '\n'.join(lines)
    if unknown:
        raise UnknownAssertionError(message)
    raise AssertionError(message)


def _running(context, run):
    """The branches of ``run`` whose threads are still alive, in plan order."""
    threads = context.active_threads
    alive = []
    for name in run.names:
        entry = threads.get(name)
        if entry is not None and entry['worker'].is_alive():
            alive.append(name)
    return alive


def _request_stop(context, names):
    threads = context.active_threads
    for name in names:
        entry = threads.get(name)
        if entry is not None:
            entry['stop_event'].set()


def _stop_branches(context, run, names):
    """Stop the branches ``names`` at their next step; wait up to the grace
    for them. Returns the ones that did not stop, which are abandoned."""
    if not names:
        return []
    _request_stop(context, names)
    threads = context.active_threads
    deadline = time.time() + STOP_GRACE_S
    abandoned = []
    for name in names:
        entry = threads.get(name)
        if entry is None:
            continue
        entry['worker'].join(max(0.0, deadline - time.time()))
        if entry['worker'].is_alive():
            abandoned.append(name)
            logger.warn(f"Branch '{run.branch(name)}' of '{run.id}' did not stop within "
                        f"{secs_to_timestr(STOP_GRACE_S)} and was abandoned.")
            threads.pop(name, None)
    return abandoned


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
    try:
        loops = _number(max_loops)
        if loops is not None and (loops < 1 or int(loops) != loops):
            raise ValueError
    except ValueError:
        raise DataError(f"'max_loops' of loop '{loop_id}' must be a positive "
                        f"integer, got {max_loops!r}.")
    try:
        seconds = timestr_to_secs(max_seconds) if _given(max_seconds) else None
    except ValueError:
        raise DataError(f"'max_seconds' of loop '{loop_id}' must be a valid time, "
                        f"got {max_seconds!r}.")
    limit, deadline, restored, message = run.loop_started(
        loop_id, int(loops) if loops is not None else None, seconds
    )
    run.variable_names = [name for name in variables if name]
    run.read_variable = builtin.get_variable_value
    scopes = EXECUTION_CONTEXTS.current.namespace.variables
    kept = [name for name in restored if scopes.given_on_command_line(name)]
    for name, value in restored.items():
        if name not in kept:
            builtin.set_test_variable(name, value)
    if message:
        logger.info(message, also_console=True)
        if restored:
            logger.info(f"Restored variables: {', '.join(sorted(set(restored) - set(kept)))}."
                        if set(restored) - set(kept) else 'No variables restored.')
        if kept:
            logger.info(f"Kept from the command line instead of the checkpoint: "
                        f"{', '.join(sorted(kept))}.")
    return ['NONE' if limit is None else str(limit), deadline]


# ------------------------------------------------------ pause, resume, stop

def flow_snapshot(name=''):
    """Writes a named snapshot of the flow's state next to its checkpoint.

    The file is kept after the run and a new run continues from it with
    ``FLOW_CHECKPOINT``. The operator does the same from outside with
    ``python -m robot.flow control <store> snapshot [name]``.
    """
    path = _current_run().snapshot(name)
    logger.info(f"Snapshot '{os.path.basename(path)}' written; continue from it with "
                f"FLOW_CHECKPOINT:{path}", also_console=True)
    return path


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
    _follow_store()
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

    def start_suite(self, data, result):
        # A flow's loops run for hours: finished iterations leave memory once
        # they are in the output file (what --pruneresults does for suites).
        EXECUTION_CONTEXTS.current.prune_results = True

    def end_test(self, data, result):
        run = _CONTROL.run
        if run is not None:
            run.phase_ended(result.name, result.status)

    def end_suite(self, data, result):
        run = _CONTROL.run
        if run is not None and str(data.source or '') == run.source:
            run.finish()
            _CONTROL.publish('stopped' if run.stopped else 'finished')
            # Nothing is left to control; the next run binds the poller again.
            _CONTROL.stop_polling()
        with _REGIONS_LOCK:
            _REGIONS.clear()
            _BY_THREAD.clear()


ROBOT_LIBRARY_LISTENER = _Listener()


def _follow_store():
    """Poll the signal store named by ROBOT_FLOW_SIGNALS, (re)bound per run.

    A process that runs several flows in a row (the API, tests) may name a
    different store for each; the poller follows the one of the current run.
    """
    store = os.environ.get('ROBOT_FLOW_SIGNALS')
    if not store:
        return
    if _CONTROL._poller is not None and _CONTROL._store_url == store:
        return
    from .signals import open_store as _open_store
    _CONTROL.stop_polling()
    _CONTROL.start_polling(_open_store(store), url=store)


# The body runner's pause point, and the control channel of the run: the
# signal store the flows of a run group already share.
_control.install()
_follow_store()
