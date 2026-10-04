import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from robot.output.segmentmerger import merge_segments, recover
from robot.output.xmllogger import XmlLogger
from robot.result import ExecutionResult, Keyword, Message, TestCase, TestSuite


class NullLogger:

    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(("INFO", msg))

    def warn(self, msg):
        self.messages.append(("WARN", msg))

    def error(self, msg):
        self.messages.append(("ERROR", msg))


def write_run(path, segment_interval=None, truncate_live=False):
    """Writes a two-test run through XmlLogger, optionally rotating on every call."""
    file = open(path, "w", encoding="UTF-8")
    logger = XmlLogger(file, path=path, segment_interval=segment_interval)
    if segment_interval is not None:
        logger._main_writer.last_rotation = -1e9  # force rotation at every check
    suite = TestSuite(name="Suite", start_time=datetime(2026, 10, 5, 10, 0, 0))
    logger.start_suite(suite)
    for index in range(2):
        test = TestCase(name=f"Test {index}", start_time=datetime(2026, 10, 5, 10, 0, 1))
        logger.start_test(test)
        for kw_index in range(3):
            kw = Keyword(name=f"KW {index}-{kw_index}", owner="Lib", status="PASS")
            kw.start_time = datetime(2026, 10, 5, 10, 0, 2)
            kw.elapsed_time = 1
            logger.start_keyword(kw)
            logger.message(Message(f"msg {index}-{kw_index}", "INFO"))
            logger.end_keyword(kw)
            if segment_interval is not None:
                logger._main_writer.last_rotation = -1e9
        test.status = "PASS"
        test.elapsed_time = 4
        logger.end_test(test)
    suite.elapsed_time = 10
    logger.end_suite(suite)
    if truncate_live:
        logger._main_writer.close()
        text = Path(path).read_text(encoding="UTF-8")
        Path(path).write_text(text[: len(text) - 40], encoding="UTF-8")
        return logger
    logger.close()
    return logger


class TestSegmentedOutput(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "output.xml"
        self.log = NullLogger()

    def tearDown(self):
        self.tmp.cleanup()

    def _structure(self, path):
        root = ET.parse(path).getroot()
        return [
            (test.get("name"), [kw.get("name") for kw in test.iter("kw")],
             [m.text for m in test.iter("msg")])
            for test in root.iter("test")
        ]

    def test_rotation_creates_well_formed_segments(self):
        logger = write_run(self.path, segment_interval=1e-9)
        parts = logger.segment_paths
        self.assertGreater(len(parts), 3)
        self.assertEqual(
            [os.path.basename(p) for p in parts[:2]],
            ["output_part_001.xml", "output_part_002.xml"],
        )
        for part in parts:
            root = ET.parse(part).getroot()  # every segment parses on its own
            self.assertEqual(root.tag, "robot")
        continued = ET.parse(parts[1]).getroot().find("suite")
        self.assertEqual(continued.get("continued"), "true")

    def test_merged_output_equals_unsegmented_run(self):
        reference = Path(self.tmp.name) / "reference.xml"
        write_run(reference)
        logger = write_run(self.path, segment_interval=1e-9)
        merged = merge_segments(self.path, logger.segment_paths, logger=self.log)
        self.assertEqual(merged, len(logger.segment_paths))
        self.assertEqual(self._structure(self.path), self._structure(reference))
        self.assertFalse(list(Path(self.tmp.name).glob("output_part_*.xml")))
        result = ExecutionResult(self.path)
        self.assertEqual([t.name for t in result.suite.tests], ["Test 0", "Test 1"])
        self.assertEqual(len(result.suite.tests[1].body), 3)
        self.assertNotIn("continued", self.path.read_text(encoding="UTF-8"))

    def test_discovery_and_idempotency(self):
        write_run(self.path, segment_interval=1e-9)
        self.assertGreater(merge_segments(self.path, logger=self.log), 0)
        self.assertEqual(merge_segments(self.path, logger=self.log), 0)

    def test_truncated_live_file_is_recovered(self):
        logger = write_run(self.path, segment_interval=1e-9, truncate_live=True)
        merge_segments(self.path, logger.segment_paths, logger=self.log)
        self.assertTrue(any("recovered" in m for _, m in self.log.messages))
        result = ExecutionResult(self.path)
        self.assertEqual([t.name for t in result.suite.tests], ["Test 0", "Test 1"])

    def test_recover_closes_open_tags(self):
        text = '<robot><suite name="s"><test name="t"><kw name="k"><arg>x</arg><msg lev'
        repaired = recover(text)
        self.assertTrue(repaired.endswith("</kw></test></suite></robot>"))
        self.assertEqual(ET.fromstring(repaired).find(".//kw").get("name"), "k")

    def test_no_rotation_without_interval(self):
        logger = write_run(self.path)
        self.assertEqual(logger.segment_paths, [])
        self.assertEqual(merge_segments(self.path, logger=self.log), 0)


if __name__ == "__main__":
    unittest.main()
