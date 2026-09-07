#  cuongnht add watchdog
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

import threading
import time

from robot.api import logger
from robot.libraries.BuiltIn import BuiltIn
from robot.utils import timestr_to_secs, secs_to_timestr
from robot.version import get_version


class Watchdog:
    """Library for supervising long running tests from a ``THREAD`` block.

    A watchdog is a supervisor that runs in parallel with the actual test
    (using the ``THREAD`` keyword) and watches for three things:

    - *Heartbeat*: it periodically logs a proof-of-life message so a long
      quiet run is visibly still alive in the console and the log.
    - *Stall detection*: the supervised code is expected to call
      `Feed Watchdog` regularly. If no feed arrives within
      ``stall_timeout``, the watchdog *fires*.
    - *Deadline*: if ``max_duration`` is exceeded, the watchdog fires.

    When the watchdog fires it runs the configured ``on_timeout`` keyword
    (e.g. one that collects diagnostics or requests some component to stop)
    and records the reason. Because keyword failures inside a ``THREAD`` do
    not fail the test, the main test flow must check the outcome itself with
    `Watchdog Should Not Have Fired`, typically in the test teardown.

    This library is independent of what it supervises: the only contract is
    that the supervised code feeds the watchdog and that the ``on_timeout``
    keyword knows how to react. It can supervise a state machine, a
    measurement loop, a device flashing procedure or anything else.

    == Typical usage ==

    | `Configure Watchdog` | max_duration=48h | stall_timeout=10 min | heartbeat=5 min | on_timeout=Collect Diagnostics |
    | THREAD | SUPERVISOR | True |
    |    | `Run Watchdog` |
    | END |
    | # ... long running test steps that call `Feed Watchdog` ... |
    | `Stop Watchdog` |
    | `Watchdog Should Not Have Fired` |

    Multiple independent watchdogs can be used by giving them names.

    == Watchdog states ==

    `Get Watchdog Status` returns one of ``CONFIGURED`` (not started yet),
    ``RUNNING``, ``STOPPED`` (stopped gracefully with `Stop Watchdog`) or
    ``FIRED`` (stall or deadline detected; the watchdog loop has ended).
    """
    ROBOT_LIBRARY_SCOPE = 'GLOBAL'
    ROBOT_LIBRARY_VERSION = get_version()

    _POLL = 0.2   # internal polling granularity in seconds

    def __init__(self):
        self._watchdogs = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure_watchdog(self, name='DEFAULT', max_duration=None,
                           stall_timeout=None, heartbeat='1 min',
                           on_timeout=None, heartbeat_keyword=None):
        """Creates a watchdog. Must be called before starting its ``THREAD``.

        - ``max_duration``: overall deadline, e.g. ``48h``. Disabled if not
          given.
        - ``stall_timeout``: maximum allowed time between `Feed Watchdog`
          calls, e.g. ``10 min``. Disabled if not given.
        - ``heartbeat``: interval of proof-of-life log messages. Use ``NONE``
          to disable.
        - ``on_timeout``: keyword run (in the watchdog thread) when the
          watchdog fires.
        - ``heartbeat_keyword``: keyword run on every heartbeat, e.g. for
          logging system statistics. Its failures are logged and ignored.
        """
        if name in self._watchdogs and self._watchdogs[name].state == 'RUNNING':
            raise RuntimeError(f"Watchdog '{name}' is already running.")
        if heartbeat and str(heartbeat).upper() == 'NONE':
            heartbeat = None
        self._watchdogs[name] = _Watchdog(
            name,
            timestr_to_secs(max_duration) if max_duration else None,
            timestr_to_secs(stall_timeout) if stall_timeout else None,
            timestr_to_secs(heartbeat) if heartbeat else None,
            on_timeout, heartbeat_keyword)
        logger.info(f"Configured watchdog '{name}' (max_duration="
                    f"{max_duration or '-'}, stall_timeout="
                    f"{stall_timeout or '-'}, heartbeat={heartbeat or '-'}).")

    def reset_watchdogs(self):
        """Stops all running watchdogs and removes all configurations."""
        for wd in self._watchdogs.values():
            wd.stop.set()
        self._watchdogs = {}

    # ------------------------------------------------------------------
    # Supervision loop (run this inside a THREAD block)
    # ------------------------------------------------------------------

    def run_watchdog(self, name='DEFAULT'):
        """Runs the supervision loop. Intended to run inside a ``THREAD``.

        Blocks until the watchdog fires or `Stop Watchdog` is called, so run
        it in its own ``THREAD`` block to supervise the main flow.
        """
        wd = self._get(name)
        if wd.state == 'RUNNING':
            raise RuntimeError(f"Watchdog '{name}' is already running.")
        wd.start()
        logger.info(f"Watchdog '{name}' started.", also_console=True)
        while not wd.stop.wait(self._POLL):
            now = time.monotonic()
            if wd.heartbeat and now >= wd.next_heartbeat:
                self._heartbeat(wd, now)
            reason = wd.check(now)
            if reason:
                self._fire(wd, reason)
                return
        wd.state = 'STOPPED'
        elapsed = secs_to_timestr(time.monotonic() - wd.started)
        logger.info(f"Watchdog '{name}' stopped after {elapsed}, "
                    f"{wd.feeds} feed(s).")

    def _heartbeat(self, wd, now):
        elapsed = secs_to_timestr(now - wd.started)
        logger.info(f"WATCHDOG {wd.name} heartbeat: {elapsed} elapsed, "
                    f"{wd.feeds} feed(s).", also_console=True)
        if wd.heartbeat_keyword:
            try:
                BuiltIn().run_keyword(wd.heartbeat_keyword)
            except Exception as err:
                logger.warn(f"Watchdog '{wd.name}' heartbeat keyword "
                            f"'{wd.heartbeat_keyword}' failed: {err}")
        wd.next_heartbeat = now + wd.heartbeat

    def _fire(self, wd, reason):
        wd.state = 'FIRED'
        wd.reason = reason
        logger.warn(f"WATCHDOG {wd.name} FIRED: {reason}")
        if wd.on_timeout:
            try:
                BuiltIn().run_keyword(wd.on_timeout)
            except Exception as err:
                logger.warn(f"Watchdog '{wd.name}' on_timeout keyword "
                            f"'{wd.on_timeout}' failed: {err}")

    # ------------------------------------------------------------------
    # Keywords for the supervised code
    # ------------------------------------------------------------------

    def feed_watchdog(self, name='DEFAULT'):
        """Signals that the supervised code is making progress.

        Call this regularly from the main flow (e.g. once per cycle of a
        long loop). If ``stall_timeout`` is configured and no feed arrives
        in time, the watchdog fires.
        """
        wd = self._get(name)
        wd.feed()
        logger.debug(f"Watchdog '{name}' fed.")

    def stop_watchdog(self, name='DEFAULT', timeout='10 s'):
        """Stops the watchdog gracefully and waits until its loop has ended.

        Fails if the watchdog loop does not end within ``timeout``.
        """
        wd = self._get(name)
        wd.stop.set()
        max_wait = time.monotonic() + timestr_to_secs(timeout)
        while wd.state == 'RUNNING':
            if time.monotonic() > max_wait:
                raise AssertionError(f"Watchdog '{name}' did not stop in "
                                     f"{timeout}.")
            time.sleep(self._POLL / 2)

    def get_watchdog_status(self, name='DEFAULT'):
        """Returns ``CONFIGURED``, ``RUNNING``, ``STOPPED`` or ``FIRED``."""
        return self._get(name).state

    def watchdog_should_not_have_fired(self, name='DEFAULT'):
        """Fails if the watchdog has fired.

        Because failures inside a ``THREAD`` block do not fail the test,
        call this in the main flow - typically in the test teardown - to
        turn a fired watchdog into a test failure.
        """
        wd = self._get(name)
        if wd.state == 'FIRED':
            raise AssertionError(f"Watchdog '{name}' fired: {wd.reason}")

    def watchdog_should_have_fired(self, name='DEFAULT'):
        """Fails unless the watchdog has fired. Useful in negative tests."""
        wd = self._get(name)
        if wd.state != 'FIRED':
            raise AssertionError(f"Watchdog '{name}' has not fired "
                                 f"(state: {wd.state}).")

    # ------------------------------------------------------------------

    def _get(self, name):
        if name not in self._watchdogs:
            raise RuntimeError(f"Watchdog '{name}' is not configured.")
        return self._watchdogs[name]


class _Watchdog:
    """State of one watchdog, shared between main and supervisor threads."""

    def __init__(self, name, max_duration, stall_timeout, heartbeat,
                 on_timeout, heartbeat_keyword):
        self.name = name
        self.max_duration = max_duration
        self.stall_timeout = stall_timeout
        self.heartbeat = heartbeat
        self.on_timeout = on_timeout
        self.heartbeat_keyword = heartbeat_keyword
        self.state = 'CONFIGURED'
        self.reason = None
        self.stop = threading.Event()
        self.feeds = 0
        self.started = None
        self.last_feed = None
        self.next_heartbeat = None

    def start(self):
        self.started = self.last_feed = time.monotonic()
        if self.heartbeat:
            self.next_heartbeat = self.started + self.heartbeat
        self.state = 'RUNNING'

    def feed(self):
        self.last_feed = time.monotonic()
        self.feeds += 1

    def check(self, now):
        """Returns the reason to fire, or None."""
        if self.max_duration and now - self.started > self.max_duration:
            return (f'max_duration {secs_to_timestr(self.max_duration)} '
                    f'exceeded.')
        if self.stall_timeout and now - self.last_feed > self.stall_timeout:
            return (f'no feed within stall_timeout '
                    f'{secs_to_timestr(self.stall_timeout)}.')
        return None
