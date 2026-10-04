import unittest
from io import BytesIO, StringIO
from pathlib import Path
from xml.etree import ElementTree as ET

from test_resultbuilder import GOLDEN_XML

# Expected serialization results with the fork's write-time behaviour:
# statistics carry the 'unknown' attribute.
CURDIR = Path(__file__).resolve().parent
GOLDEN_SERIALIZED = (CURDIR / "golden_serialized.xml").read_text(encoding="UTF-8")
GOLDEN_TWICE_SERIALIZED = (CURDIR / "goldenTwice_serialized.xml").read_text(
    encoding="UTF-8"
)

from robot.reporting.outputwriter import OutputWriter
from robot.result import ExecutionResult
from robot.utils import ETSource, XmlWriter
from robot.utils.asserts import assert_equal


class StreamXmlWriter(XmlWriter):

    def _create_output(self, output):
        return output

    def close(self):
        pass


class TestableOutputWriter(OutputWriter):

    def _get_writer(self, output, preamble=True):
        return StreamXmlWriter(output, write_empty=False)


class TestResultSerializer(unittest.TestCase):

    def test_single_result_serialization(self):
        output = StringIO()
        writer = TestableOutputWriter(output)
        ExecutionResult(GOLDEN_XML).visit(writer)
        self._assert_xml_content(
            self._xml_lines(output.getvalue()),
            self._xml_lines(GOLDEN_SERIALIZED),
        )

    def _xml_lines(self, text):
        with ETSource(text) as source:
            tree = ET.parse(source)
        output = BytesIO()
        tree.write(output)
        return output.getvalue().splitlines()

    def _assert_xml_content(self, actual, expected):
        assert_equal(len(actual), len(expected))
        for index, (act, exp) in enumerate(list(zip(actual, expected))[2:]):
            assert_equal(
                act,
                exp.strip(),
                f"Different values on line {index}",
            )

    def test_combining_results(self):
        output = StringIO()
        writer = TestableOutputWriter(output)
        ExecutionResult(GOLDEN_XML, GOLDEN_XML).visit(writer)
        self._assert_xml_content(
            self._xml_lines(output.getvalue()),
            self._xml_lines(GOLDEN_TWICE_SERIALIZED),
        )


if __name__ == "__main__":
    unittest.main()
