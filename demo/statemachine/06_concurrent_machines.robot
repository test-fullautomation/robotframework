*** Settings ***
Documentation     Example 6 - two named machines running concurrently: a STIMULATOR
...               machine feeds data from inside a THREAD block while the
...               MAIN machine in the test flow waits for that progress.
...               The worker-hosted machine is controlled from the main
...               flow via Stop State Machine (guards inside a THREAD do
...               not see same-thread variable writes - see the docs).
Library           StateMachine
Test Teardown     Reset State Machine

*** Test Cases ***
Stimulator Feeds The Main Machine
    Set Suite Variable    ${TICKS}    ${0}
    # STIMULATOR machine: emits data forever until stopped
    Define State    EMIT    during=Emit Tick    machine=STIMULATOR
    Define State    DONE    final=True    machine=STIMULATOR
    Define Transition    EMIT    DONE    condition=$TICKS >= 999999    machine=STIMULATOR
    # MAIN machine: waits until enough data arrived
    Define State    WAIT    machine=MAIN
    Define State    DONE    final=True    machine=MAIN
    Define Transition    WAIT    DONE    condition=$TICKS >= 5    machine=MAIN
    THREAD    STIM_SM    True
        Run State Machine    initial=EMIT    machine=STIMULATOR    poll_interval=0.1 s
    END
    Run State Machine    initial=WAIT    machine=MAIN    poll_interval=0.1 s
    Stop State Machine    machine=STIMULATOR
    Wait For Thread    STIM_SM    timeout=10 s
    ${main}=    Get Current State    machine=MAIN
    ${stim}=    Get Current State    machine=STIMULATOR
    Should Be Equal    ${main}    DONE
    Should Be Equal    ${stim}    EMIT    # stopped gracefully mid-state
    Should Be True    ${TICKS} >= 5
    ${stats}=    Get State Statistics    machine=STIMULATOR
    Log    Stimulator statistics: ${stats}

*** Keywords ***
Emit Tick
    ${t}=    Evaluate    $TICKS + 1
    Set Suite Variable    ${TICKS}    ${t}
