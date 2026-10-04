import os
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from robot.output.threadmerger import merge_thread_outputs
from robot.result import ExecutionResult
from robot.timeline import build_tests, generate_timeline

MAIN = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 7.5" generated="2026-10-05T10:00:00.000000" rpa="false" schemaversion="5">
<suite id="s1" name="Suite">
<test id="s1-t1" name="Test" line="2">
<thread name="WORKER" daemon="True">
<doc></doc>
<status status="PASS" start="2026-10-05T10:00:00.100000" elapsed="0"></status>
</thread>
<kw name="Sleep" owner="BuiltIn">
<arg>0.2</arg>
<status status="PASS" start="2026-10-05T10:00:00.110000" elapsed="0.200000"/>
</kw>
<status status="PASS" start="2026-10-05T10:00:00.050000" elapsed="0.300000"/>
</test>
<status status="PASS" start="2026-10-05T10:00:00.000000" elapsed="0.400000"/>
</suite>
<statistics>
<total><stat pass="1" fail="0" skip="0" unknown="0">All Tests</stat></total>
<tag></tag>
<suite><stat name="Suite" id="s1" pass="1" fail="0" skip="0" unknown="0">Suite</stat></suite>
</statistics>
<errors></errors>
</robot>
"""

THREAD_FINISHED = """<?xml version="1.0" encoding="UTF-8"?>
<thread name="WORKER" generator="Robot 7.5" generated="2026-10-05T10:00:00.100000" rpa="false" schemaversion="5">
<kw name="Log" owner="BuiltIn">
<msg time="2026-10-05T10:00:00.150000" level="INFO">from the worker</msg>
<arg>from the worker</arg>
<status status="PASS" start="2026-10-05T10:00:00.120000" elapsed="0.050000"/>
</kw>
<status status="PASS" start="2026-10-05T10:00:00.100000" elapsed="0.100000"/>
</thread>
"""

# The worker was abandoned: the root element was never closed and there is
# no final status.
THREAD_ABANDONED = """<?xml version="1.0" encoding="UTF-8"?>
<thread name="WORKER" generator="Robot 7.5" generated="2026-10-05T10:00:00.100000" rpa="false" schemaversion="5">
<kw name="Sleep" owner="BuiltIn">
<arg>1 hour</arg>
"""


class TestThreadMerger(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "output.xml"
        self.thread_file = Path(self.tmp.name) / "output_WORKER.xml"
        self.output.write_text(MAIN, encoding="UTF-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _merge(self, thread_content):
        self.thread_file.write_text(thread_content, encoding="UTF-8")
        return merge_thread_outputs(self.output, {"WORKER": str(self.thread_file)})

    def test_body_and_real_status_are_grafted(self):
        self.assertEqual(self._merge(THREAD_FINISHED), 1)
        self.assertFalse(self.thread_file.exists(), "merged file must be removed")
        thread = ET.parse(self.output).getroot().find(".//thread")
        self.assertEqual([c.tag for c in thread], ["kw", "doc", "status"])
        self.assertEqual(thread.find("status").get("elapsed"), "0.100000")
        result = ExecutionResult(self.output)
        thread = result.suite.tests[0].body[0]
        self.assertEqual(thread.type, "THREAD")
        self.assertEqual(thread.body[0].messages[0].message, "from the worker")
        self.assertEqual(thread.elapsed_time.total_seconds(), 0.1)

    def test_abandoned_thread_becomes_unknown(self):
        self.assertEqual(self._merge(THREAD_ABANDONED), 1)
        result = ExecutionResult(self.output)
        thread = result.suite.tests[0].body[0]
        self.assertEqual(thread.status, "UNKNOWN")
        self.assertIn("did not finish", thread.message)
        self.assertEqual(thread.body[0].name, "Sleep")

    def test_merge_is_idempotent_and_discovers_files(self):
        self.thread_file.write_text(THREAD_FINISHED, encoding="UTF-8")
        self.assertEqual(merge_thread_outputs(self.output), 1)
        self.assertEqual(merge_thread_outputs(self.output), 0)

    def test_timeline_from_merged_output(self):
        self._merge(THREAD_FINISHED)
        tests = build_tests(ET.parse(self.output).getroot())
        self.assertEqual(len(tests), 1)
        lanes = tests[0]["lanes"]
        self.assertEqual(set(lanes), {"MainThread", "WORKER"})
        self.assertEqual(lanes["WORKER"]["span"]["id"], "s1-t1-k1")
        self.assertEqual(lanes["WORKER"]["bars"][0]["label"], "BuiltIn.Log")
        self.assertEqual(lanes["MainThread"]["bars"][0]["id"], "s1-t1-k2")
        path, count = generate_timeline(self.output)
        self.assertEqual(count, 1)
        html = Path(path).read_text(encoding="UTF-8")
        self.assertIn("WORKER", html)
        self.assertIn('href="log.html#s1-t1-k1"', html)


if __name__ == "__main__":
    unittest.main()
