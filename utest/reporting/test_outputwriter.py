import os
import tempfile
import unittest

from robot.reporting.outputwriter import OutputWriter
from robot.result import ExecutionResult, Result, TestSuite


class TestOutputWriterThreads(unittest.TestCase):
    """rebot --output must serialize <thread> elements of a result model.

    XmlLogger.start_thread implements the run-time placeholder for the main
    writer and expects a running-model thread; used as-is by OutputWriter it
    crashed with "'Thread' object has no attribute 'result'".
    """

    def test_thread_survives_rebot_round_trip(self):
        suite = TestSuite(name='Suite')
        test = suite.tests.create(name='Test', status='PASS')
        thread = test.body.create_thread(name='MONITOR', daemon=True)
        thread.status = 'PASS'
        thread.starttime = '20260927 10:00:00.000'
        thread.endtime = '20260927 10:00:01.000'
        thread.doc = 'reports progress'
        kw = thread.body.create_keyword(kwname='Log', status='PASS')
        kw.body.create_message(message='hello from the thread', level='INFO',
                               timestamp='20260927 10:00:00.500')
        test.body.create_keyword(kwname='After', status='PASS')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'output.xml')
            Result(root_suite=suite).visit(OutputWriter(path))
            reread = ExecutionResult(path)
        test = reread.suite.tests[0]
        thread, after = test.body
        self.assertEqual(thread.type, thread.THREAD)
        # The XML reader keeps 'daemon' as the attribute string, as for run-time outputs.
        self.assertEqual((thread.name, str(thread.daemon)), ('MONITOR', 'True'))
        self.assertEqual(thread.status, 'PASS')
        self.assertEqual(thread.doc, 'reports progress')
        self.assertEqual(thread.body[0].name, 'Log')
        self.assertEqual(thread.body[0].messages[0].message, 'hello from the thread')
        self.assertEqual(after.name, 'After')


if __name__ == '__main__':
    unittest.main()
