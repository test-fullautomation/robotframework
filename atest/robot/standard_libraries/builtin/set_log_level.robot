*** Settings ***
Suite Setup       Run Tests    --log set_log_level_log.html    standard_libraries/builtin/set_log_level.robot
Resource          atest_resource.robot

*** Test Cases ***
Set Log Level
    [Documentation]    A 'Log' keyword with an explicit level below the current
    ...    log level is left out of the output altogether in this fork, so the
    ...    keywords logging 'This is NOT logged' have no indexes at all here.
    ${tc} =    Check Test Case    ${TESTNAME}
    Check Log Message    ${tc[0, 0]}       Log level changed from INFO to TRACE.     DEBUG
    Check Log Message    ${tc[1, 1]}       This is logged                            TRACE
    Check Log Message    ${tc[2, 1]}       This is logged                            DEBUG
    Check Log Message    ${tc[3, 1]}       This is logged                            INFO
    Check Log Message    ${tc[4, 1]}       Log level changed from TRACE to DEBUG.    DEBUG
    Check Log Message    ${tc[6, 0]}       This is logged                            DEBUG
    Check Log Message    ${tc[7, 0]}       This is logged                            INFO
    Check Log Message    ${tc[9, 0]}       This is logged                            INFO
    Check Log Message    ${tc[12, 0]}      This is logged                            ERROR
    # Level is not given, so this one is kept even though nothing is logged.
    Should Be Empty      ${tc[14].body}
    Length Should Be     ${tc.non_messages}    15

Invalid Log Level Failure Is Catchable
    Check Test Case    ${TESTNAME}

Reset Log Level
    [Documentation]    The last 'Log' with an explicit DEBUG level is left out
    ...    of the output altogether in this fork.
    ${tc} =    Check Test Case    ${TESTNAME}
    Check Log Message    ${tc[0, 0]}       Log level changed from INFO to DEBUG.     DEBUG
    Check Log Message    ${tc[1, 0]}       This is logged                            INFO
    Check Log Message    ${tc[2, 0]}       This is logged                            DEBUG
    Should Be Empty      ${tc[3].body}
    Check Log Message    ${tc[4, 0]}       This is logged                            INFO
    Length Should Be     ${tc.non_messages}    5

Log Level Goes To HTML
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    KW Info to log
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    KW Trace to log
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    TC Info to log
    File Should Contain    ${OUTDIR}${/}set_log_level_log.html    TC Trace to log
