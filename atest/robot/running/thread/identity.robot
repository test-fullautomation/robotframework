*** Settings ***
Documentation     cuongnht add thread: a thread is identified by a key, not by
...               its name, and its runtime state is its own.
Suite Setup       Run Tests    ${EMPTY}    running/thread/identity.robot
Resource          thread.resource

*** Test Cases ***
Same Name In A Loop
    ${tc}=    Check Test Case    ${TESTNAME}
    FOR    ${i}    IN RANGE    3
        ${thread}=    Set Variable    ${tc.body[0].body[${i}].body[0]}
        Should Be Equal    ${thread.type}    THREAD
        Should Be Equal    ${thread.status}    PASS
        Check Log Message    ${thread.body[0].messages[0]}    dup ${i}
    END
    No Thread Warnings

Thread Inside IF False
    ${tc}=    Check Test Case    ${TESTNAME}
    Should Be Equal    ${tc.body[0].body[0].body[0].type}    THREAD
    Should Be Equal    ${tc.body[0].body[0].body[0].status}    NOT RUN
    No Thread Warnings

Same Name Across Tests
    ${a}=    Check Test Case    Same Name Across Tests A
    ${b}=    Check Test Case    Same Name Across Tests B
    ${thread}=    First Thread Of    ${a}
    Should Be Equal    ${thread.status}    PASS
    Check Log Message    ${thread.body[1].messages[0]}    shared from A
    # Test B starts with the note that A's thread is still running.
    ${thread}=    First Thread Of    ${b}
    Should Be Equal    ${thread.status}    PASS
    Check Log Message    ${thread.body[0].messages[0]}    shared from B
    No Thread Warnings

Nested Threads
    ${tc}=    Check Test Case    ${TESTNAME}
    ${outer}=    Set Variable    ${tc.body[0]}
    Should Be Equal    ${outer.status}    PASS
    Check Log Message    ${outer.body[0].messages[0]}    outer start
    Should Be Equal    ${outer.body[1].type}    THREAD
    Should Be Equal    ${outer.body[1].status}    PASS
    Check Log Message    ${outer.body[1].body[0].messages[0]}    inner body
    Check Log Message    ${outer.body[3].messages[0]}    outer end
    No Thread Warnings

Worker Timeout Stays In The Worker
    ${tc}=    Check Test Case    ${TESTNAME}
    Should Be Equal    ${tc.body[0].status}    FAIL
    Should Contain    ${tc.body[0].message}    Keyword timeout 300 milliseconds exceeded
    Should Be Equal    ${tc.body[1].status}    PASS
    Check Log Message    ${tc.body[2].messages[0]}    main slept through

Worker Continue On Failure Stays In The Worker
    ${tc}=    Check Test Case    ${TESTNAME}
    Should Be Equal    ${tc.body[0].status}    PASS
    Should Be Equal    ${tc.body[2].body[0].body[0].status}    FAIL
    Should Be Equal    ${tc.body[2].body[0].body[1].status}    NOT RUN

Variables Set In A Worker Reach The Main Thread
    Check Test Case    ${TESTNAME}

A Suite Thread Sets A Test Variable After Its Test
    Check Test Case    ${TESTNAME}
    Check Test Case    The Next Test Does Not See It

Flatten In The Main Thread Leaves The Worker Log Intact
    ${tc}=    Check Test Case    ${TESTNAME}
    ${thread}=    First Thread Of    ${tc}
    ${loop}=    Set Variable    ${thread.body[0]}
    Should Be Equal    ${loop.type}    FOR
    Length Should Be    ${loop.body}    6
    FOR    ${i}    IN RANGE    6
        Check Log Message    ${loop.body[${i}].body[0].messages[0]}    worker msg ${i}
    END

*** Keywords ***
First Thread Of
    [Arguments]    ${tc}
    FOR    ${item}    IN    @{tc.body}
        IF    $item.type == 'THREAD'    RETURN    ${item}
    END
    Fail    No THREAD in test '${tc.name}'.

No Thread Warnings
    FOR    ${msg}    IN    @{ERRORS}
        Should Not Contain    ${msg.message}    Multiple THREAD blocks
        Should Not Contain    ${msg.message}    No output file found
        Should Not Contain    ${msg.message}    Cannot parse thread output
    END
