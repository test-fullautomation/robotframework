import unittest

from robot.api.parsing import get_model, Thread as ThreadBlock, ThreadHeader
from robot.running import TestSuite, Thread

DATA = """\
*** Test Cases ***
Example
    THREAD    monitor    False
        Log    in the thread
        FOR    ${i}    IN RANGE    2
            Log    ${i}
        END
    END
    THREAD    helper
        Log    default daemon
    END
    Log    after
"""


class TestThreadParsing(unittest.TestCase):

    def test_parsing_model(self):
        model = get_model(DATA)
        test = model.sections[0].body[0]
        thread = test.body[0]
        self.assertIsInstance(thread, ThreadBlock)
        self.assertIsInstance(thread.header, ThreadHeader)
        self.assertEqual((thread.name, thread.daemon), ("monitor", False))
        self.assertEqual(thread.errors, ())
        self.assertEqual(len(thread.body), 2)
        helper = test.body[1]
        self.assertEqual((helper.name, helper.daemon), ("helper", True))

    def test_validation(self):
        model = get_model(
            "*** Test Cases ***\nT\n    THREAD    name    maybe\n        Log    x\n"
        )
        thread = model.sections[0].body[0].body[0]
        self.assertEqual(thread.header.errors, ("THREAD has invalid daemon setting.",))
        self.assertEqual(thread.errors, ("THREAD has no closing END.",))

    def test_running_model(self):
        suite = TestSuite.from_string(DATA)
        thread, helper, log = suite.tests[0].body
        self.assertIsInstance(thread, Thread)
        self.assertEqual(thread.type, "THREAD")
        self.assertEqual((thread.name, thread.daemon, thread.lineno), ("monitor", False, 3))
        self.assertEqual(thread.error, None)
        self.assertEqual([item.type for item in thread.body], ["KEYWORD", "FOR"])
        self.assertEqual(helper.daemon, True)
        self.assertEqual(log.name, "Log")

    def test_dict_round_trip(self):
        suite = TestSuite.from_string(DATA)
        data = suite.to_dict()
        reread = TestSuite.from_dict(data)
        thread = reread.tests[0].body[0]
        self.assertIsInstance(thread, Thread)
        self.assertEqual(thread.to_dict(), suite.tests[0].body[0].to_dict())
        self.assertEqual(str(thread), "THREAD    name=monitor    daemon=False")

    def test_invalid_thread_gets_error(self):
        suite = TestSuite.from_string(
            "*** Test Cases ***\nT\n    THREAD    name    maybe\n        Log    x\n"
        )
        thread = suite.tests[0].body[0]
        self.assertIn("invalid daemon setting", thread.error)


if __name__ == "__main__":
    unittest.main()
