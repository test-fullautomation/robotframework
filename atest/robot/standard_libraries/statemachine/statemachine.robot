*** Settings ***
Documentation     Acceptance tests for the StateMachine standard library.
Library           StateMachine
Library           OperatingSystem
Test Teardown     Reset State Machine

*** Variables ***
${CHECKPOINT}     ${OUTPUT DIR}${/}statemachine_checkpoint.json

*** Test Cases ***
Machine Runs To Final State
    Set Test Variable    ${CYCLES}    ${0}
    Define State    INIT    enter=Prepare
    Define State    WORK    during=Increment Cycles
    Define State    DONE    final=True
    Define Transition    INIT    WORK
    Define Transition    WORK    DONE    condition=$CYCLES >= 3
    Run State Machine    initial=INIT    poll_interval=0.05 s
    Should Be Equal    ${CYCLES}    ${3}
    ${state}=    Get Current State
    Should Be Equal    ${state}    DONE

Transitions Are Evaluated In Definition Order
    Set Test Variable    ${N}    ${10}
    Define State    A
    Define State    FIRST    final=True
    Define State    SECOND    final=True
    Define Transition    A    FIRST     condition=$N > 5
    Define Transition    A    SECOND    condition=$N > 1
    Run State Machine    initial=A    poll_interval=0.05 s
    ${state}=    Get Current State
    Should Be Equal    ${state}    FIRST

Failure Routes To On Error State
    Define State    INIT    enter=Break Something    on_error=FAULT
    Define State    FAULT    enter=Collect Diagnostics    final=True
    Define State    DONE    final=True
    Define Transition    INIT    DONE
    Run State Machine    initial=INIT
    ${state}=    Get Current State
    Should Be Equal    ${state}    FAULT

Failure Without On Error Fails The Test
    Define State    INIT    enter=Break Something
    Define State    DONE    final=True
    Define Transition    INIT    DONE
    Run Keyword And Expect Error    State 'INIT' failed: Simulated hardware fault
    ...    Run State Machine    initial=INIT

Invalid Machine Is Rejected Before Running
    Define State    LONELY    # not final, no outgoing transitions
    Run Keyword And Expect Error    *no outgoing transitions*
    ...    Run State Machine    initial=LONELY

Stuck Keyword Is Interrupted By State Timeout
    [Documentation]    The enter keyword would sleep 60 s; the state timeout
    ...                interrupts it preemptively and routes to FAULT.
    Define State    SLOW    enter=Sleep A Long Time    timeout=0.3 s
    ...    on_error=FAULT
    Define State    FAULT    final=True
    Define State    DONE    final=True
    Define Transition    SLOW    DONE
    ${start}=    Get Time    epoch
    Run State Machine    initial=SLOW    poll_interval=0.05 s
    ${end}=    Get Time    epoch
    Should Be True    ${end} - ${start} < 30    Timeout did not interrupt
    ${state}=    Get Current State
    Should Be Equal    ${state}    FAULT

Interrupted Run Resumes From Checkpoint
    Remove File    ${CHECKPOINT}
    Set Test Variable    ${N}    ${0}
    Define State    INIT    enter=Prepare
    Define State    WORK    during=Increment N
    Define State    DONE    final=True
    Define Transition    INIT    WORK
    Define Transition    WORK    DONE    condition=$N >= 5
    Checkpoint Variable    \${N}
    Run Keyword And Expect Error    *max_duration*exceeded*
    ...    Run State Machine    initial=INIT    max_duration=0.4 s
    ...    checkpoint=${CHECKPOINT}    poll_interval=0.1 s
    File Should Exist    ${CHECKPOINT}
    ${before}=    Set Variable    ${N}
    Should Be True    ${before} > 0
    Run State Machine    initial=INIT    max_duration=1 min
    ...    checkpoint=${CHECKPOINT}    poll_interval=0.1 s
    Should Be Equal    ${N}    ${5}
    File Should Not Exist    ${CHECKPOINT}
    ${state}=    Get Current State
    Should Be Equal    ${state}    DONE

Unserializable Checkpoint Variable Fails Fast
    ${obj}=    Evaluate    object()
    Set Test Variable    ${OBJ}    ${obj}
    Run Keyword And Expect Error    *not JSON serializable*
    ...    Checkpoint Variable    \${OBJ}

Max Visits Guard Stops Transition Ping Pong
    Define State    A
    Define State    B
    Define Transition    A    B
    Define Transition    B    A
    Run Keyword And Expect Error    *max_visits 6 reached*
    ...    Run State Machine    initial=A    max_visits=6    poll_interval=0.02 s

On Error Routing Cycle Is Detected
    Define State    A    enter=Break Something    on_error=B
    Define State    B    enter=Break Something    on_error=A
    Define State    C    final=True
    Define Transition    A    C
    Define Transition    B    C
    Run Keyword And Expect Error    *cycle detected*
    ...    Run State Machine    initial=A

State Statistics Are Available
    Set Test Variable    ${CYCLES}    ${0}
    Define State    INIT    enter=Prepare
    Define State    WORK    during=Increment Cycles
    Define State    DONE    final=True
    Define Transition    INIT    WORK
    Define Transition    WORK    DONE    condition=$CYCLES >= 2
    Run State Machine    initial=INIT    poll_interval=0.05 s
    ${stats}=    Get State Statistics
    Should Be Equal    ${stats['WORK']['visits']}    ${1}
    Should Be True    ${stats['WORK']['elapsed']} >= 0
    Should Be True    ${stats['INIT']['visits']} == 1

State Keywords Accept Arguments
    Set Test Variable    ${TOTAL}    ${0}
    ${enter}=    Create List    Add To Total    ${5}
    ${during}=    Create List    Add To Total    ${2}
    Define State    INIT    enter=${enter}
    Define State    WORK    during=${during}
    Define State    DONE    final=True
    Define Transition    INIT    WORK
    Define Transition    WORK    DONE    condition=$TOTAL >= 9
    Run State Machine    initial=INIT    poll_interval=0.05 s
    Should Be Equal    ${TOTAL}    ${9}

Machine Loaded From JSON File
    Set Test Variable    ${N}    ${0}
    ${machine}=    Catenate    SEPARATOR=\n
    ...    {"states": {
    ...      "INIT": {"enter": "Prepare"},
    ...      "WORK": {"during": ["Add N", "3"]},
    ...      "DONE": {"final": true}},
    ...    "transitions": [
    ...      "INIT -> WORK",
    ...      {"source": "WORK", "target": "DONE", "condition": "$N >= 6"}],
    ...    "checkpoint_variables": ["\${N}"]}
    Create File    ${OUTPUT DIR}${/}machine.json    ${machine}
    Load State Machine    ${OUTPUT DIR}${/}machine.json
    Run State Machine    initial=INIT    poll_interval=0.05 s
    Should Be Equal    ${N}    ${6}
    ${state}=    Get Current State
    Should Be Equal    ${state}    DONE
    [Teardown]    Run Keywords    Reset State Machine
    ...    AND    Remove File    ${OUTPUT DIR}${/}machine.json

Invalid Machine File Is Rejected
    Create File    ${OUTPUT DIR}${/}bad.json
    ...    {"states": {"A": {"final": true, "colour": "red"}}}
    Run Keyword And Expect Error    *unknown option*colour*
    ...    Load State Machine    ${OUTPUT DIR}${/}bad.json
    [Teardown]    Run Keywords    Reset State Machine
    ...    AND    Remove File    ${OUTPUT DIR}${/}bad.json

Independent Named Machines
    [Documentation]    Two machines share state names without clashing.
    Set Test Variable    ${NA}    ${0}
    Set Test Variable    ${NB}    ${0}
    Define State    INIT    machine=A
    Define State    WORK    during=Increment NA    machine=A
    Define State    DONE    final=True    machine=A
    Define Transition    INIT    WORK    machine=A
    Define Transition    WORK    DONE    condition=$NA >= 2    machine=A
    Define State    INIT    machine=B
    Define State    WORK    during=Increment NB    machine=B
    Define State    DONE    final=True    machine=B
    Define Transition    INIT    WORK    machine=B
    Define Transition    WORK    DONE    condition=$NB >= 3    machine=B
    Run State Machine    initial=INIT    machine=A    poll_interval=0.05 s
    Run State Machine    initial=INIT    machine=B    poll_interval=0.05 s
    Should Be Equal    ${NA}    ${2}
    Should Be Equal    ${NB}    ${3}
    ${state a}=    Get Current State    machine=A
    ${state b}=    Get Current State    machine=B
    Should Be Equal    ${state a}    DONE
    Should Be Equal    ${state b}    DONE

Concurrent Machines In Threads
    [Documentation]    BG machine runs in a THREAD and feeds ${N}; the FG
    ...                machine in the main flow waits for that progress and
    ...                then stops the BG machine via the shared library
    ...                instance (guards of a machine running inside a THREAD
    ...                do not see variables set in that same thread - see
    ...                library docs; control such machines with
    ...                Stop State Machine instead).
    Set Test Variable    ${N}    ${0}
    Define State    WORK    during=Increment N    machine=BG
    Define State    DONE    final=True    machine=BG
    Define Transition    WORK    DONE    condition=$N >= 999999    machine=BG
    Define State    WAIT    machine=FG
    Define State    DONE    final=True    machine=FG
    Define Transition    WAIT    DONE    condition=$N >= 3    machine=FG
    THREAD    BG_SM    True
        Run State Machine    initial=WORK    machine=BG    poll_interval=0.1 s
    END
    Run State Machine    initial=WAIT    machine=FG    poll_interval=0.1 s
    Stop State Machine    machine=BG
    Wait For Thread    BG_SM    timeout=10 s
    ${state bg}=    Get Current State    machine=BG
    ${state fg}=    Get Current State    machine=FG
    Should Be Equal    ${state bg}    WORK    # stopped gracefully mid-state
    Should Be Equal    ${state fg}    DONE
    Should Be True    ${N} >= 3

Graceful Stop From A Keyword
    Set Test Variable    ${N}    ${0}
    Define State    INIT    enter=Prepare
    Define State    WORK    during=Increment N And Stop At 3
    Define State    DONE    final=True
    Define Transition    INIT    WORK
    Define Transition    WORK    DONE    condition=$N >= 999999
    Run State Machine    initial=INIT    poll_interval=0.05 s
    ${state}=    Get Current State
    Should Be Equal    ${state}    WORK
    Should Be Equal    ${N}    ${3}

*** Keywords ***
Prepare
    Log    Preparing the device under test

Increment Cycles
    ${c}=    Evaluate    $CYCLES + 1
    Set Test Variable    ${CYCLES}    ${c}

Increment N
    ${n}=    Evaluate    $N + 1
    Set Test Variable    ${N}    ${n}

Increment N And Stop At 3
    Increment N
    IF    $N >= 3
        Stop State Machine
    END

Add To Total
    [Arguments]    ${amount}
    ${t}=    Evaluate    $TOTAL + $amount
    Set Test Variable    ${TOTAL}    ${t}

Add N
    [Arguments]    ${amount}
    ${n}=    Evaluate    $N + int($amount)
    Set Test Variable    ${N}    ${n}

Increment NA
    ${n}=    Evaluate    $NA + 1
    Set Test Variable    ${NA}    ${n}

Increment NB
    ${n}=    Evaluate    $NB + 1
    Set Test Variable    ${NB}    ${n}

Break Something
    Fail    Simulated hardware fault

Collect Diagnostics
    Log    Collecting diagnostics after fault

Sleep A Long Time
    Sleep    60 s
