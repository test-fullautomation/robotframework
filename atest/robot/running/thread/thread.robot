*** Settings ***
Resource          thread.resource
Suite Setup       Run Tests    ${EMPTY}    running/thread/thread.robot

*** Test Cases ***
Thread executed
    ${thread}=    Check Threading    PASS    True
	${tc}=    Check test case    ${TEST NAME}
    Check Log Message   ${tc.body[1].msgs[0]}    CONTINUE IN MAIN THREAD

Thread change variable
    ${thread}=    Check Threading    PASS    True    body[1]
	${tc}=    Check test case    ${TEST NAME}
    Check Log Message   ${tc.body[2].msgs[0]}    Variable before: 1
	Check Log Message   ${tc.body[4].msgs[0]}    Variable after: 2

Thread wait for thread notification
    ${thread}=    Check Threading    PASS    True
	${tc}=    Check test case    ${TEST NAME}
    Check Log Message   ${tc.body[2].msgs[0]}    Get notification from thread. Payloads: Thread Done
	
Thread wait for thread notification timeout
    ${thread}=    Check Threading    PASS    True
	${tc}=    Check test case    ${TEST NAME}       FAIL


