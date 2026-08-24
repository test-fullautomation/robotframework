*** Settings ***
Suite Setup       Run Tests    --log set_log_level_log.html    standard_libraries/builtin/set_log_level.robot
Resource          atest_resource.robot

*** Test Cases ***
Set Log Level
    [Documentation]    A 'Log' keyword with an explicit level below the current
    ...    log level is left out of the output altogether in this fork, so the
    ...    keywords logging 'This is NOT logged' have no indexes at all here.
    ${tc} =    Check Test Case    ${TESTNAME}
    Check Log Message    ${tc.kws[0].msgs[0]}    Log level changed from INFO to TRACE.
    Check Log Message    ${tc.kws[1].msgs[1]}    This is logged    TRACE
    Check Log Message    ${tc.kws[2].msgs[1]}    This is logged    DEBUG
    Check Log Message    ${tc.kws[3].msgs[1]}    This is logged    INFO
    Check Log Message    ${tc.kws[6].msgs[0]}    This is logged    DEBUG
    Check Log Message    ${tc.kws[7].msgs[0]}    This is logged    INFO
    Check Log Message    ${tc.kws[9].msgs[0]}    This is logged    INFO
    Check Log Message    ${tc.kws[12].msgs[0]}    This is logged    ERROR
    # Level is not given, so this one is kept even though nothing is logged.
    Should Be Empty    ${tc.kws[14].msgs}
    Length Should Be    ${tc.kws}    15

Invalid Log Level Failure Is Catchable
    Check Test Case    ${TESTNAME}

Log Level Goes To HTML
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    KW Info to log
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    KW Trace to log
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    TC Info to log
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    TC Trace to log
