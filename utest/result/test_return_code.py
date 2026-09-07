import unittest

from robot.result import Result, TestSuite
from robot.utils.asserts import assert_equal


def result_with(failed=0, unknown=0, passed=1):
    suite = TestSuite()
    for status, count in (('FAIL', failed), ('UNKNOWN', unknown),
                          ('PASS', passed)):
        for i in range(count):
            suite.tests.create(name='%s %d' % (status, i), status=status)
    return Result(root_suite=suite)


class TestReturnCodeEncoding(unittest.TestCase):
    """rc == (min(unknown, 14) << 4) | min(failed, 15); 0 = all good."""

    def test_all_passed(self):
        assert_equal(result_with().return_code, 0)

    def test_failed_in_low_nibble(self):
        assert_equal(result_with(failed=3).return_code, 3)
        assert_equal(result_with(failed=3).return_code & 0xF, 3)

    def test_unknown_in_high_nibble(self):
        assert_equal(result_with(unknown=1).return_code, 16)
        assert_equal((result_with(unknown=1).return_code >> 4) & 0xF, 1)

    def test_combined(self):
        rc = result_with(failed=2, unknown=3).return_code
        assert_equal(rc, (3 << 4) | 2)
        assert_equal(rc & 0xF, 2)
        assert_equal((rc >> 4) & 0xF, 3)

    def test_caps(self):
        assert_equal(result_with(failed=20).return_code, 15)
        assert_equal(result_with(unknown=20).return_code, 14 << 4)
        assert_equal(result_with(failed=20, unknown=20).return_code, 239)

    def test_never_reaches_reserved_codes(self):
        rc = result_with(failed=250, unknown=250).return_code
        assert rc < 251, rc

    def test_nonzero_for_unknown_only_run(self):
        # An environment-broken run must not look green to CI.
        assert result_with(unknown=1, passed=0).return_code != 0

    def test_status_rc_disabled(self):
        result = result_with(failed=5, unknown=5)
        result.configure(status_rc=False)
        assert_equal(result.return_code, 0)


if __name__ == '__main__':
    unittest.main()
