*** Settings ***
Suite Setup       Run Tests    ${EMPTY}    running/if/invalid_if.robot
Test Template     Branch statuses should be
Resource          atest_resource.robot

*** Test Cases ***
IF without condition
    UNKNOWN

IF without condition with ELSE
    UNKNOWN    NOT RUN

IF with invalid condition
    UNKNOWN

IF with invalid condition with ELSE
    UNKNOWN    NOT RUN

IF condition with non-existing ${variable}
    UNKNOWN    NOT RUN

IF condition with non-existing $variable
    UNKNOWN    NOT RUN

ELSE IF with invalid condition
    NOT RUN    NOT RUN    UNKNOWN    NOT RUN    NOT RUN

Recommend $var syntax if invalid condition contains ${var}
    UNKNOWN    index=1

IF without END
    UNKNOWN

Invalid END
    UNKNOWN

IF with wrong case
    [Template]    NONE
    Check Test Case    ${TEST NAME}

ELSE IF without condition
    UNKNOWN    NOT RUN    NOT RUN

ELSE IF with multiple conditions
    [Template]    NONE
    ${tc} =    Branch statuses should be    UNKNOWN    NOT RUN    NOT RUN
    Should Be Equal    ${tc.body[0].body[1].condition}    \${False}, ooops, \${True}

ELSE with condition
    UNKNOWN    NOT RUN

IF with empty body
    UNKNOWN

ELSE with empty body
    UNKNOWN    NOT RUN

ELSE IF with empty body
    UNKNOWN    NOT RUN    NOT RUN

ELSE after ELSE
    UNKNOWN    NOT RUN    NOT RUN

ELSE IF after ELSE
    UNKNOWN    NOT RUN    NOT RUN

Dangling ELSE
    [Template]    Check Test Case
    ${TEST NAME}

Dangling ELSE inside FOR
    [Template]    Check Test Case
    ${TEST NAME}

Dangling ELSE inside WHILE
    [Template]    Check Test Case
    ${TEST NAME}

Dangling ELSE IF
    [Template]    Check Test Case
    ${TEST NAME}

Dangling ELSE IF inside FOR
    [Template]    Check Test Case
    ${TEST NAME}

Dangling ELSE IF inside WHILE
    [Template]    Check Test Case
    ${TEST NAME}

Dangling ELSE IF inside TRY
    [Template]    Check Test Case
    ${TEST NAME}

Invalid IF inside FOR
    UNKNOWN

Multiple errors
    UNKNOWN    NOT RUN    NOT RUN    NOT RUN    NOT RUN

Invalid data causes syntax error
    [Template]    Check Test Case
    ${TEST NAME}

Invalid condition causes normal error
    [Template]    Check Test Case
    ${TEST NAME}

Non-existing variable in condition causes normal error
    [Template]    Check Test Case
    ${TEST NAME}

*** Keywords ***
Branch statuses should be
    [Arguments]    @{statuses}    ${index}=0
    ${tc} =    Check Test Case    ${TESTNAME}
    ${if} =    Set Variable    ${tc.body}[${index}]
    Should Be Equal    ${if.status}    UNKNOWN
    FOR    ${branch}    ${status}    IN ZIP    ${if.body}    ${statuses}    mode=STRICT
        Should Be Equal    ${branch.status}    ${status}
    END
    RETURN    ${tc}
