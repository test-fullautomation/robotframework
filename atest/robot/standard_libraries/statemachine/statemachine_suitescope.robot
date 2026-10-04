*** Settings ***
Documentation     Suite-scope behaviour of the StateMachine library:
...               definitions shared over tests, machines running in
...               suite-scoped THREADs reachable from later tests, and
...               graceful stop when the hosting thread's scope ends.
Library           StateMachine
Library           OperatingSystem
Suite Setup       Define Shared Machine
Suite Teardown    Reset State Machine

*** Variables ***
${CP}             ${OUTPUT DIR}${/}suitescope_checkpoint.json

*** Test Cases ***
Definitions From Suite Setup Are Available
    Set Test Variable    ${SHN}    ${0}
    Run State Machine    initial=INIT    machine=SHARED    poll_interval=0.05 s
    ${state}=    Get Current State    machine=SHARED
    Should Be Equal    ${state}    DONE

Definitions Survive Into The Next Test
    Set Test Variable    ${SHN}    ${0}
    Run State Machine    initial=INIT    machine=SHARED    poll_interval=0.05 s
    Should Be Equal    ${SHN}    ${2}

Start Long Running Machine In Suite Thread
    Set Suite Variable    ${GO}    ${0}
    Remove File    ${CP}
    Define State    WORK    machine=LONGRUN
    Define State    DONE    final=True    machine=LONGRUN
    Define Transition    WORK    DONE    condition=$GO >= 1    machine=LONGRUN
    THREAD    LONGRUN_SM    False
        Run State Machine    initial=WORK    machine=LONGRUN
        ...    checkpoint=${CP}    poll_interval=0.1 s
    END
    Sleep    0.4 s
    Log    machine started in a suite-scoped thread

Machine In Suite Thread Is Reachable From A Later Test
    ${state}=    Get Current State    machine=LONGRUN
    Should Be Equal    ${state}    WORK
    Stop State Machine    machine=LONGRUN
    Wait For Thread    LONGRUN_SM    timeout=10 s
    File Should Exist    ${CP}    # a stopped machine keeps its checkpoint
    [Teardown]    Remove File    ${CP}

Start Machine In Test Scoped Thread And Let It Be Reaped
    Set Suite Variable    ${GO2}    ${0}
    Define State    WORK    machine=REAPED
    Define State    DONE    final=True    machine=REAPED
    Define Transition    WORK    DONE    condition=$GO2 >= 1    machine=REAPED
    THREAD    REAPED_SM    True
        Run State Machine    initial=WORK    machine=REAPED    poll_interval=0.1 s
    END
    Sleep    0.3 s
    Log    test ends now; the thread reaper stops REAPED_SM

Reaped Machine Stopped Gracefully
    [Documentation]    The machine hosted in the test-scoped thread of the
    ...                previous test must have stopped gracefully (state
    ...                kept, no on_error routing) when its thread was reaped.
    ${state}=    Get Current State    machine=REAPED
    Should Be Equal    ${state}    WORK

Concurrent Machines Must Use Different Checkpoint Files
    Set Suite Variable    ${GO3}    ${0}
    Remove File    ${CP}
    Define State    WORK    machine=HOLDER
    Define State    DONE    final=True    machine=HOLDER
    Define Transition    WORK    DONE    condition=$GO3 >= 1    machine=HOLDER
    Define State    WORK    machine=INTRUDER
    Define State    DONE    final=True    machine=INTRUDER
    Define Transition    WORK    DONE    condition=$GO3 >= 1    machine=INTRUDER
    THREAD    HOLDER_SM    True
        Run State Machine    initial=WORK    machine=HOLDER
        ...    checkpoint=${CP}    poll_interval=0.1 s
    END
    Sleep    0.4 s
    Run Keyword And Expect Error    *already used by the running state machine 'HOLDER'*
    ...    Run State Machine    initial=WORK    machine=INTRUDER    checkpoint=${CP}
    Stop State Machine    machine=HOLDER
    Wait For Thread    HOLDER_SM    timeout=10 s
    [Teardown]    Remove File    ${CP}

*** Keywords ***
Define Shared Machine
    Define State    INIT    machine=SHARED
    Define State    WORK    during=Increment SHN    machine=SHARED
    Define State    DONE    final=True    machine=SHARED
    Define Transition    INIT    WORK    machine=SHARED
    Define Transition    WORK    DONE    condition=$SHN >= 2    machine=SHARED

Increment SHN
    ${n}=    Evaluate    $SHN + 1
    Set Test Variable    ${SHN}    ${n}
