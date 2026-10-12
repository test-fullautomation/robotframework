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

"""Pause, resume and stop of a running flow, and its checkpoint for a restart.

Two features share this module.

*Pause / resume* holds a running flow in the same process. A flow pauses
between two steps, never inside a keyword, and while it is paused the *flow
clock* (:func:`clock`) stands still: loop deadlines and gate timeouts are
measured with it, so a hold does not use up their time. The command comes
from the run's signal store (``python -m robot.flow control <store> pause``),
from the ``Flow Pause`` / ``Flow Resume`` / ``Flow Stop`` keywords, or from
step mode (``--variable FLOW_STEP:yes``).

*Checkpoint / restart* continues a flow in a new process. While a flow runs,
a small JSON file records the phases that finished and how far the current
loop got. A stopped or crashed run leaves it behind; the next run of the same
flow file reads it, skips the finished phases and continues the loop with
what is left of its bounds. See :class:`FlowRun`.

``stop`` joins the two: the flow leaves at its next step boundary, the
checkpoint is written, the test ends UNKNOWN and the teardown runs.
"""

import hashlib
import json
import os
import re
import sys
import threading
import time

from robot.api import logger
from robot.errors import DataError, ExecutionFailed
from robot.running import pausepoint
from robot.running.context import EXECUTION_CONTEXTS
from robot.utils import in_worker_thread, secs_to_timestr


CONTROL_SIGNAL = 'flow.control'
# A stop is written under this name as well. The store keeps one value per
# name, so a command given right after a stop would replace it before the
# flow has read it; under its own name the stop stays.
STOP_SIGNAL = 'flow.stop'
STATE_SIGNAL = 'flow.state'
# Requests with a payload, each under its own name so none replaces another:
# 'flow.set.<rig or *>.<NAME>' holds a value, 'flow.snapshot[.<rig>]' a name.
SET_SIGNAL = 'flow.set'
SNAPSHOT_SIGNAL = 'flow.snapshot'
PAUSE, RESUME, STOP, SET, SNAPSHOT = 'pause', 'resume', 'stop', 'set', 'snapshot'
COMMANDS = (PAUSE, RESUME, STOP, SET, SNAPSHOT)
VARIABLE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
RESUME_MODES = ('auto', 'always', 'never')
CHECKPOINT_VERSION = 1


class FlowControl:
    """Pause state of this process, shared by the main flow and its threads."""

    POLL_S = 0.5        # how often the control signal is read
    SLICE_S = 0.2       # how long a paused thread sleeps between looks

    def __init__(self):
        self._lock = threading.RLock()
        self._paused = threading.Event()
        self._pause_started = None
        self._pause_reason = ''
        self._paused_total = 0.0
        self._stop_reason = None
        self._store = None
        self._store_url = None  # what the store was opened from, to tell a rebind
        self._poller = None
        self._poller_stop = threading.Event()
        # Commands are for this process when written after it started: by the
        # store's sequence number, or by time for a store that has none.
        self._since = time.time()
        self._since_seq = 0
        self._seen = None
        self._applied = set()   # (store key, seq or time) of requests taken over
        self._pending = []      # set / snapshot requests for the flow's thread
        self._console = None
        self.rig = os.environ.get('ROBOT_FLOW_RIG', '')
        self.step_mode = False
        self.stopped = False
        self.run = None         # the FlowRun of the flow being executed, if any

    # ------------------------------------------------------------- the clock

    def clock(self):
        """Seconds since the epoch, not counting the time spent paused."""
        with self._lock:
            paused = self._paused_total
            if self._pause_started is not None:
                paused += time.time() - self._pause_started
        return time.time() - paused

    # ------------------------------------------------------- pause and stop

    @property
    def paused(self):
        return self._paused.is_set()

    @property
    def stop_requested(self):
        return self._stop_reason is not None

    @property
    def can_resume(self):
        """Whether something outside the flow can end a pause."""
        return self._poller is not None or self._console is not None

    def pause(self, reason='operator'):
        with self._lock:
            if self._paused.is_set():
                return False
            self._pause_started = time.time()
            self._pause_reason = reason
            self._paused.set()
        pausepoint.notify_pause()
        self._publish('paused')
        return True

    def resume(self):
        with self._lock:
            if not self._paused.is_set():
                return 0.0
            seconds = time.time() - self._pause_started
            self._paused_total += seconds
            self._pause_started = None
            self._paused.clear()
        pausepoint.notify_resume(seconds)
        self._publish('running')
        return seconds

    def stop(self, reason='operator'):
        """Ask the flow to leave at its next step boundary."""
        with self._lock:
            if self.stopped or self._stop_reason is not None:
                return
            self._stop_reason = reason

    def pause_point(self, context=None, where=None):
        """Wait here while paused; leave here when stopped.

        Called between steps by the body runner hook and between the polls of
        a gate. Worker threads wait like the main flow but are never the ones
        to raise the stop: their scope ends them.
        """
        if self._paused.is_set():
            self._wait(where)
        if self._pending:
            self._apply_pending()
        if self._stop_reason is not None and _on_main_thread() \
                and not (context is not None and context.in_teardown):
            self._raise_stop(where)

    def sleep(self, seconds):
        """Sleep on the flow clock: a pause during the sleep does not count."""
        end = self.clock() + seconds
        while True:
            self.pause_point(EXECUTION_CONTEXTS.current)
            remaining = end - self.clock()
            if remaining <= 0:
                return
            time.sleep(min(remaining, self.SLICE_S))

    def _wait(self, where):
        reason = self._pause_reason
        position = where or self._position()
        logger.info(f"Flow paused by {reason}{' ' + position if position else ''}.",
                    also_console=_on_main_thread())
        runners = self._pause_timeouts()
        started = time.time()
        while self._paused.is_set() and self._stop_reason is None:
            if _signal_received():
                # Ctrl+C while paused: the run ends as a stop at the step
                # boundary this wait returns to; a second signal would end
                # the process without log or report.
                logger.info('Signal received while paused: the flow leaves the pause.')
                self.resume()
                break
            time.sleep(self.SLICE_S)
        seconds = time.time() - started
        self._resume_timeouts(runners, seconds)
        waited = secs_to_timestr(round(seconds, 1))
        if self._stop_reason is None:
            logger.info(f'Flow resumed after {waited}.', also_console=_on_main_thread())
        else:
            logger.info(f'Flow stopped while paused ({waited}).')

    def _pause_timeouts(self):
        """Keyword and test timeouts run on the wall clock: they are held
        while the flow is paused and given the pause back afterwards, so a
        timed step paused for an hour has all of its timeout, as a gate has."""
        if not _on_main_thread():
            return []
        context = EXECUTION_CONTEXTS.current
        if context is None:
            return []
        runners = [r for r in getattr(context, 'active_timeouts', ()) if hasattr(r, 'pause')]
        for runner in runners:
            runner.pause()
        return runners

    def _resume_timeouts(self, runners, seconds):
        context = EXECUTION_CONTEXTS.current
        if context is not None and seconds > 0:
            for timeout in getattr(context, 'timeouts', ()):
                if getattr(timeout, 'start_time', -1) >= 0:
                    timeout.start_time += seconds
        for runner in runners:
            if seconds > 0:
                runner.extend(seconds)      # forgets a timer that fired meanwhile
            runner.resume()

    def _raise_stop(self, where):
        with self._lock:
            reason, self._stop_reason = self._stop_reason, None
            self.stopped = True
        self.resume()
        message = f'Stopped by {reason}'
        position = where or self._position()
        if position:
            message += f' {position}'
        run = self.run
        if run is not None:
            path = run.stop()
            if path:
                message += f'; resumable from {path}'
        self._publish('stopped')
        # Flagged as a syntax error only so that no TRY/EXCEPT, recovery region
        # or `Run Keyword And Ignore Error` can catch it; `exit` ends the run.
        raise ExecutionFailed(message + '.', syntax=True, exit=True, unknown=True)

    def _position(self):
        run = self.run
        return run.position_text() if run is not None else ''

    # -------------------------------------------------------- step mode

    def step(self, context, step):
        """Step mode: pause before every node of the flow."""
        if not _on_main_thread() or context.in_teardown or self._paused.is_set():
            return
        name = getattr(step, 'name', None)
        if getattr(step, 'lineno', None) is not None or not name \
                or name in _INTERNAL_KEYWORDS:
            return      # a step of a resource file, a control structure, bookkeeping
        self.pause(f"step mode before '{name}'")

    def enable_step_mode(self):
        if not self.can_resume and not self._start_console():
            raise DataError(
                'Step mode needs a way to resume: set ROBOT_FLOW_SIGNALS to the '
                "run's signal store, or run on a console."
            )
        self.step_mode = True

    def _start_console(self):
        """Enter on the console resumes; only when there is a console."""
        stream = sys.__stdin__
        try:
            if stream is None or not stream.isatty():
                return False
        except (AttributeError, ValueError):
            return False

        def read():
            while True:
                try:
                    if not stream.readline():
                        return
                except (OSError, ValueError):
                    return
                self.resume()

        self._console = threading.Thread(target=read, name='FlowConsole', daemon=True)
        self._console.start()
        return True

    # ------------------------------------------------- the control channel

    def start_polling(self, store, url=None):
        """Follow the control signal of ``store`` (a :class:`~robot.flow.signals.SignalStore`)."""
        with self._lock:
            if self._poller is not None:
                return
            self._store = store
            self._store_url = url
            try:
                self._since_seq = int(store.last_seq())
            except Exception:
                self._since_seq = 0
            # What was seen and applied belongs to the store it came from:
            # another store numbers its writes from the start again.
            self._seen = None
            self._applied = set()
            self._poller_stop.clear()
            self._poller = threading.Thread(target=self._poll, name='FlowControl',
                                            daemon=True)
            self._poller.start()

    def stop_polling(self):
        with self._lock:
            poller, self._poller = self._poller, None
        if poller is not None:
            self._poller_stop.set()
            poller.join(self.POLL_S * 4)

    def _poll(self):
        store = self._store
        waits = getattr(store, 'supports_wait', False)
        last = self._since_seq
        while not self._poller_stop.is_set():
            if waits:
                # The store tells when something changed (a long poll, a
                # subscription): a command reaches the flow at once.
                try:
                    store.wait(last, self.POLL_S)
                except Exception:
                    time.sleep(self.POLL_S)
                if self._poller_stop.is_set():
                    break
            elif self._poller_stop.wait(self.POLL_S):
                break
            try:
                entries = self._entries()
                newest = max((_seq(entry) for entry in entries.values()), default=0)
                if entries and newest < max(last, self._since_seq):
                    # The store numbers its writes from the start again (the
                    # server restarted without its file, Redis was flushed):
                    # what it holds now is new to this process, whatever was
                    # seen or applied before.
                    with self._lock:
                        self._since_seq = 0
                        self._seen = None
                        self._applied = set()
                    last = 0
                self.apply(self._read_command(entries))
                self._queue(self._read_requests(entries))
                last = max(last, newest)
            except Exception:       # a store that cannot be read must not end the watch
                pass

    def _entries(self):
        """Every entry of the store; a store of the first contract (get/set
        only) is read name by name for the commands."""
        read = getattr(self._store, 'read', None) or getattr(self._store, '_read', None)
        entries = read() if read else None
        if entries:
            return dict(entries)
        entries = {}
        for signal in (STOP_SIGNAL, CONTROL_SIGNAL, SNAPSHOT_SIGNAL):
            for name in self._names(signal):
                entry = self._store.get(name)
                if entry:
                    entries[name] = entry
        return entries

    def _newer(self, entry):
        """Written after this process started: by sequence number, else by time."""
        if not isinstance(entry, dict):
            return False
        seq = _seq(entry)
        if seq:
            return seq > self._since_seq
        return entry.get('time', 0) >= self._since

    def _read_command(self, entries):
        """The newest command for this process given since it started.

        A stop, once given, is the command: nothing written after it takes
        it back.
        """
        for name in self._names(STOP_SIGNAL):
            entry = entries.get(name)
            if self._newer(entry):
                return {'value': STOP, 'time': entry.get('time'), 'seq': _seq(entry)}
        newest = None
        for name in self._names(CONTROL_SIGNAL):
            entry = entries.get(name)
            if not self._newer(entry):
                continue
            if newest is None or _order(entry) >= _order(newest):
                newest = entry
        return newest

    def _read_requests(self, entries):
        """Set and snapshot requests for this process since it started, each once."""
        found = []
        for key, entry in entries.items():
            if not self._newer(entry):
                continue
            stamp = (key, _seq(entry) or entry.get('time'))
            if stamp in self._applied:
                continue
            if key.startswith(SET_SIGNAL + '.'):
                # The variable name holds no dot, a rig name may ('RIG.A').
                target, _, name = key[len(SET_SIGNAL) + 1:].rpartition('.')
                if name and target in ('*', self.rig or '*'):
                    found.append((stamp, _order(entry), (SET, name, entry.get('value'))))
            elif key in self._names(SNAPSHOT_SIGNAL):
                found.append((stamp, _order(entry), (SNAPSHOT, str(entry.get('value') or ''))))
        found.sort(key=lambda item: item[1])
        for stamp, _, request in found:
            self._applied.add(stamp)
        return [request for _, _, request in found]

    def _queue(self, requests):
        if requests:
            with self._lock:
                self._pending.extend(requests)

    def request(self, kind, *args):
        """Queue a set / snapshot request from inside the process (keywords, tests)."""
        self._queue([(kind, *args)])

    def _apply_pending(self):
        """Apply queued requests on the flow's own thread, between two steps."""
        if not _on_main_thread():
            return
        with self._lock:
            pending, self._pending = self._pending, []
        for request in pending:
            try:
                if request[0] == SET:
                    self._set_variable(request[1], request[2])
                else:
                    self._take_snapshot(request[1])
            except Exception as err:
                logger.warn(f'Flow {request[0]} request failed: {err}')

    def _set_variable(self, name, value):
        from robot.libraries.BuiltIn import BuiltIn
        if not VARIABLE_NAME.match(str(name)):
            raise DataError(f"'{name}' is not a variable name.")
        value = '' if value is None else str(value)
        BuiltIn().set_suite_variable(f'${{{name}}}', value)
        logger.info(f"Variable ${{{name}}} set to '{value}' by operator (suite scope).",
                    also_console=True)

    def _take_snapshot(self, name):
        run = self.run
        if run is None:
            raise DataError('no flow is running')
        path = run.snapshot(name)
        logger.info(f"Snapshot '{os.path.basename(path)}' written {self._position()}: "
                    f"continue from it with FLOW_CHECKPOINT:{path}", also_console=True)
        return path

    def _names(self, signal):
        """The names a command for this process is written under."""
        return [signal, f'{signal}.{self.rig}'] if self.rig else [signal]

    def apply(self, entry):
        """Act on a control entry ``{'value': command, 'time': seconds}`` once."""
        if not entry:
            return
        key = (entry.get('value'), _seq(entry) or entry.get('time'))
        if key == self._seen:
            return
        self._seen = key
        command = str(entry.get('value')).strip().lower()
        if command == PAUSE:
            self.pause()
        elif command == RESUME:
            self.resume()
        elif command == STOP:
            self.stop()

    def _publish(self, state):
        """Tell the store what this process is doing, for `control status`."""
        store = self._store
        if store is None:
            return
        run = self.run
        entry = {'state': state}
        if run is not None:
            entry.update(flow=run.name, phase=run.phase, loop=run.loop,
                         iteration=run.iteration)
        try:
            store.set(f'{STATE_SIGNAL}.{self.rig or os.getpid()}', entry)
        except Exception:
            pass

    def publish(self, state):
        self._publish(state)

    def new_run(self):
        """A new flow starts in this process: the state of the one before
        (a stop given to it, its step mode, its paused time) is not its own.
        The store and its poller stay: `_follow_store` rebinds them."""
        self.resume()
        with self._lock:
            self._paused_total = 0.0
            self._stop_reason = None
            self._pending = []
            self.step_mode = False
            self.stopped = False
            self.run = None

    def clear_stop_request(self):
        with self._lock:
            self._stop_reason = None

    def reset(self):
        """Forget everything; for tests and for a new run in the same process."""
        self.stop_polling()
        self.resume()
        with self._lock:
            self._paused_total = 0.0
            self._stop_reason = None
            self._since = time.time()
            self._seen = None
            self._applied = set()
            self._pending = []
            self._store = None
            self._store_url = None
            self._since_seq = 0
            self.step_mode = False
            self.stopped = False
            self.run = None


# Keywords the builder emits for bookkeeping; step mode does not stop at them
# (a parallel region's node is its 'Flow Parallel Start').
_INTERNAL_KEYWORDS = ('Flow Phase', 'Flow Loop', 'Flow Loop End', 'Flow Branch', 'Flow Join')


def _seq(entry):
    try:
        return int((entry or {}).get('seq') or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _order(entry):
    """Writes in the order they happened: by sequence number, then by time."""
    return (_seq(entry), entry.get('time') or 0)

CONTROL = FlowControl()


def clock():
    """The flow clock: ``time.time()`` without the time spent paused.

    Loop deadlines are written with it (``robot.flow.control.clock() <
    $flow_deadline_x``), so a paused flow does not run out of time.
    """
    return CONTROL.clock()


def _on_main_thread():
    # The thread executing the suite; `robot.run()` may be called from any thread.
    return not in_worker_thread()


def _signal_received():
    """Whether Ctrl+C or SIGTERM reached the run (Robot raises it at the next keyword)."""
    try:
        from robot.running.signalhandler import STOP_SIGNAL_MONITOR
        return bool(getattr(STOP_SIGNAL_MONITOR, '_signal_count', 0))
    except Exception:
        return False


def _hook(context, step):
    """Called by the body runner before every step."""
    control = CONTROL
    if not (control.step_mode or control._paused.is_set()
            or control._stop_reason is not None or control._pending) or context.dry_run:
        return
    if control.step_mode:
        control.step(context, step)
    control.pause_point(context)


def _iteration_hook(context, iteration):
    """Called by the WHILE runner when an iteration starts, before its first step.

    Counts the iteration when the WHILE is a checkpointed flow loop: the
    iterations before it are complete, the checkpoint may be due. A stop asked
    for during the previous iteration takes effect here, with that iteration
    counted as done; a pause is held here too, so the position in the log is
    up to date.
    """
    run = CONTROL.run
    if run is None or run.finished or not _on_main_thread():
        return          # a branch thread's loop is not checkpointed, nor written from there
    loop_id = run.loop_of(iteration.parent)
    if loop_id is None:
        return          # a loop the checkpoint does not track (nested, sub-flow)
    run.iteration_started(loop_id)
    CONTROL.pause_point(context)


def install():
    """Register the pause point and the iteration hook with the body runner."""
    pausepoint.hook = _hook
    pausepoint.iteration_hook = _iteration_hook


# ---------------------------------------------------------------- checkpoint

class FlowRun:
    """What a flow has done so far, kept in a checkpoint file.

    The file is written when a test phase starts and ends, at the start of the
    iterations of its top-level loops (at most once a second, or every
    ``every`` iterations) and when the flow is stopped. A run that reaches its
    end deletes it; a stopped or crashed one leaves it for the next run.

    The unit of a restart is the loop iteration: the interrupted iteration is
    done again, so iterations must be safe to repeat. The setup phase always
    runs again, because the state of the bench lives outside the flow.
    """

    MIN_INTERVAL_S = 1.0

    def __init__(self, source, name, path, outputdir='', every=None, clock=clock):
        self.source = str(source) if source else ''
        self.name = name
        self.path = os.path.abspath(path)
        self.outputdir = str(outputdir)
        self.every = every
        self._clock = clock
        self.fingerprint = fingerprint(self.source)
        self.started = _timestamp()
        self.completed = []         # [{'name', 'status', 'finished', 'outputdir'}]
        self.phase = None
        self.loop = None
        self.iteration = 0          # completed iterations of `loop`
        self.done_loops = []        # loops of `phase` that ran to their end
        self.variables = {}
        self.variable_names = []    # variables of the current loop to save
        self.read_variable = None   # callable(name) giving a variable's value
        self._unsaved = set()
        self.stopped = False
        self.finished = False
        self._restored = None       # position saved by an earlier run
        self._phase_restored = None # the same, while its phase is being continued
        self._loops = {}            # loop id -> state
        self._tracked = None        # the WHILE data of `loop`, bound at its first iteration
        self._expect = False        # `Flow Loop` ran: the next WHILE to iterate is `loop`
        self._written = 0.0
        self.snapshots = []         # [{'name', 'path', 'taken', 'position'}]

    # --------------------------------------------------------------- restart

    def load(self, mode='auto'):
        """Take over the checkpoint of an earlier run.

        ``mode`` is ``auto`` (use a matching checkpoint, warn about another
        one), ``always`` (fail without a matching one) or ``never``. Returns
        whether a checkpoint was taken over.
        """
        if mode not in RESUME_MODES:
            raise DataError(f"FLOW_RESUME must be one of {', '.join(RESUME_MODES)}, "
                            f"got '{mode}'.")
        if mode == 'never':
            return False
        data, problem = read_checkpoint(self.path)
        if data is not None and data.get('snapshot'):
            # Continuing from a snapshot: it stays as it is; the run writes its
            # own checkpoint next to it.
            taken = data['snapshot']
            logger.info(f"Continuing from snapshot '{taken.get('name')}' taken "
                        f"{taken.get('taken')} {taken.get('position') or ''}.".replace(' .', '.'),
                        also_console=True)
            self.path = re.sub(r'\.snapshot-[^\\/]*\.json$', '.checkpoint.json', self.path)
        if data is not None and data.get('fingerprint') != self.fingerprint:
            data, problem = None, (f"Checkpoint '{self.path}' belongs to another "
                                   f"version of the flow file '{data.get('flow')}'")
        if data is None:
            if mode == 'always':
                raise DataError(f"{problem or 'No checkpoint at ' + chr(39) + self.path + chr(39)}; "
                                f"cannot resume (FLOW_RESUME is 'always').")
            if problem:
                logger.warn(f'{problem}; starting from the beginning.')
            return False
        self.completed = list(data.get('completed_phases') or [])
        self._restored = data.get('position') or {}
        self._restored['remaining'] = data.get('remaining') or {}
        self._restored['variables'] = data.get('variables') or {}
        self._restored['saved'] = data.get('saved')
        return True

    # ---------------------------------------------------------------- phases

    def phase_started(self, name):
        """Returns the reason to skip the phase, or None to run it."""
        for done in self.completed:
            if done.get('name') == name:
                where = f" (see {done['outputdir']})" if done.get('outputdir') else ''
                return (f"Completed with status {done.get('status', '?')} in the run "
                        f"that ended {done.get('finished', '?')}{where}.")
        self.phase = name
        self.loop = None
        self.iteration = 0
        self.done_loops = []
        self.variables = {}
        self.variable_names = []
        self._loops = {}
        self._tracked, self._expect = None, False
        restored = self._restored
        if restored and restored.get('phase') != name:
            restored = None
        self._phase_restored = restored
        if restored:
            self.done_loops = list(restored.get('done_loops') or [])
        self.write(force=True)
        return None

    def phase_ended(self, name, status):
        if self.stopped or name != self.phase:
            return
        self.completed.append({'name': name, 'status': status,
                               'finished': _timestamp(), 'outputdir': self.outputdir})
        self.phase = self.loop = None
        self.iteration = 0
        self.done_loops = []
        self._phase_restored = None
        self.write(force=True)

    # ----------------------------------------------------------------- loops

    def loop_started(self, loop_id, max_loops=None, max_seconds=None):
        """Bounds for the loop about to start.

        Returns ``(limit, deadline, variables, message)``: the iterations and
        the flow-clock deadline that are left, the variables to restore and a
        note for the log when the loop continues an earlier run.
        """
        if self.loop and self.loop != loop_id and self.loop not in self.done_loops:
            self.done_loops.append(self.loop)       # the previous loop ran to its end
        restored = self._phase_restored
        done = 0
        seconds = max_seconds
        variables = {}
        message = None
        self._tracked, self._expect = None, True
        if loop_id in self.done_loops:
            self.done_loops.remove(loop_id)
            self._loops[loop_id] = {'offset': 0, 'count': 0, 'deadline': -1.0,
                                    'max_loops': max_loops}
            self.loop, self.iteration = loop_id, 0
            # No WHILE of this loop iterates: the next one to iterate (a
            # nested loop, a sub-flow's) is not this loop's and must not be
            # claimed as it.
            self._expect = False
            return 1, -1.0, {}, f"Loop '{loop_id}' ran to its end in the earlier run."
        if restored and restored.get('loop') == loop_id:
            done = int(restored.get('iteration') or 0)
            remaining = restored.get('remaining') or {}
            if max_seconds is not None and remaining.get('max_seconds') is not None:
                seconds = max(0.0, float(remaining['max_seconds']))
            variables = dict(restored.get('variables') or {})
            self._phase_restored = None
            left = []
            if max_loops is not None:
                left.append(f'{max(0, max_loops - done)} iteration(s)')
            if seconds is not None:
                left.append(secs_to_timestr(round(seconds, 1)))
            message = (f"Loop '{loop_id}' continues after {done} completed "
                       f"iteration(s) of the run saved {restored.get('saved', '?')}: "
                       f"{' and '.join(left)} left.")
        deadline = float('inf') if seconds is None else self._clock() + seconds
        limit = None
        if max_loops is not None:
            limit = max_loops - done
            if limit <= 0:
                limit, deadline = 1, -1.0           # nothing is left: no iteration runs
        self._loops[loop_id] = {'offset': done, 'count': 0, 'deadline': deadline,
                                'max_loops': max_loops}
        self.loop, self.iteration = loop_id, done
        return limit, deadline, variables, message

    def loop_ended(self, loop_id):
        """The loop ran to its end (``Flow Loop End``, right after the WHILE).

        Recorded at once: a stop or a crash later in the same phase would
        otherwise replay the loop's last iteration, because the checkpoint
        still named it as the loop in progress.
        """
        if self.loop != loop_id:
            return
        if loop_id not in self.done_loops:
            self.done_loops.append(loop_id)
        self.loop, self.iteration = None, 0
        self._tracked, self._expect = None, False
        self.write(force=True)

    def loop_of(self, while_data):
        """The id of the tracked loop that ``while_data`` (a WHILE) is, or None.

        ``Flow Loop`` runs right before its WHILE, so the first WHILE to
        iterate after it is the loop's; it is remembered by identity, and a
        WHILE nested in its body is not it.
        """
        if while_data is self._tracked:
            return self.loop
        if self._expect and self.loop:
            self._tracked, self._expect = while_data, False
            return self.loop
        return None

    def iteration_started(self, loop_id):
        """An iteration of ``loop_id`` starts: the ones before it are complete."""
        state = self._loops.get(loop_id)
        if state is None:
            return
        self.loop = loop_id
        self.iteration = state['offset'] + state['count']
        state['count'] += 1
        # Taken here, at the boundary, so that the variables always belong to
        # the iteration count saved with them, whenever the file is written.
        self._snapshot_variables()
        if self.every:
            due = self.iteration % self.every == 0
        else:
            due = time.time() - self._written >= self.MIN_INTERVAL_S
        if due and self.write(force=True):
            # The log shows the progress at the pace of the checkpoint.
            logger.info(f"Iteration {self.iteration + 1} of loop '{loop_id}'; "
                        f"checkpoint written.")

    def _snapshot_variables(self):
        read = self.read_variable
        if read is None:
            return
        values = {}
        for name in self.variable_names:
            try:
                value = read(name)
            except Exception:
                continue
            if value is not None:       # None: not set yet, or not visible from here
                values[name] = value
        self.variables = values

    def _saved_variables(self):
        """The snapshot without the values JSON cannot hold."""
        saved = {}
        for name, value in self.variables.items():
            if json_safe(value):
                saved[name] = value
            elif name not in self._unsaved:
                self._unsaved.add(name)
                logger.warn(f"Variable '{name}' is not saved in the flow checkpoint: "
                            f"its value cannot be written as JSON.")
        return saved

    # ------------------------------------------------------------------ file

    def stop(self):
        """The flow is being stopped: keep the checkpoint. Returns its path."""
        self.stopped = True
        return self.path if self.write(force=True) else None

    def finish(self):
        """The flow ran to its end: the checkpoint has done its job."""
        self.finished = True
        if self.stopped:
            return
        try:
            os.remove(self.path)
        except OSError:
            pass

    def to_dict(self):
        state = self._loops.get(self.loop) if self.loop else None
        remaining = {}
        if state:
            if state['max_loops'] is not None:
                remaining['max_loops'] = max(0, state['max_loops'] - self.iteration)
            if state['deadline'] not in (float('inf'), -1.0):
                remaining['max_seconds'] = round(max(0.0, state['deadline'] - self._clock()), 3)
        return {
            'checkpoint': CHECKPOINT_VERSION,
            'flow': self.source,
            'fingerprint': self.fingerprint,
            'run': {'started': self.started, 'outputdir': self.outputdir},
            'position': {'phase': self.phase, 'loop': self.loop,
                         'iteration': self.iteration, 'done_loops': list(self.done_loops)},
            'remaining': remaining,
            'variables': self._saved_variables(),
            'completed_phases': self.completed,
            'saved': _timestamp(),
        }

    def write(self, force=False):
        """Write the file whole and to the disk: a reader never sees half of
        it, and a power loss right after the write does not lose it."""
        if self.finished:
            return False
        try:
            _write_json(self.path, self.to_dict())
        except OSError as err:
            logger.warn(f"Writing flow checkpoint '{self.path}' failed: {err}")
            return False
        self._written = time.time()
        return True

    def snapshot(self, name=''):
        """Write the state as a named file next to the checkpoint and keep it.

        The file is a checkpoint (a run continues from it with
        ``FLOW_CHECKPOINT``) plus a ``snapshot`` section: when and where it was
        taken and the JSON-safe variables in scope, for inspection.
        """
        label = re.sub(r'[^\w.-]+', '_', str(name or '')).strip('_') \
            or time.strftime('%Y%m%d-%H%M%S')
        stem = self.path[:-len('.checkpoint.json')] \
            if self.path.endswith('.checkpoint.json') else os.path.splitext(self.path)[0]
        path = f'{stem}.snapshot-{label}.json'
        # The variables are the ones taken at the iteration boundary, as in the
        # checkpoint: a run from the snapshot replays the iteration it was
        # taken in from its start. Read again here, mid-iteration, they would
        # carry a counter already advanced into an iteration counted as not done.
        data = self.to_dict()
        data['snapshot'] = {'name': label, 'taken': _timestamp(),
                            'position': self.position_text(),
                            'phase': self.phase, 'loop': self.loop, 'iteration': self.iteration,
                            'scope': self._scope_variables()}
        _write_json(path, data)
        self.snapshots.append({'name': label, 'path': path, 'taken': data['snapshot']['taken'],
                               'position': self.position_text()})
        return path

    def _scope_variables(self):
        """The JSON-safe variables in scope, for inspection (not restored)."""
        try:
            from robot.libraries.BuiltIn import BuiltIn
            variables = BuiltIn().get_variables()
        except Exception:
            return {}
        scope = {}
        for name, value in variables.items():
            if name in _AUTOMATIC_VARIABLES or name.startswith('${flow_') or not json_safe(value):
                continue
            scope[name] = value
        return scope

    def position_text(self):
        if self.loop:
            return f"at iteration {self.iteration + 1} of loop '{self.loop}'"
        if self.phase:
            return f"in phase '{self.phase}'"
        return ''


_AUTOMATIC_VARIABLES = frozenset(
    '${/} ${:} ${\\n} ${SPACE} ${EMPTY} ${True} ${False} ${None} ${null} ${TEMPDIR} ${EXECDIR} '
    '${CURDIR} ${OUTPUT_DIR} ${OUTPUT_FILE} ${LOG_FILE} ${REPORT_FILE} ${DEBUG_FILE} ${LOG_LEVEL} '
    '${PREV_TEST_NAME} ${PREV_TEST_STATUS} ${PREV_TEST_MESSAGE} ${SUITE_NAME} ${SUITE_SOURCE} '
    '${SUITE_DOCUMENTATION} ${TEST_NAME} ${TEST_DOCUMENTATION} ${OPTIONS} &{SUITE_METADATA} '
    '@{TEST_TAGS} ${KEYWORD_STATUS} ${KEYWORD_MESSAGE} ${TEST_STATUS} ${TEST_MESSAGE} '
    '${SUITE_STATUS} ${SUITE_MESSAGE}'.split()
)


def _write_json(path, data):
    """Write ``data`` whole and to the disk: a temporary file, fsync, rename."""
    directory = os.path.dirname(path)
    temp = f'{path}.{os.getpid()}.tmp'
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(temp, 'w', encoding='UTF-8') as file:
        json.dump(data, file, indent=1)
        file.flush()
        os.fsync(file.fileno())
    for attempt in range(50):
        try:
            os.replace(temp, path)
            break
        except PermissionError:     # being read on Windows
            time.sleep(0.01)
    else:
        os.remove(temp)
        raise OSError('the file stays locked')
    _fsync_directory(directory or '.')


def _fsync_directory(directory):
    """Make the rename durable too (POSIX; directories cannot be opened on Windows)."""
    if os.name == 'nt':
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def read_checkpoint(path):
    """Returns ``(data, problem)``; ``data`` is None when there is no usable file."""
    try:
        with open(path, encoding='UTF-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as err:
        return None, f"Checkpoint '{path}' cannot be read ({err})"
    if not isinstance(data, dict) or data.get('checkpoint') != CHECKPOINT_VERSION:
        return None, f"Checkpoint '{path}' has an unknown format"
    return data, None


def fingerprint(source):
    """SHA-1 of the flow file: a checkpoint is only valid for the same plan."""
    try:
        with open(source, 'rb') as file:
            return hashlib.sha1(file.read()).hexdigest()
    except (OSError, TypeError):
        return ''


def checkpoint_name(flow_name):
    """File name of the default checkpoint of a flow."""
    return re.sub(r'[^\w.-]+', '_', flow_name).strip('_') + '.checkpoint.json'


def json_safe(value):
    """Whether ``value`` survives a JSON round trip unchanged."""
    try:
        return json.loads(json.dumps(value)) == value
    except (TypeError, ValueError):
        return False


def _timestamp():
    return time.strftime('%Y-%m-%d %H:%M:%S')
