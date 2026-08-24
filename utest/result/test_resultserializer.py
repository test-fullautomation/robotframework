import os.path
import unittest
from io import BytesIO, StringIO

from robot.result import ExecutionResult
from robot.reporting.outputwriter import OutputWriter
from robot.utils import ET, ETSource, XmlWriter
from robot.utils.asserts import assert_equal

from test_resultbuilder import GOLDEN_XML, GOLDEN_XML_TWICE

# Expected serialization results with the fork's write-time behaviour:
# BuiltIn.Log keywords below the XML log level are suppressed and
# statistics carry the 'unknown' attribute.
CURDIR = os.path.dirname(os.path.abspath(__file__))
GOLDEN_SERIALIZED = os.path.join(CURDIR, 'golden_serialized.xml')
GOLDEN_TWICE_SERIALIZED = os.path.join(CURDIR, 'goldenTwice_serialized.xml')


class StreamXmlWriter(XmlWriter):

    def _order_attrs(self, attrs):
        return sorted(attrs.items())

    def _create_output(self, output):
        return output

    def close(self):
        pass


class TestableOutputWriter(OutputWriter):

    def _get_writer(self, output, rpa, generator):
        writer = StreamXmlWriter(output, write_empty=False)
        writer.start('robot')
        return writer


class TestResultSerializer(unittest.TestCase):

    def test_single_result_serialization(self):
        output = StringIO()
        writer = TestableOutputWriter(output)
        ExecutionResult(GOLDEN_XML).visit(writer)
        self._assert_xml_content(self._xml_lines(output.getvalue()),
                                 self._xml_lines(GOLDEN_SERIALIZED))

    def _xml_lines(self, text):
        with ETSource(text) as source:
            tree = ET.parse(source)
        output = BytesIO()
        tree.write(output)
        return output.getvalue().splitlines()

    def _assert_xml_content(self, actual, expected):
        assert_equal(len(actual), len(expected))
        for index, (act, exp) in enumerate(list(zip(actual, expected))[2:]):
            assert_equal(act, exp.strip(), 'Different values on line %d' % index)

    def test_combining_results(self):
        output = StringIO()
        writer = TestableOutputWriter(output)
        ExecutionResult(GOLDEN_XML, GOLDEN_XML).visit(writer)
        self._assert_xml_content(self._xml_lines(output.getvalue()),
                                 self._xml_lines(GOLDEN_TWICE_SERIALIZED))


if __name__ == '__main__':
    unittest.main()
