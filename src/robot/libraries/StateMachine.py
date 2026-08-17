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
    """
    ROBOT_LIBRARY_SCOPE = 'TEST'
    ROBOT_LIBRARY_VERSION = get_version()

    def __init__(self):
        self._states = {}
        self._transitions = []       # evaluated in definition order
        self._checkpoint_variables = []
        self._current = None
        self._visits = {}
        self._stop_requested = False
        self._elapsed_offset = 0     # elapsed seconds restored from checkpoint

    # ------------------------------------------------------------------
    # Definition keywords
    # ------------------------------------------------------------------

    def define_state(self, name, enter=None, during=None, exit=None,
                     timeout=None, on_error=None, final=False):
        """Defines a state.

        - ``enter``/``during``/``exit``: a keyword name, or a list of
          keyword name followed by its arguments (see library docs).
        - ``timeout``: max duration of one visit, e.g. ``10 min`` or ``30s``.
        - ``on_error``: state to go to if a keyword fails or ``timeout`` is
          exceeded.
        - ``final``: entering this state completes the machine.
        """
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
        logger.info(f"Defined state '{name}' (final={final}).")

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
        logger.info(f"Defined transition {source} -> {target} when {cond}.")

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
        logger.info(f"Loaded state machine from '{path}': "
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

    def reset_state_machine(self):
        """Removes all states, transitions and registered variables."""
        self.__init__()

    # ------------------------------------------------------------------
    # Runtime keywords
    # ------------------------------------------------------------------

    def get_current_state(self):
        """Returns the name of the state the machine is currently in."""
        return self._current

    def stop_state_machine(self):
        """Requests a graceful stop.

        Can be called from a state keyword (or from a ``THREAD`` supervisor).
        The machine finishes the current polling round, saves the checkpoint
        and returns from `Run State Machine` successfully.
        """
        self._stop_requested = True
        logger.info('State machine stop requested.')

    def run_state_machine(self, initial, max_duration=None, checkpoint=None,
                          resume='AUTO', poll_interval='1 s'):
        """Runs the machine until a final state, stop request, or failure.

        - ``initial``: name of the start state.
        - ``max_duration``: overall limit, e.g. ``48h`` (see library docs).
        - ``checkpoint``: path of the persistent checkpoint file.
        - ``resume``: ``AUTO`` (default) resumes when a checkpoint exists,
          ``True`` requires one, ``False`` starts from scratch.
        - ``poll_interval``: delay between guard evaluation rounds.
        """
        self._validate_machine(initial)
        builtin = BuiltIn()
        max_duration = timestr_to_secs(max_duration) if max_duration else None
        poll = timestr_to_secs(poll_interval)
        state = self._restore(initial, checkpoint, resume, builtin)
        started = time.time() - self._elapsed_offset
        self._stop_requested = False
        while True:
            state = self._visit(state, builtin, started, max_duration,
                                checkpoint, poll)
            if state is None:
                break
        if checkpoint and os.path.isfile(checkpoint):
            os.remove(checkpoint)
        elapsed = secs_to_timestr(time.time() - started)
        logger.info(f'State machine finished in {elapsed} after '
                    f'{sum(self._visits.values())} state visit(s).')

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------

    def _visit(self, name, builtin, started, max_duration, checkpoint, poll):
        """Runs one visit of state ``name``, returns the next state or None."""
        state = self._states[name]
        self._current = name
        self._visits[name] = self._visits.get(name, 0) + 1
        visit_started = time.time()
        logger.info(f"STATE {name} (visit #{self._visits[name]})",
                    also_console=True)
        error = self._run_state_keyword(builtin, state['enter'],
                                        self._remaining(state, visit_started))
        if error:
            return self._handle_error(state, error, checkpoint, started)
        if state['final']:
            self._save(checkpoint, started)
            return None
        while True:
            self._check_max_duration(started, max_duration, checkpoint)
            if self._stop_requested:
                self._save(checkpoint, started)
                return None
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
            time.sleep(poll)
        error = self._run_state_keyword(builtin, state['exit'],
                                        self._remaining(state, visit_started))
        if error:
            return self._handle_error(state, error, checkpoint, started)
        logger.info(f'TRANSITION {name} -> {target}')
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

    def _handle_error(self, state, error, checkpoint, started):
        if state['on_error'] and state['on_error'] != state['name']:
            logger.warn(f"State '{state['name']}' failed: {error} "
                        f"Continuing in state '{state['on_error']}'.")
            self._current = state['on_error']
            self._save(checkpoint, started)
            return state['on_error']
        self._save(checkpoint, started)
        raise AssertionError(f"State '{state['name']}' failed: {error}")

    def _check_max_duration(self, started, max_duration, checkpoint):
        if max_duration and time.time() - started > max_duration:
            self._save(checkpoint, started)
            limit = secs_to_timestr(max_duration)
            raise AssertionError(
                f'State machine max_duration {limit} exceeded in state '
                f"'{self._current}'. The run can be resumed from the "
                f'checkpoint file.')

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
            data = {'state': self._current,
                    'visits': self._visits,
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
        self._elapsed_offset = data.get('elapsed', 0)
        for name, value in data.get('variables', {}).items():
            builtin.set_test_variable(name, value)
        elapsed = secs_to_timestr(self._elapsed_offset)
        logger.info(f"Resuming state machine from checkpoint "
                    f"'{checkpoint}': state '{state}', {elapsed} elapsed, "
                    f"saved {data.get('saved', 'N/A')}.", also_console=True)
        return state

    # ------------------------------------------------------------------

    @staticmethod
    def _is_truthy(value):
        if isinstance(value, str):
            return value.upper() not in ('FALSE', 'NO', '0', 'NONE', '')
        return bool(value)
