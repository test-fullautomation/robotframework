*** Settings ***
Suite Setup       Log    Suite setup executed
Suite Teardown    Non-Existing Keyword
Default Tags      tag1    tag2

*** Variables ***
${ERROR}    Parent suite teardown unknown:\nNo keyword with name 'Non-Existing Keyword' found.

*** Test Cases ***
Test 1
    [Documentation]    UNKNOWN ${ERROR}
    Log    This is executed normally
    My Keyword

Test 2
    [Documentation]    UNKNOWN ${ERROR}
    Log    All tests pass here

*** Keywords ***
My Keyword
    Log    User keywords work normally
    No Operation
