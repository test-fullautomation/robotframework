*** Settings ***
Documentation     Example 5 - interruption and resume: the first test aborts on
...               max_duration (simulating a crash/power loss at "hour 40"),
...               leaving a checkpoint; the second test resumes from it and
...               completes. The machine is defined once in the suite setup
...               (SUITE library scope) and shared by both tests.
Library           StateMachine
Library           OperatingSystem
Suite Setup       Define Counting Machine
Suite Teardown    Reset State Machine

*** Variables ***
${CHECKPOINT}     ${OUTPUT DIR}${/}resume_checkpoint.json

*** Test Cases ***
Run Is Interrupted
    Remove File    ${CHECKPOINT}
    # Test scope on purpose: checkpoint variables are restored as TEST
    # variables on resume; a suite variable of the same name would be
    # shadowed by the restored value.
    Set Test Variable    ${N}    ${0}
    Run Keyword And Expect Error    *max_duration*exceeded*
    ...    Run State Machine    initial=WORK    max_duration=0.6 s
    ...    checkpoint=${CHECKPOINT}    poll_interval=0.15 s
    File Should Exist    ${CHECKPOINT}
    Log    Interrupted with N=${N}; progress is checkpointed
    Should Be True    ${N} > 0

Run Resumes And Completes
    [Documentation]    resume=AUTO picks up the checkpoint: state, counters
    ...                and the registered ${N} variable are restored.
    Run State Machine    initial=WORK    max_duration=1 min
    ...    checkpoint=${CHECKPOINT}    poll_interval=0.15 s
    Should Be Equal    ${N}    ${12}
    File Should Not Exist    ${CHECKPOINT}
    ${state}=    Get Current State
    Should Be Equal    ${state}    DONE

*** Keywords ***
Define Counting Machine
    Define State    WORK    during=Count Up
    Define State    DONE    final=True
    Define Transition    WORK    DONE    condition=$N >= 12
    Checkpoint Variable    \${N}

Count Up
    ${n}=    Evaluate    $N + 1
    Set Test Variable    ${N}    ${n}
