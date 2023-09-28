*** Settings ***
Suite Setup       Run Tests    ${EMPTY}    running/if/invalid_inline_if.robot
Test Template     Check IF/ELSE Status
Resource          if.resource

*** Test Cases ***
Invalid condition
    UNKNOWN    NOT RUN

Condition with non-existing variable
    UNKNOWN

Invalid condition with other error
    UNKNOWN    NOT RUN

Empty IF
    UNKNOWN

IF without branch
    UNKNOWN

IF without branch with ELSE IF
    UNKNOWN    NOT RUN    else=False

IF without branch with ELSE
    UNKNOWN    NOT RUN

IF followed by ELSE IF
    UNKNOWN

IF followed by ELSE
    UNKNOWN

Empty ELSE IF
    UNKNOWN       NOT RUN    test=${TESTNAME} 1    else=False
    NOT RUN    UNKNOWN       test=${TESTNAME} 2    else=False

ELSE IF without branch
    UNKNOWN    NOT RUN               test=${TESTNAME} 1    else=False
    UNKNOWN    NOT RUN    NOT RUN    test=${TESTNAME} 2

Empty ELSE
    UNKNOWN    NOT RUN    NOT RUN

ELSE IF after ELSE
    UNKNOWN    NOT RUN    NOT RUN               types=['IF', 'ELSE', 'ELSE IF']               test=${TESTNAME} 1
    UNKNOWN    NOT RUN    NOT RUN    NOT RUN    types=['IF', 'ELSE', 'ELSE IF', 'ELSE IF']    test=${TESTNAME} 2

Multiple ELSEs
    UNKNOWN    NOT RUN    NOT RUN               types=['IF', 'ELSE', 'ELSE']            test=${TESTNAME} 1
    UNKNOWN    NOT RUN    NOT RUN    NOT RUN    types=['IF', 'ELSE', 'ELSE', 'ELSE']    test=${TESTNAME} 2

Nested IF
    UNKNOWN               test=${TESTNAME} 1
    UNKNOWN    NOT RUN    test=${TESTNAME} 2
    UNKNOWN               test=${TESTNAME} 3

Nested FOR
    UNKNOWN

Unnecessary END
    PASS       NOT RUN    index=0
    NOT RUN    UNKNOWN       index=1

Invalid END after inline header
    [Template]    NONE
    ${tc} =    Check Test Case    ${TEST NAME}
    Check IF/ELSE Status    PASS    root=${tc.body[0]}
    Check Log Message     ${tc.body[0].body[0].body[0].body[0]}   Executed inside inline IF
    Check Log Message     ${tc.body[1].body[0]}                   Executed outside IF
    Check Keyword Data    ${tc.body[2]}                           ${EMPTY}    type=ERROR    status=UNKNOWN

Assign in IF branch
    UNKNOWN

Assign in ELSE IF branch
    UNKNOWN    NOT RUN    else=False

Assign in ELSE branch
    UNKNOWN    NOT RUN

Invalid assign mark usage
    UNKNOWN    NOT RUN

Too many list variables in assign
    UNKNOWN    NOT RUN

Invalid number of variables in assign
    NOT RUN    UNKNOWN

Invalid value for list assign
    UNKNOWN    NOT RUN

Invalid value for dict assign
    NOT RUN    UNKNOWN

Assign when IF branch is empty
    UNKNOWN    NOT RUN

Assign when ELSE IF branch is empty
    UNKNOWN    NOT RUN    NOT RUN

Assign when ELSE branch is empty
    UNKNOWN    NOT RUN

Control structures are allowed
    [Template]    NONE
    ${tc} =    Check Test Case    ${TESTNAME}
    Check IF/ELSE Status    NOT RUN    PASS    root=${tc.body[0].body[0]}

Control structures are not allowed with assignment
    [Template]    NONE
    ${tc} =    Check Test Case    ${TESTNAME}
    Check IF/ELSE Status    UNKNOWN    NOT RUN    root=${tc.body[0].body[0]}
