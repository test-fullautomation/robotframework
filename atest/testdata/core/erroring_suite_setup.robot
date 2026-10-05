*** Settings ***
Suite Setup       Non-Existing Keyword
Suite Teardown    My TD

*** Test Cases ***
Test 1
    [Documentation]    UNKNOWN Parent suite setup unknown:
    ...    No keyword with name 'Non-Existing Keyword' found.
    Fail    This is not executed

Test 2
    [Documentation]    UNKNOWN Parent suite setup unknown:
    ...    No keyword with name 'Non-Existing Keyword' found.
    Fail    This is not executed

*** Keywords ***
My TD
    Log    Hello from suite teardown!
    No Operation
