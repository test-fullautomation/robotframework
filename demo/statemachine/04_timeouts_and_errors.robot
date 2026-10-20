*** Settings ***
Documentation     Example 4 - what happens when things go wrong.
...
...               Three safety nets for machines that run for days:
...               - `on_error`: a failing state keyword routes the machine
...                 to a recovery state instead of killing the run
...               - `timeout`: a visit that takes too long is treated the
...                 same way (routed to `on_error`)
...               - `max_visits`: a wrong guard that makes two states
...                 bounce forever is caught with a clear failure instead
...                 of spinning until `max_duration`
Library           StateMachine
Test Teardown     Reset State Machine

*** Test Cases ***
Failing Keyword Routes To Recovery
    Define State    WORK        during=Explode    on_error=RECOVERY
    Define State    RECOVERY    enter=Collect Evidence    final=True
    Define State    DONE        final=True
    Define Transition    WORK    DONE    condition=False
    Run State Machine    initial=WORK    poll_interval=0.1 s
    ${state}=    Get Current State
    Should Be Equal    ${state}    RECOVERY

Stuck State Hits Its Timeout
    [Documentation]    STARTUP never sets ${READY}, so its guard can never
    ...                fire; the 0.5 s visit timeout routes it to RECOVERY.
    Set Suite Variable    ${READY}    ${False}
    Define State    STARTUP     during=Wait A Bit    timeout=0.5 s    on_error=RECOVERY
    Define State    RECOVERY    enter=Collect Evidence    final=True
    Define State    DONE        final=True
    Define Transition    STARTUP    DONE    condition=$READY
    Run State Machine    initial=STARTUP    poll_interval=0.1 s
    ${state}=    Get Current State
    Should Be Equal    ${state}    RECOVERY

Transition Ping-Pong Is Caught By Max Visits
    [Documentation]    Both guards are always true - a classic condition
    ...                bug. Without max_visits this would bounce until
    ...                max_duration; with it the run fails immediately with
    ...                a message that names the problem.
    Define State    PING
    Define State    PONG
    Define State    DONE    final=True
    Define Transition    PING    PONG    condition=True
    Define Transition    PONG    PING    condition=True
    Run Keyword And Expect Error    *max_visits*
    ...    Run State Machine    initial=PING    max_visits=10    poll_interval=0.05 s
    ${stats}=    Get State Statistics
    Log    Where the visits went: ${stats}

*** Keywords ***
Explode
    Fail    Simulated hardware fault

Wait A Bit
    Sleep    0.15 s

Collect Evidence
    Log    Recovery state reached - collecting diagnostics    WARN
