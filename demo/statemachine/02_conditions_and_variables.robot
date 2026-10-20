*** Settings ***
Documentation     Example 2 - guarded transitions driven by variables.
...
...               A coffee machine: HEATING raises the temperature on every
...               poll (the `during` keyword), and the machine only moves to
...               BREWING once the guard `$TEMP >= 90` becomes true. BREWING
...               counts cups the same way. Guards are ordinary `Evaluate`
...               expressions; use `$name` to access variables.
...
...               Rules shown here:
...               - `during` runs once per poll while the state is active
...               - guards are re-evaluated after every poll
...               - guarded transitions are tried in definition order; an
...                 unconditional one acts as the fallback and must be
...                 defined last
Library           StateMachine
Suite Teardown    Reset State Machine

*** Test Cases ***
Brew Two Cups
    Set Suite Variable    ${TEMP}    ${20}
    Set Suite Variable    ${CUPS}    ${0}
    Define State    HEATING    during=Heat Up
    Define State    BREWING    during=Fill Cup
    Define State    DONE       final=True
    Define Transition    HEATING    BREWING    condition=$TEMP >= 90
    Define Transition    BREWING    DONE       condition=$CUPS >= 2
    Run State Machine    initial=HEATING    poll_interval=0.1 s
    Should Be Equal    ${CUPS}    ${2}
    ${state}=    Get Current State
    Should Be Equal    ${state}    DONE

Statistics Tell What Happened
    [Documentation]    Every state records how often it was visited and how
    ...                long it was active - useful assertions for long runs.
    ${stats}=    Get State Statistics
    Log    ${stats}
    Should Be True    ${stats['HEATING']['visits']} >= 1
    Should Be True    ${stats['BREWING']['visits']} >= 1

*** Keywords ***
Heat Up
    ${t}=    Evaluate    $TEMP + 25
    Set Suite Variable    ${TEMP}    ${t}
    Log    Water at ${TEMP} degrees

Fill Cup
    ${c}=    Evaluate    $CUPS + 1
    Set Suite Variable    ${CUPS}    ${c}
    Log    Cup ${CUPS} brewed
