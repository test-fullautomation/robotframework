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
from robot.utils import secs_to_timestr


CONTROL_SIGNAL = 'flow.control'
# A stop is written under this name as well. The store keeps one value per
# name, so a command given right after a stop would replace it before the
# flow has read it; under its own name the stop stays.
STOP_SIGNAL = 'flow.stop'
STATE_SIGNAL = 'flow.state'
PAUSE, RESUME, STOP = 'pause', 'resume', 'stop'
COMMANDS = (PAUSE, RESUME, STOP)
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
        self._poller = None
        self._poller_stop = threading.Event()
        self._since = time.time()
        self._seen = None
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
        started = time.time()
        while self._paused.is_set() and self._stop_reason is None:
            time.sleep(self.SLICE_S)
        waited = secs_to_timestr(round(time.time() - started, 1))
        if self._stop_reason is None:
            logger.info(f'Flow resumed after {waited}.', also_console=_on_main_thread())
        else:
            logger.info(f'Flow stopped while paused ({waited}).')

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

    def start_polling(self, store):
        """Follow the control signal of ``store`` (``get(name)``, ``set(name, value)``)."""
        with self._lock:
            if self._poller is not None:
                return
            self._store = store
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
        while not self._poller_stop.wait(self.POLL_S):
            try:
                self.apply(self._read_command())
            except Exception:       # a store that cannot be read must not end the watch
                pass

    def _read_command(self):
        """The newest command for this process given since it started.

        A stop, once given, is the command: nothing written after it takes
        it back.
        """
        for name in self._names(STOP_SIGNAL):
            entry = self._store.get(name)
            if entry and entry.get('time', 0) >= self._since:
                return {'value': STOP, 'time': entry['time']}
        newest = None
        for name in self._names(CONTROL_SIGNAL):
            entry = self._store.get(name)
            if not entry or entry.get('time', 0) < self._since:
                continue
            if newest is None or entry['time'] >= newest['time']:
                newest = entry
        return newest

    def _names(self, signal):
        """The names a command for this process is written under."""
        return [signal, f'{signal}.{self.rig}'] if self.rig else [signal]

    def apply(self, entry):
        """Act on a control entry ``{'value': command, 'time': seconds}`` once."""
        if not entry:
            return
        key = (entry.get('value'), entry.get('time'))
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

    def reset(self):
        """Forget everything; for tests and for a new run in the same process."""
        self.stop_polling()
        self.resume()
        with self._lock:
            self._paused_total = 0.0
            self._stop_reason = None
            self._since = time.time()
            self._seen = None
            self._store = None
            self.step_mode = False
            self.stopped = False
            self.run = None


# Keywords the builder emits for bookkeeping; step mode does not stop at them.
ITERATION_KEYWORD = 'Flow Iteration'
_INTERNAL_KEYWORDS = ('Flow Phase', 'Flow Loop', ITERATION_KEYWORD)

CONTROL = FlowControl()


def clock():
    """The flow clock: ``time.time()`` without the time spent paused.

    Loop deadlines are written with it (``robot.flow.control.clock() <
    $flow_deadline_x``), so a paused flow does not run out of time.
    """
    return CONTROL.clock()


def _on_main_thread():
    return threading.current_thread() is threading.main_thread()


def _hook(context, step):
    """Called by the body runner before every step."""
    control = CONTROL
    if not (control.step_mode or control._paused.is_set()
            or control._stop_reason is not None) or context.dry_run:
        return
    if control.step_mode:
        control.step(context, step)
    if control._stop_reason is not None and getattr(step, 'name', None) == ITERATION_KEYWORD:
        # Let the iteration be counted first: `Flow Iteration` stops the flow
        # itself, so an iteration that ran to its end is not done again.
        if control._paused.is_set():
            control._wait(None)
        return
    control.pause_point(context)


def install():
    """Register the pause point with the body runner."""
    pausepoint.hook = _hook


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
        self._written = 0.0

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
        if loop_id in self.done_loops:
            self.done_loops.remove(loop_id)
            self._loops[loop_id] = {'offset': 0, 'count': 0, 'deadline': -1.0,
                                    'max_loops': max_loops}
            self.loop, self.iteration = loop_id, 0
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
        if due:
            self.write(force=True)

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
        """Write the file whole: a reader never sees half of it."""
        if self.finished:
            return False
        directory = os.path.dirname(self.path)
        temp = f'{self.path}.{os.getpid()}.tmp'
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(temp, 'w', encoding='UTF-8') as file:
                json.dump(self.to_dict(), file, indent=1)
            for attempt in range(50):
                try:
                    os.replace(temp, self.path)
                    break
                except PermissionError:     # being read on Windows
                    time.sleep(0.01)
            else:
                os.remove(temp)
                raise OSError('the file stays locked')
        except OSError as err:
            logger.warn(f"Writing flow checkpoint '{self.path}' failed: {err}")
            return False
        self._written = time.time()
        return True

    def position_text(self):
        if self.loop:
            return f"at iteration {self.iteration + 1} of loop '{self.loop}'"
        if self.phase:
            return f"in phase '{self.phase}'"
        return ''


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
