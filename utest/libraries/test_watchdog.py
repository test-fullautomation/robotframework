import time
import unittest

from robot.libraries.Watchdog import Watchdog, _Watchdog
from robot.running import pausepoint


class TestWatchdogPause(unittest.TestCase):

    def _running(self, **config):
        settings = dict(max_duration=None, stall_timeout=None, heartbeat=None,
                        on_timeout=None, heartbeat_keyword=None)
        settings.update(config)
        wd = _Watchdog('WD', **settings)
        wd.start()
        return wd

    def test_paused_time_is_not_a_stall(self):
        wd = self._running(stall_timeout=0.2)
        wd.pause()
        time.sleep(0.3)
        seconds = wd.resume()
        self.assertGreaterEqual(seconds, 0.08)
        self.assertIsNone(wd.check(time.monotonic()))
        time.sleep(0.3)
        self.assertIn('no feed within stall_timeout', wd.check(time.monotonic()))

    def test_paused_time_does_not_use_up_the_deadline(self):
        wd = self._running(max_duration=0.2, heartbeat=10)
        heartbeat = wd.next_heartbeat
        wd.pause()
        time.sleep(0.3)
        wd.resume()
        self.assertIsNone(wd.check(time.monotonic()))
        self.assertGreaterEqual(wd.next_heartbeat - heartbeat, 0.08)

    def test_pause_only_applies_to_a_running_watchdog(self):
        wd = _Watchdog('WD', None, 1, None, None, None)
        wd.pause()
        self.assertIsNone(wd.paused_at)
        self.assertIsNone(wd.resume())
        wd.start()
        wd.pause()
        first = wd.paused_at
        wd.pause()
        self.assertEqual(wd.paused_at, first)

    def test_a_paused_flow_pauses_every_watchdog(self):
        library = Watchdog()
        library.configure_watchdog('A', stall_timeout='1 min', heartbeat='NONE')
        library.configure_watchdog('B', stall_timeout='1 min', heartbeat='NONE')
        library._watchdogs['A'].start()
        pausepoint.notify_pause()
        self.assertIsNotNone(library._watchdogs['A'].paused_at)
        self.assertIsNone(library._watchdogs['B'].paused_at)      # not running
        pausepoint.notify_resume(0.0)
        self.assertIsNone(library._watchdogs['A'].paused_at)

    def test_keywords(self):
        library = Watchdog()
        library.configure_watchdog(stall_timeout='1 min', heartbeat='NONE')
        library._watchdogs['DEFAULT'].start()
        library.pause_watchdog()
        self.assertIsNotNone(library._watchdogs['DEFAULT'].paused_at)
        library.resume_watchdog()
        library.resume_watchdog()       # resuming twice is harmless
        self.assertIsNone(library._watchdogs['DEFAULT'].paused_at)


if __name__ == '__main__':
    unittest.main()
