*** Settings ***
Library    mylib.py

*** Test Cases ***
Adds
    ${sum}=    Add Numbers    1    2
    Should Be Equal As Integers    ${sum}    3
    Log    the sum is ${sum}
