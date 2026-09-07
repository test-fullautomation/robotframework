*** Settings ***
Suite Teardown       Recursion With Run Keyword

*** Variables ***
${LIMIT EXCEEDED}    Maximum limit of started keywords and control structures exceeded.
${PSTD FAILED}       \n\nAlso parent suite teardown unknown:\n${LIMIT EXCEEDED}

*** Test Cases ***
Infinite recursion
    [Documentation]    UNKNOWN ${LIMIT EXCEEDED}${PSTD FAILED}
    Recursion

Infinite cyclic recursion
    [Documentation]    UNKNOWN ${LIMIT EXCEEDED}${PSTD FAILED}
    Cyclic recursion

Infinite recursion with Run Keyword
    [Documentation]    UNKNOWN ${LIMIT EXCEEDED}${PSTD FAILED}
    Recursion with Run Keyword

Infinitely recursive for loop
    [Documentation]    UNKNOWN ${LIMIT EXCEEDED}${PSTD FAILED}
    Infinitely recursive for loop

Recursion below the recursion limit is ok
    [Documentation]    UNKNOWN Still below recursion limit!${PSTD FAILED}
    Limited recursion
    Recursive for loop    10
    Failing limited recursion

*** Keywords ***
Recursion
    Recursion

Limited recursion
    [Arguments]    ${limit}=${25}
    Log    ${limit}
    IF    ${limit} > 0    Limited recursion   ${limit - 1}

Failing limited recursion
    [Arguments]    ${limit}=${50}
    Log    ${limit}
    IF    ${limit} < 0    Fail    Still below recursion limit!
    Failing limited recursion     ${limit - 1}

Cyclic recursion
    Cyclic recursion 2

Cyclic recursion 2
    Cyclic recursion 3

Cyclic recursion 3
    Cyclic recursion

Recursion With Run Keyword
    Run Keyword    Recursion With Run Keyword

Recursive for loop
    [Arguments]    ${rounds}
    FOR    ${i}    IN RANGE    ${rounds}
        Recursive for loop    ${i}
    END

Infinitely recursive for loop
    FOR    ${var}    IN    recursion
        Infinitely recursive for loop
    END
