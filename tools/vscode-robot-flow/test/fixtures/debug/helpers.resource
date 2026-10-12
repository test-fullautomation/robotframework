*** Keywords ***
Greet
    [Arguments]    ${who}
    Log    hello ${who}
    ${answer}=    Set Variable    42
    Log    the answer is ${answer}
