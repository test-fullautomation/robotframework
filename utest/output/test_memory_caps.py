import gc
import os
import tempfile
import unittest
from pathlib import Path

from robot.output.logger import Logger
from robot.output.loggerhelper import Message
from robot.output.loglevel import LogLevel
from robot.output.outputfile import OutputFile
from robot.utils import PriorityQueue
from robot.utils.priorityqueue import QueuedNotification

try:
    import tracemalloc
except ImportError:  # PyPy has no tracemalloc; the soak tests are skipped there.
    tracemalloc = None


class MessageCollector:

    def __init__(self):
        self.messages = []

    def message(self, msg):
        self.messages.append(msg)


class TestLoggerMessageCacheCap(unittest.TestCase):

    def setUp(self):
        self.logger = Logger(register_console_logger=False)
        self.logger.message_cache_limit = 50

    def test_cache_is_capped(self):
        for i in range(80):
            self.logger.message(Message(f"msg {i}", "INFO"))
        self.assertEqual(len(self.logger._message_cache), 50)
        self.assertEqual(self.logger._message_cache_dropped, 30)

    def test_relay_reports_dropped_messages(self):
        for i in range(60):
            self.logger.message(Message(f"msg {i}", "INFO"))
        collector = MessageCollector()
        self.logger._relay_cached_messages(collector)
        self.assertEqual(len(collector.messages), 51)
        self.assertIn(
            "10 further messages were not cached", collector.messages[-1].message
        )

    def test_no_note_when_nothing_dropped(self):
        for i in range(10):
            self.logger.message(Message(f"msg {i}", "INFO"))
        collector = MessageCollector()
        self.logger._relay_cached_messages(collector)
        self.assertEqual(len(collector.messages), 10)

    def test_disabled_cache_does_not_grow(self):
        self.logger.disable_message_cache()
        for i in range(100):
            self.logger.message(Message(f"msg {i}", "INFO"))
        self.assertIsNone(self.logger._message_cache)


def output_file(path, max_errors=40):
    output = OutputFile(path, LogLevel("INFO"))
    output.max_errors = max_errors
    return output


class TestOutputFileErrorsCap(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        self.path = Path(path)
        self.output = output_file(self.path)
        base, _ = os.path.splitext(path)
        self.spill_path = base + "_errors_spill.jsonl"

    def tearDown(self):
        for path in (self.path, self.spill_path):
            if os.path.exists(path):
                os.remove(path)

    def test_overflow_spills_to_disk_and_errors_section_is_complete(self):
        for i in range(100):
            self.output.message(Message(f"warn {i}", "WARN"))
        self.assertEqual(len(self.output.errors), 40)
        self.assertEqual(self.output._errors_dropped, 0)
        self.assertTrue(os.path.exists(self.spill_path))
        self.output.close()
        content = self.path.read_text(encoding="UTF-8")
        # No message is lost: memory batch AND spilled overflow are written.
        for i in (0, 39, 40, 99):
            self.assertIn(f"warn {i}", content)
        self.assertNotIn("further warning/error messages", content)
        self.assertFalse(
            os.path.exists(self.spill_path), "spill file must be removed after close"
        )

    def test_no_spill_below_cap(self):
        self.output.message(Message("only one", "WARN"))
        self.assertFalse(os.path.exists(self.spill_path))
        self.output.close()
        self.assertIn("only one", self.path.read_text(encoding="UTF-8"))

    def test_unknown_level_messages_are_collected(self):
        self.output.message(Message("import failed", "UNKNOWN"))
        self.assertEqual(len(self.output.errors), 1)
        self.output.close()
        self.assertIn('level="UNKNOWN"', self.path.read_text(encoding="UTF-8"))

    def test_without_output_file_overflow_is_counted(self):
        self.output.close()  # release the setUp file handle (Windows)
        output = output_file(None)
        for i in range(100):
            output.message(Message(f"warn {i}", "WARN"))
        self.assertEqual(len(output.errors), 40)
        self.assertEqual(output._errors_dropped, 60)
        output.close()  # NullLogger: must not raise

    def test_info_messages_not_collected(self):
        for i in range(100):
            self.output.message(Message(f"info {i}", "INFO"))
        self.assertEqual(len(self.output.errors), 0)
        self.assertFalse(os.path.exists(self.spill_path))
        self.output.close()


class TestNotificationQueueCap(unittest.TestCase):

    def test_queue_drops_oldest_when_full(self):
        q = PriorityQueue(queue_type="FIFO")
        q.max_items = 25
        for i in range(60):
            q.put(QueuedNotification(f"n{i}"))
        self.assertEqual(q.qsize(), 25)
        self.assertEqual(q.dropped, 35)
        # Oldest were dropped: the first remaining entry is n35.
        _, item = q.get(block=False)
        self.assertEqual(item.name, "n35")

    def test_no_drop_below_cap(self):
        q = PriorityQueue(queue_type="FIFO")
        q.max_items = 25
        for i in range(10):
            q.put(QueuedNotification(f"n{i}"))
        self.assertEqual(q.qsize(), 10)
        self.assertEqual(q.dropped, 0)
        _, item = q.get(block=False)
        self.assertEqual(item.name, "n0")


def steady_state_growth(action, iterations=5000):
    """Bytes of Python memory retained between two equal workload phases.

    The caller must have pushed the exercised accumulator past its cap
    BEFORE calling, so both measurements happen in the steady (capped)
    state: entries allocated in phase 1 are replaced by entries from
    phase 2, and the delta only shows genuine unbounded growth.
    """
    gc.collect()
    tracemalloc.start()
    try:
        for i in range(iterations):
            action(i)
        gc.collect()
        first = tracemalloc.get_traced_memory()[0]
        for i in range(iterations):
            action(iterations + i)
        gc.collect()
        second = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    return second - first


class NullSink:
    """Registered logger that consumes without storing (like console)."""

    def message(self, msg):
        pass


@unittest.skipUnless(tracemalloc, "Requires tracemalloc.")
class TestMemorySoak(unittest.TestCase):
    """Push real workloads through the pipelines and assert flat memory.

    Unlike the cap tests above (which assert bounded container sizes),
    these measure actual retained Python memory with tracemalloc, so an
    unknown accumulator sitting on the same code path would also fail them.
    """

    THRESHOLD = 256 * 1024  # generous noise allowance for 5000 iterations

    def test_logger_message_pipeline_is_flat(self):
        logger = Logger(register_console_logger=False)
        logger.message_cache_limit = 100
        logger.register_logger(NullSink())
        for i in range(150):  # past the cap -> steady state
            logger.message(Message(f"fill {i}", "INFO"))
        growth = steady_state_growth(
            lambda i: logger.message(Message(f"msg {i} with some payload", "WARN"))
        )
        self.assertLess(
            growth,
            self.THRESHOLD,
            f"LOGGER.message grew by {growth} bytes in steady state",
        )

    def test_output_file_errors_pipeline_is_flat(self):
        fd, path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        output = output_file(Path(path), max_errors=100)
        try:
            for i in range(150):  # past the cap -> spill active
                output.message(Message(f"fill {i}", "WARN"))
            growth = steady_state_growth(
                lambda i: output.message(Message(f"warn {i} with payload", "WARN"))
            )
            self.assertLess(
                growth,
                self.THRESHOLD,
                f"OutputFile.message grew by {growth} bytes in steady state",
            )
        finally:
            output.close()
            base, _ = os.path.splitext(path)
            for leftover in (path, base + "_errors_spill.jsonl"):
                if os.path.exists(leftover):
                    os.remove(leftover)

    def test_notification_queue_is_flat(self):
        q = PriorityQueue(queue_type="FIFO")
        q.max_items = 100
        for i in range(150):  # past the cap -> drop-oldest active
            q.put(QueuedNotification(f"fill {i}"))
        growth = steady_state_growth(
            lambda i: q.put(QueuedNotification(f"notification {i}"))
        )
        self.assertLess(
            growth,
            self.THRESHOLD,
            f"notification queue grew by {growth} bytes in steady state",
        )


if __name__ == "__main__":
    unittest.main()
