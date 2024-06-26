*** Variables ***
${TEST OR TASK}   Test

*** Test Cases ***
    [Documentation]  FAIL ${TEST OR TASK} name cannot be empty.
    Fail  Should not be executed

Empty Test Case
    [Documentation]  FAIL ${TEST OR TASK} cannot be empty.

Empty Test Case With Setup And Teardown
    [Documentation]  FAIL ${TEST OR TASK} cannot be empty.
    [Setup]  Fail  Should not be executed
    [Teardown]  Fail  Should not be executed

Empty User Keyword
    [Documentation]  UNKNOWN User keyword cannot be empty.
    Empty UK

User Keyword With Only Non-Empty [Return] Works
    UK With Return

User Keyword With Empty [Return] Does Not Work
    UK With Empty Return

Empty User Keyword With Other Settings Than [Return]
    [Documentation]  UNKNOWN User keyword cannot be empty.
    Empty UK With Settings  argument

Non-Empty And Empty User Keyword
    [Documentation]  UNKNOWN User keyword cannot be empty.
    UK
    Empty Uk
    Fail  We should not be here

Non-Empty UK Using Empty UK
    [Documentation]  UNKNOWN User keyword cannot be empty.
    Non Empty UK Using Empty UK

*** Keywords ***
    [Documentation]  This keyword has no name
    [Arguments]  ${arg}=urg
    Fail   Should not be executed

Empty UK

Empty UK With Settings
    [Arguments]  ${arg}
    [Documentation]  Settings other than [Return] are not enough to make keyword non-empty

Non Empty UK Using Empty UK
    UK
    Empty UK

UK
    Log  In UK

UK With Return
    [Return]  This is a return value

UK With Empty Return
    [Return]
