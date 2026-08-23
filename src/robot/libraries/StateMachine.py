#  cuongnht add state machine
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

import hashlib
import json
import os
import time

import re

from robot.api import logger
from robot.errors import TimeoutError as RobotTimeoutError
from robot.libraries.BuiltIn import BuiltIn
from robot.running.timeouts import KeywordTimeout
from robot.utils import is_list_like, timestr_to_secs, secs_to_timestr
from robot.version import get_version


class StateMachine:
    """Library for running long tests as a persistent state machine.

    Designed for very long running tests (hours to days). The test is modeled
    as states with enter/during/exit keywords and guarded transitions. The
    engine loop evaluates guards, runs the keywords and - crucially for long
    runs - checkpoints its progress to disk after every transition so that an
    interrupted run (crash, power loss, reboot) can be *resumed* from the last
    state instead of restarting from zero.

    == Defining a machine ==

    | `Define State`      | INIT        | enter=Setup DUT        | timeout=10 min  |
    | `Define State`      | CHARGING    | enter=Start Charging   | on_error=FAULT  |
    | `Define State`      | DISCHARGING | enter=Start Discharging | on_error=FAULT |
    | `Define State`      | FAULT       | enter=Collect Diagnostics | final=True   |
    | `Define State`      | DONE        | final=True             |                 |
    | `Define Transition` | INIT        | CHARGING    | condition=$DUT_READY      |
    | `Define Transition` | CHARGING    | DISCHARGING | condition=$SOC >= 80      |
    | `Define Transition` | DISCHARGING | CHARGING    | condition=$SOC <= 20      |
    | `Define Transition` | DISCHARGING | DONE        | condition=$CYCLES >= 500  |
    | `Checkpoint Variable` | ${/}CYCLES |            |                           |
    | `Run State Machine` | initial=INIT | max_duration=48h | checkpoint=${OUTPUTDIR}${/}sm.json |

    == Conditions ==

    Conditions are Python expressions evaluated with BuiltIn `Evaluate`
    semantics every polling round. Use the special `$name` variable syntax
    (*not* `${name}`) so that the *current* value of the variable is used on
    every evaluation instead of the value at definition time. A transition
    without a condition is always taken; define it last as a default branch.

    Transitions are evaluated in definition order; the first matching one is
    taken.

    == Keywords attached to states ==

    - `enter` is run once when the state is entered.
    - `during` is run on every polling round while waiting for a transition.
    - `exit` is run once when leaving the state.

    Each accepts either a keyword name, or a list whose first item is the
    keyword name and the rest are arguments:

    | ${enter}=       | Create List | Start Charging | fast | ${AMPS} |
    | `Define State`  | CHARGING    | enter=${enter} |      |         |

    They appear in log.html as normal keywords nested inside
    `Run State Machine`, one block per state visit.

    == Defining a machine in a file ==

    `Load State Machine` reads the whole machine from a YAML or JSON file
    (YAML requires the ``pyyaml`` module; JSON works out of the box):

    | `Load State Machine` | ${CURDIR}${/}machine.yaml |
    | `Run State Machine`  | initial=INIT | max_duration=48h |

    machine.yaml:
    | states:
    |   INIT:     {enter: Setup DUT, timeout: 10 min}
    |   CHARGING: {enter: [Start Charging, fast], on_error: FAULT}
    |   FAULT:    {enter: Collect Diagnostics, final: true}
    |   DONE:     {final: true}
    | transitions:
    |   - INIT -> CHARGING when $DUT_READY
    |   - {source: CHARGING, target: DONE, condition: $CYCLES >= 500}
    | checkpoint_variables: ['${CYCLES}']

    Transitions support the compact ``SOURCE -> TARGET when CONDITION``
    string form (``when ...`` optional) and the explicit mapping form; they
    are evaluated in file order. File definitions and `Define State` /
    `Define Transition` keywords can be freely combined.

    == Error handling ==

    If a state keyword fails and the state defines `on_error`, the machine
    transitions to that state (typically a diagnostics/FAULT state) instead
    of failing the test. Without `on_error` the failure is propagated and the
    machine stops (checkpoint is saved first, so the run can be resumed).
    The failing state's `exit` keyword is *not* run when routing to
    `on_error` - the state did not complete. If `on_error` routing itself
    forms a cycle (e.g. FAULT and RECOVER failing into each other) without
    any successful transition in between, the machine fails instead of
    looping; a normal guarded transition resets the cycle tracking.

    == Checkpoint and resume ==

    With `checkpoint=<file>` the engine atomically writes a JSON file after
    every transition containing the current state, visit counters, elapsed
    time and the values of variables registered with `Checkpoint Variable`.
    On the next run with `resume=AUTO` (default) the machine continues from
    the checkpointed state: registered variables are restored and the state's
    `enter` keyword is executed again - design enter keywords to be
    *idempotent*. Use `resume=False` to ignore an existing checkpoint or
    `resume=True` to require one. The checkpoint file is removed when the
    machine finishes successfully.

    == Timeouts and the watchdog ==

    - `max_duration` given to `Run State Machine` bounds the whole run. When
      exceeded, the checkpoint is saved and the test fails with a message
      telling that the run can be resumed.
    - `timeout` given to `Define State` bounds one visit to that state. When
      exceeded, the machine goes to the state's `on_error` target, or fails
      if there is none.

    State ``timeout`` is enforced preemptively: enter/during/exit keywords
    run under a Robot Framework keyword timeout limited to the remaining
    visit budget, so also a stuck keyword is interrupted (RF timeouts cannot
    interrupt code blocking inside C, but RF's own keywords like ``Sleep``
    are interruptible). ``max_duration`` is checked between keyword calls;
    for hard supervision of the whole run combine with a ``Watchdog`` in a
    ``THREAD`` block.

    == Multiple machines ==

    All keywords accept ``machine=<name>`` (default ``DEFAULT``), so several
    fully isolated machines - own states, transitions, checkpoint variables,
    statistics - can live in one test. They can run sequentially, or
    concurrently by giving each machine its own ``THREAD`` block:

    | `Define State`      | ...  | machine=CHARGER |
    | THREAD | CHARGER_SM | True |
    |        | `Run State Machine` | initial=INIT | machine=CHARGER |
    | END    |
    | `Run State Machine` | initial=INIT | machine=MAIN |
    | Wait For Thread     | CHARGER_SM |

    Give concurrently running machines separate checkpoint files. Log
    messages of non-default machines are prefixed with ``[<name>]``.

    *Variable scoping caveat:* transition guards of a machine running inside
    a ``THREAD`` block do not see variables set by keywords in that same
    thread (thread variable scoping); machines running in the main flow do
    see variables fed by worker threads. Control a worker-hosted machine
    with `Stop State Machine` from the main flow, or base its guards on
    variables set before the thread starts.

    == Library scope ==

    The library has ``SUITE`` scope: machine definitions live for the whole
    suite and are shared by all its tests (nested suites get own instances).
    This enables two patterns:

    - Define the machine once in the *suite setup* (e.g. with
      `Load State Machine`) and run it from any test.
    - A machine running in a SUITE-scoped ``THREAD`` (``daemon=False``)
      stays reachable in later tests: `Stop State Machine`,
      `Get Current State` and `Get State Statistics` address the same
      machine that the worker thread is running.

    Consequence: tests that define the *same* machine again must clean up
    with ``Test Teardown    Reset State Machine`` (or use unique machine
    names) - defining an already existing state fails.

    A machine whose hosting ``THREAD`` is stopped at its scope end (test or
    suite boundary) stops *gracefully*: it saves its checkpoint and returns
    from `Run State Machine` instead of routing into ``on_error``.
    """
    ROBOT_LIBRARY_SCOPE = 'SUITE'
    ROBOT_LIBRARY_VERSION = get_version()

    def __init__(self):
        self._machines = {}

    def _machine(self, machine):
        name = str(machine)
        if name not in self._machines:
            self._machines[name] = _Engine(name)
        return self._machines[name]

    def define_state(self, name, enter=None, during=None, exit=None,
                     timeout=None, on_error=None, final=False,
                     machine='DEFAULT'):
        """Defines a state.

        - ``enter``/``during``/``exit``: a keyword name, or a list of
          keyword name followed by its arguments (see library docs).
        - ``timeout``: max duration of one visit, e.g. ``10 min`` or ``30s``.
        - ``on_error``: state to go to if a keyword fails or ``timeout`` is
          exceeded.
        - ``final``: entering this state completes the machine.
        - ``machine``: the machine this state belongs to.
        """
        self._machine(machine).define_state(name, enter, during, exit,
                                            timeout, on_error, final)

    def define_transition(self, source, target, condition=None,
                          machine='DEFAULT'):
        """Defines a transition from ``source`` to ``target``.

        ``condition`` is a Python expression evaluated with `Evaluate`
        semantics; use ``$name`` syntax to access variables at run time.
        Omitting the condition makes the transition unconditional (default
        branch); define it after the guarded ones.
        """
        self._machine(machine).define_transition(source, target, condition)

    def checkpoint_variable(self, name, machine='DEFAULT'):
        """Registers a variable (e.g. ``\\${CYCLES}``) to be persisted.

        Registered variables are stored in the machine's checkpoint file
        after every transition and restored as test variables when the run
        is resumed. Values must be JSON serializable; an existing value is
        validated immediately so mistakes fail fast.
        """
        self._machine(machine).checkpoint_variable(name)

    def load_state_machine(self, path, machine='DEFAULT'):
        """Loads states, transitions and checkpoint variables from a file.

        The file format is selected by extension: ``.json`` is parsed with
        the standard library, everything else as YAML (requires the
        ``pyyaml`` module). See the library documentation for the file
        structure. Definitions from the file go through the same validation
        as `Define State` / `Define Transition` and can be combined with
        them; transitions keep their file order.
        """
        self._machine(machine).load_state_machine(path)

    def run_state_machine(self, initial, max_duration=None, checkpoint=None,
                          resume='AUTO', poll_interval='1 s', max_visits=None,
                          machine='DEFAULT'):
        """Runs the machine until a final state, stop request, or failure.

        - ``initial``: name of the start state.
        - ``max_duration``: overall limit, e.g. ``48h`` (see library docs).
        - ``checkpoint``: path of the persistent checkpoint file.
        - ``resume``: ``AUTO`` (default) resumes when a checkpoint exists,
          ``True`` requires one, ``False`` starts from scratch.
        - ``poll_interval``: delay between guard evaluation rounds.
        - ``max_visits``: fail when the total number of state visits reaches
          this limit - a guard against transition ping-pong caused by wrong
          conditions. The counter includes visits restored from a checkpoint.
        - ``machine``: the machine to run.

        A per-state statistics summary (visits, time in state) is logged when
        the machine ends, also on failure; see `Get State Statistics`.
        """
        self._machine(machine).run_state_machine(initial, max_duration,
                                                 checkpoint, resume,
                                                 poll_interval, max_visits)

    def get_current_state(self, machine='DEFAULT'):
        """Returns the name of the state the machine is currently in."""
        return self._machine(machine).get_current_state()

    def get_state_statistics(self, machine='DEFAULT'):
        """Returns ``{state: {'visits': int, 'elapsed': seconds}}``.

        Covers all states visited so far, including visits and times restored
        from a checkpoint when the run was resumed.
        """
        return self._machine(machine).get_state_statistics()

    def stop_state_machine(self, machine='DEFAULT'):
        """Requests a graceful stop of the machine.

        Can be called from a state keyword (or from a ``THREAD`` supervisor).
        The machine finishes the current polling round, saves the checkpoint
        and returns from `Run State Machine` successfully.
        """
        self._machine(machine).stop_state_machine()

    def reset_state_machine(self, machine=None):
        """Removes machine definitions.

        With ``machine`` only that machine is removed; without an argument
        ALL machines are removed. Still running machines are asked to stop
        gracefully first.
        """
        names = [str(machine)] if machine is not None else list(self._machines)
        for name in names:
            engine = self._machines.pop(name, None)
            if engine is not None:
                engine._stop_requested = True


def _thread_scope_stop_requested():
    """True if the current worker THREAD was asked to stop (scope ended)."""
    from robot.running.context import EXECUTION_CONTEXTS
    ctx = EXECUTION_CONTEXTS.current
    checker = getattr(ctx, 'thread_stop_requested', None) if ctx else None
    return bool(checker and checker())


# Checkpoint files of currently running machines: abspath -> machine name.
# Guards against two concurrently running machines sharing one file.
_active_checkpoints = {}


class _Engine:
    """One state machine: definitions, engine loop, checkpointing."""

    def __init__(self, name='DEFAULT'):
        self.name = name
        self._label = '' if name == 'DEFAULT' else f'[{name}] '
        self._states = {}
        self._transitions = []       # evaluated in definition order
        self._checkpoint_variables = []
        self._current = None
        self._visits = {}
        self._visit_times = {}       # state -> accumulated seconds in state
        self._current_visit = None   # (state name, start time) while visiting
        self._error_chain = []       # states reached via on_error routing
        self._stop_requested = False
        self._reached_final = False
        self._elapsed_offset = 0     # elapsed seconds restored from checkpoint

    # ------------------------------------------------------------------
    # Definitions
    # ------------------------------------------------------------------

    def define_state(self, name, enter=None, during=None, exit=None,
                     timeout=None, on_error=None, final=False):
        if name in self._states:
            raise RuntimeError(f"State '{name}' is already defined.")
        self._states[name] = {
            'name': name,
            'enter': self._normalize_keyword(name, 'enter', enter),
            'during': self._normalize_keyword(name, 'during', during),
            'exit': self._normalize_keyword(name, 'exit', exit),
            'timeout': timestr_to_secs(timeout) if timeout else None,
            'on_error': on_error,
            'final': self._is_truthy(final),
        }
        logger.info(f"{self._label}Defined state '{name}' (final={final}).")

    @staticmethod
    def _normalize_keyword(state, what, value):
        """Normalizes a state keyword to ``(name, args)`` or None."""
        if value is None or value == '':
            return None
        if is_list_like(value):
            items = list(value)
            if not items:
                return None
            return (str(items[0]), tuple(items[1:]))
        return (str(value), ())

    def define_transition(self, source, target, condition=None):
        """Defines a transition from ``source`` to ``target``.

        ``condition`` is a Python expression evaluated with `Evaluate`
        semantics; use ``$name`` syntax to access variables at run time.
        Omitting the condition makes the transition unconditional (default
        branch); define it after the guarded ones.
        """
        self._transitions.append({'source': source, 'target': target,
                                  'condition': condition})
        cond = condition or '<always>'
        logger.info(f"{self._label}Defined transition {source} -> {target} "
                    f"when {cond}.")

    def checkpoint_variable(self, name):
        """Registers a variable (e.g. ``\\${CYCLES}``) to be persisted.

        Registered variables are stored in the checkpoint file after every
        transition and restored as test variables when the run is resumed.
        Values must be JSON serializable; if the variable already has a
        value, that is validated immediately so mistakes fail fast instead
        of crashing the engine at the first transition.
        """
        name = str(name)
        value = BuiltIn().get_variable_value(name)
        if value is not None and not self._is_serializable(value):
            raise RuntimeError(
                f"Value of checkpoint variable '{name}' is not JSON "
                f"serializable: {type(value).__name__}.")
        if name not in self._checkpoint_variables:
            self._checkpoint_variables.append(name)

    @staticmethod
    def _is_serializable(value):
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            return False
        return True

    def load_state_machine(self, path):
        """Loads states, transitions and checkpoint variables from a file.

        The file format is selected by extension: ``.json`` is parsed with
        the standard library, everything else as YAML (requires the
        ``pyyaml`` module). See the library documentation for the file
        structure. Definitions from the file go through the same validation
        as `Define State` / `Define Transition` and can be combined with
        them; transitions keep their file order.
        """
        data = self._read_machine_file(path)
        if not isinstance(data, dict):
            raise RuntimeError(f"Machine file '{path}' must contain a "
                               f"mapping, got {type(data).__name__}.")
        known = {'enter', 'during', 'exit', 'timeout', 'on_error', 'final'}
        for name, options in (data.get('states') or {}).items():
            options = options or {}
            if not isinstance(options, dict):
                raise RuntimeError(f"State '{name}': options must be a "
                                   f"mapping, got {type(options).__name__}.")
            unknown = set(options) - known
            if unknown:
                raise RuntimeError(f"State '{name}' has unknown option(s) "
                                   f"{', '.join(sorted(unknown))}.")
            self.define_state(str(name), **options)
        for item in (data.get('transitions') or []):
            source, target, condition = self._parse_transition(item)
            self.define_transition(source, target, condition)
        for name in (data.get('checkpoint_variables') or []):
            self.checkpoint_variable(name)
        logger.info(f"{self._label}Loaded state machine from '{path}': "
                    f"{len(self._states)} state(s), "
                    f"{len(self._transitions)} transition(s).")

    def _read_machine_file(self, path):
        if not os.path.isfile(path):
            raise RuntimeError(f"Machine file '{path}' does not exist.")
        with open(path, encoding='UTF-8') as f:
            text = f.read()
        if str(path).lower().endswith('.json'):
            return json.loads(text)
        try:
            import yaml
        except ImportError:
            raise RuntimeError(
                f"Using YAML machine file '{path}' requires the 'pyyaml' "
                f"module to be installed (pip install pyyaml). JSON files "
                f"work without extra dependencies.")
        return yaml.safe_load(text)

    _TRANSITION_RE = re.compile(r'^\s*(\S+)\s*->\s*(\S+?)\s*'
                                r'(?:\s+when\s+(.+?))?\s*$')

    def _parse_transition(self, item):
        if isinstance(item, dict):
            unknown = set(item) - {'source', 'target', 'condition'}
            if unknown:
                raise RuntimeError(f"Transition has unknown key(s) "
                                   f"{', '.join(sorted(unknown))}.")
            try:
                return (str(item['source']), str(item['target']),
                        item.get('condition'))
            except KeyError as err:
                raise RuntimeError(f"Transition is missing key {err}.")
        match = self._TRANSITION_RE.match(str(item))
        if not match:
            raise RuntimeError(
                f"Invalid transition {item!r}. Expected 'SOURCE -> TARGET' "
                f"or 'SOURCE -> TARGET when CONDITION'.")
        return match.group(1), match.group(2), match.group(3)

    # ------------------------------------------------------------------
    # Runtime
    # ------------------------------------------------------------------

    def get_current_state(self):
        return self._current

    def stop_state_machine(self):
        self._stop_requested = True
        logger.info(f'{self._label}State machine stop requested.')

    def run_state_machine(self, initial, max_duration=None, checkpoint=None,
                          resume='AUTO', poll_interval='1 s', max_visits=None):
        """Runs the machine until a final state, stop request, or failure.

        - ``initial``: name of the start state.
        - ``max_duration``: overall limit, e.g. ``48h`` (see library docs).
        - ``checkpoint``: path of the persistent checkpoint file.
        - ``resume``: ``AUTO`` (default) resumes when a checkpoint exists,
          ``True`` requires one, ``False`` starts from scratch.
        - ``poll_interval``: delay between guard evaluation rounds.
        - ``max_visits``: fail when the total number of state visits reaches
          this limit — a guard against transition ping-pong caused by wrong
          conditions. The counter includes visits restored from a checkpoint.

        A per-state statistics summary (visits, time in state) is logged when
        the machine ends, also on failure; see `Get State Statistics`.
        """
        self._validate_machine(initial)
        builtin = BuiltIn()
        max_duration = timestr_to_secs(max_duration) if max_duration else None
        max_visits = int(max_visits) if max_visits else None
        poll = timestr_to_secs(poll_interval)
        state = self._restore(initial, checkpoint, resume, builtin)
        started = time.time() - self._elapsed_offset
        self._stop_requested = False
        self._error_chain = []
        self._reached_final = False
        cp_key = os.path.abspath(checkpoint) if checkpoint else None
        if cp_key is not None:
            holder = _active_checkpoints.get(cp_key)
            if holder is not None:
                raise RuntimeError(
                    f"Checkpoint file '{checkpoint}' is already used by the "
                    f"running state machine '{holder}'. Give concurrently "
                    f"running machines separate checkpoint files.")
            _active_checkpoints[cp_key] = self.name
        try:
            try:
                while True:
                    if max_visits and sum(self._visits.values()) >= max_visits:
                        self._save(checkpoint, started)
                        raise AssertionError(
                            f'{self._label}State machine max_visits {max_visits} '
                            f"reached in state '{self._current}'. Check the "
                            f'transition conditions for ping-pong loops. The run '
                            f'can be resumed from the checkpoint file.')
                    state = self._visit(state, builtin, started, max_duration,
                                        checkpoint, poll)
                    if state is None:
                        break
            finally:
                self._log_statistics()
        finally:
            if cp_key is not None:
                _active_checkpoints.pop(cp_key, None)
        if self._reached_final and checkpoint and os.path.isfile(checkpoint):
            # Only a completed machine removes its checkpoint; a stopped one
            # keeps it so the run stays resumable.
            os.remove(checkpoint)
        elapsed = secs_to_timestr(time.time() - started)
        logger.info(f'{self._label}State machine finished in {elapsed} after '
                    f'{sum(self._visits.values())} state visit(s).')

    def get_state_statistics(self):
        """Returns ``{state: {'visits': int, 'elapsed': seconds}}``.

        Covers all states visited so far, including visits and times restored
        from a checkpoint when the run was resumed.
        """
        return {name: {'visits': count,
                       'elapsed': self._visit_times.get(name, 0)}
                for name, count in self._visits.items()}

    def _log_statistics(self):
        if not self._visits:
            return
        width = max(len(name) for name in self._visits)
        lines = [f'{name.ljust(width)}  {count:>6}  '
                 f'{secs_to_timestr(self._visit_times.get(name, 0))}'
                 for name, count in self._visits.items()]
        header = f"{'STATE'.ljust(width)}  VISITS  TIME IN STATE"
        logger.info(f'{self._label}State machine statistics:\n' + header
                    + '\n' + '\n'.join(lines))

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------

    def _visit(self, name, builtin, started, max_duration, checkpoint, poll):
        """Runs one visit of state ``name``, returns the next state or None."""
        visit_started = time.time()
        self._current_visit = (name, visit_started)
        try:
            return self._do_visit(name, builtin, started, max_duration,
                                  checkpoint, poll, visit_started)
        finally:
            # Accumulate time-in-state on every outcome, also failures.
            self._current_visit = None
            self._visit_times[name] = (self._visit_times.get(name, 0)
                                       + time.time() - visit_started)

    def _do_visit(self, name, builtin, started, max_duration, checkpoint,
                  poll, visit_started):
        state = self._states[name]
        self._current = name
        self._visits[name] = self._visits.get(name, 0) + 1
        logger.info(f"{self._label}STATE {name} (visit #{self._visits[name]})",
                    also_console=True)
        error = self._run_state_keyword(builtin, state['enter'],
                                        self._remaining(state, visit_started))
        if error:
            return self._handle_error(state, error, checkpoint, started)
        if state['final']:
            self._error_chain = []
            self._reached_final = True
            self._save(checkpoint, started)
            return None
        while True:
            if self._stop_requested or _thread_scope_stop_requested():
                if not self._stop_requested:
                    logger.info(f"{self._label}State machine stopped because "
                                f"its thread's scope ended.")
                self._save(checkpoint, started)
                return None
            self._check_max_duration(started, max_duration, checkpoint)
            target = self._find_transition(name, builtin)
            if target:
                break
            if state['timeout'] and time.time() - visit_started > state['timeout']:
                timeout = secs_to_timestr(state['timeout'])
                return self._handle_error(
                    state, f"State '{name}' timeout {timeout} exceeded.",
                    checkpoint, started)
            error = self._run_state_keyword(builtin, state['during'],
                                            self._remaining(state, visit_started))
            if error:
                return self._handle_error(state, error, checkpoint, started)
            self._sleep(poll)
        error = self._run_state_keyword(builtin, state['exit'],
                                        self._remaining(state, visit_started))
        if error:
            return self._handle_error(state, error, checkpoint, started)
        logger.info(f'{self._label}TRANSITION {name} -> {target}')
        # A normal guarded transition ends a possible on_error incident.
        self._error_chain = []
        self._current = target
        self._save(checkpoint, started)
        return target

    def _find_transition(self, source, builtin):
        for tr in self._transitions:
            if tr['source'] != source:
                continue
            if tr['condition'] is None:
                return tr['target']
            if self._is_truthy(builtin.evaluate(tr['condition'])):
                return tr['target']
        return None

    def _run_state_keyword(self, builtin, keyword, timeout=None):
        """Runs a state keyword ``(name, args)``, returns an error or None.

        With ``timeout`` (seconds, the remaining visit budget) the keyword is
        interrupted preemptively using Robot Framework's keyword timeout
        machinery. Like all RF timeouts this cannot interrupt keywords that
        block inside C code; RF's own keywords (e.g. ``Sleep``) are
        interruptible.
        """
        if not keyword:
            return None
        name, args = keyword
        try:
            if timeout is not None:
                if timeout <= 0:
                    return 'State timeout exceeded.'
                kw_timeout = KeywordTimeout()
                kw_timeout.string = secs_to_timestr(timeout)
                kw_timeout.secs = timeout
                kw_timeout.start()
                status, message = kw_timeout.run(
                    builtin.run_keyword_and_ignore_error, args=(name,) + tuple(args))
            else:
                status, message = builtin.run_keyword_and_ignore_error(name, *args)
        except RobotTimeoutError as err:
            return str(err)
        return None if status == 'PASS' else message

    def _remaining(self, state, visit_started):
        """Remaining visit time budget in seconds, or None if unlimited."""
        if state['timeout'] is None:
            return None
        return state['timeout'] - (time.time() - visit_started)

    def _sleep(self, seconds):
        """Polling sleep that stays responsive to stop requests."""
        end = time.time() + seconds
        while not (self._stop_requested or _thread_scope_stop_requested()):
            remaining = end - time.time()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    def _handle_error(self, state, error, checkpoint, started):
        if _thread_scope_stop_requested():
            # The hosting THREAD's scope ended: the cooperative thread stop
            # surfaces as a keyword failure inside the state keyword. Treat
            # it as a graceful stop, not as a state error.
            logger.info(f"{self._label}State machine stopped because its "
                        f"thread's scope ended.")
            self._save(checkpoint, started)
            return None
        target = state['on_error']
        if target and target != state['name']:
            if target in self._error_chain:
                chain = ' -> '.join([state['name']]
                                    + self._error_chain[self._error_chain.index(target):]
                                    + [target])
                self._save(checkpoint, started)
                raise AssertionError(
                    f"on_error routing cycle detected ({chain}) after "
                    f"state '{state['name']}' failed: {error}")
            self._error_chain.append(target)
            logger.warn(f"{self._label}State '{state['name']}' failed: "
                        f"{error} Continuing in state '{target}'.")
            self._current = target
            self._save(checkpoint, started)
            return target
        self._save(checkpoint, started)
        raise AssertionError(f"State '{state['name']}' failed: {error}")

    def _check_max_duration(self, started, max_duration, checkpoint):
        if max_duration and time.time() - started > max_duration:
            self._save(checkpoint, started)
            limit = secs_to_timestr(max_duration)
            raise AssertionError(
                f'{self._label}State machine max_duration {limit} exceeded '
                f"in state '{self._current}'. The run can be resumed from "
                f'the checkpoint file.')

    def _validate_machine(self, initial):
        if initial not in self._states:
            raise RuntimeError(f"Initial state '{initial}' is not defined.")
        for tr in self._transitions:
            for end in (tr['source'], tr['target']):
                if end not in self._states:
                    raise RuntimeError(f"Transition {tr['source']} -> "
                                       f"{tr['target']} uses undefined "
                                       f"state '{end}'.")
        for state in self._states.values():
            if state['on_error'] and state['on_error'] not in self._states:
                raise RuntimeError(f"State '{state['name']}' has undefined "
                                   f"on_error state '{state['on_error']}'.")
            has_out = any(tr['source'] == state['name']
                          for tr in self._transitions)
            if not state['final'] and not has_out:
                raise RuntimeError(f"Non-final state '{state['name']}' has "
                                   f"no outgoing transitions.")

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def _save(self, checkpoint, started):
        if not checkpoint:
            return
        try:
            builtin = BuiltIn()
            variables = {}
            for name in self._checkpoint_variables:
                value = builtin.get_variable_value(name)
                if not self._is_serializable(value):
                    logger.warn(f"Checkpoint variable '{name}' value is not "
                                f"JSON serializable and was not saved.")
                    continue
                variables[name] = value
            # Include the in-flight time of a visit that is still running:
            # mid-visit checkpoints (max_duration, error routing, stop) are
            # written before the visit's own accounting runs.
            visit_times = dict(self._visit_times)
            if self._current_visit is not None:
                name, visit_started = self._current_visit
                visit_times[name] = (visit_times.get(name, 0)
                                     + time.time() - visit_started)
            data = {'state': self._current,
                    'visits': self._visits,
                    'visit_times': visit_times,
                    'elapsed': time.time() - started,
                    'variables': variables,
                    'machine': self._machine_fingerprint(),
                    'saved': time.strftime('%Y-%m-%d %H:%M:%S')}
            tmp = checkpoint + '.tmp'
            with open(tmp, 'w', encoding='UTF-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, checkpoint)   # atomic also on Windows
            logger.debug(f"Checkpoint saved to '{checkpoint}'.")
        except Exception as err:
            # A failing checkpoint must never kill a running machine; it
            # only degrades resumability.
            logger.warn(f"Saving checkpoint to '{checkpoint}' failed: {err}")

    def _machine_fingerprint(self):
        """Stable hash of the machine definition for resume validation."""
        data = {'states': self._states, 'transitions': self._transitions}
        text = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha1(text.encode('UTF-8')).hexdigest()

    def _restore(self, initial, checkpoint, resume, builtin):
        resume = str(resume).upper()
        exists = checkpoint and os.path.isfile(checkpoint)
        if resume == 'TRUE' and not exists:
            raise RuntimeError(f"resume=True but checkpoint file "
                               f"'{checkpoint}' does not exist.")
        if resume == 'FALSE' or not exists:
            self._visits = {}
            self._visit_times = {}
            self._elapsed_offset = 0
            return initial
        with open(checkpoint, encoding='UTF-8') as f:
            data = json.load(f)
        state = data['state']
        if state not in self._states:
            raise RuntimeError(f"Checkpointed state '{state}' is not defined "
                               f"in the current machine.")
        fingerprint = data.get('machine')
        if fingerprint and fingerprint != self._machine_fingerprint():
            logger.warn(f"Checkpoint '{checkpoint}' was created with a "
                        f"different state machine definition. Resuming "
                        f"anyway, but visit counters and saved variables "
                        f"may not match the current machine.")
        self._visits = data.get('visits', {})
        self._visit_times = data.get('visit_times', {})
        self._elapsed_offset = data.get('elapsed', 0)
        for name, value in data.get('variables', {}).items():
            builtin.set_test_variable(name, value)
        elapsed = secs_to_timestr(self._elapsed_offset)
        logger.info(f"{self._label}Resuming state machine from checkpoint "
                    f"'{checkpoint}': state '{state}', {elapsed} elapsed, "
                    f"saved {data.get('saved', 'N/A')}.", also_console=True)
        return state

    # ------------------------------------------------------------------

    @staticmethod
    def _is_truthy(value):
        if isinstance(value, str):
            return value.upper() not in ('FALSE', 'NO', '0', 'NONE', '')
        return bool(value)
