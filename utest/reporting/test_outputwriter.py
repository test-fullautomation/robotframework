import os
import tempfile
import unittest

from robot.reporting.outputwriter import OutputWriter
from robot.result import ExecutionResult, Result, TestSuite


class TestOutputWriterThreads(unittest.TestCase):
    """rebot --output must serialize <thread> elements of a result model.

    XmlLogger.start_thread implements the run-time placeholder for the main
    writer; used as-is by OutputWriter it would not write the thread body.
    """

    def test_thread_survives_rebot_round_trip(self):
        suite = TestSuite(name="Suite")
        test = suite.tests.create(name="Test", status="PASS")
        thread = test.body.create_thread(name="MONITOR", daemon=True)
        thread.status = "PASS"
        thread.start_time = "2026-09-27T10:00:00.000000"
        thread.elapsed_time = 1
        thread.doc = "reports progress"
        kw = thread.body.create_keyword(name="Log", owner="BuiltIn", status="PASS")
        kw.body.create_message(
            message="hello from the thread",
            level="INFO",
            timestamp="2026-09-27T10:00:00.500000",
        )
        test.body.create_keyword(name="After", status="PASS")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output.xml")
            with open(path, "w", encoding="UTF-8") as file:
                Result(suite=suite).visit(OutputWriter(file))
            reread = ExecutionResult(path)
        test = reread.suite.tests[0]
        thread, after = test.body
        self.assertEqual(thread.type, thread.THREAD)
        # The XML reader keeps 'daemon' as the attribute string.
        self.assertEqual((thread.name, str(thread.daemon)), ("MONITOR", "True"))
        self.assertEqual(thread.status, "PASS")
        self.assertEqual(thread.doc, "reports progress")
        self.assertEqual(thread.elapsed_time.total_seconds(), 1)
        self.assertEqual(thread.body[0].name, "Log")
        self.assertEqual(thread.body[0].messages[0].message, "hello from the thread")
        self.assertEqual(after.name, "After")


if __name__ == "__main__":
    unittest.main()
