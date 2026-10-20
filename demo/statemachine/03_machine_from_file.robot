*** Settings ***
Documentation     Example 3 - the machine as data instead of keywords.
...
...               The same coffee machine as example 2, but states and
...               transitions live in `coffee_machine.json`. This keeps the
...               .robot file focused on the state keywords and lets
...               non-programmers review or edit the machine structure.
...               A `.yaml` file with the same structure works identically
...               when the pyyaml module is installed.
...
...               New here: per-state keyword ARGUMENTS - PREHEAT's enter
...               keyword is `["Set Power Level", "1200"]` in the file.
Library           StateMachine
Suite Setup       Load State Machine    ${CURDIR}${/}coffee_machine.json
Suite Teardown    Reset State Machine

*** Test Cases ***
Brew Two Cups From A File Definition
    Set Suite Variable    ${TEMP}    ${20}
    Set Suite Variable    ${CUPS}    ${0}
    Run State Machine    initial=PREHEAT    poll_interval=0.1 s
    Should Be Equal    ${CUPS}    ${2}
    ${state}=    Get Current State
    Should Be Equal    ${state}    DONE

*** Keywords ***
Set Power Level
    [Arguments]    ${watts}
    Log    Heater set to ${watts} W

Heat Up
    ${t}=    Evaluate    $TEMP + 25
    Set Suite Variable    ${TEMP}    ${t}

Fill Cup
    ${c}=    Evaluate    $CUPS + 1
    Set Suite Variable    ${CUPS}    ${c}

Ding
    Log    *Ding* - brewing finished
