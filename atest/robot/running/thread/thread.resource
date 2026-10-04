*** Settings ***
Resource        atest_resource.robot

*** Keywords ***
Check Threading
    [Arguments]    ${status}    ${daemon}    ${path}=body[0]
    ${tc}=    Check test case    ${TEST NAME}
    ${thread}=    Check thread attributes     ${tc.${path}}    ${status}    ${daemon}
    RETURN    ${thread}

Check thread attributes
    [Arguments]    ${thread}    ${status}    ${daemon}
    Should be equal    ${thread.type}    THREAD
    Should be equal    ${thread.status}    ${status}
    Should be equal    ${thread.daemon}       ${daemon}
    RETURN    ${thread}
