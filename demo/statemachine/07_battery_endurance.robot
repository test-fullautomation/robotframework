*** Settings ***
Documentation     Example 7, the flagship: the battery endurance test from the
...               documentation, scaled from 48 hours down to seconds.
...               Shows: machine loaded from a JSON file, keywords attached
...               to states, guarded transitions, Watchdog supervision,
...               checkpointing, per-state statistics and fault routing.
...
...               Abbreviations: DUT = device under test, SOC = battery
...               state of charge in percent, CYCLES = completed
...               charge/discharge cycles.
Library           StateMachine
Library           Watchdog
Suite Setup       Load State Machine    ${CURDIR}${/}battery_machine.json    machine=BATTERY
Suite Teardown    Run Keywords    Reset State Machine    AND    Reset Watchdogs

*** Variables ***
${CHECKPOINT}     ${OUTPUT DIR}${/}battery_checkpoint.json

*** Test Cases ***
Battery Endurance Run
    [Documentation]    Three full charge/discharge cycles under Watchdog
    ...                supervision, checkpointed after every transition.
    Initialize Battery Simulation
    Configure Watchdog    stall_timeout=5 s    heartbeat=2 s
    THREAD    SUPERVISOR    True
        Run Watchdog
    END
    Run State Machine    initial=INIT    machine=BATTERY
    ...    checkpoint=${CHECKPOINT}    max_duration=1 min    poll_interval=0.1 s
    Stop Watchdog
    Watchdog Should Not Have Fired
    Should Be Equal    ${CYCLES}    ${3}
    ${state}=    Get Current State    machine=BATTERY
    Should Be Equal    ${state}    DONE
    ${stats}=    Get State Statistics    machine=BATTERY
    Log    Per-state statistics: ${stats}
    Should Be True    ${stats['CHARGING']['visits']} >= 3
    File Should Not Exist If Supported    ${CHECKPOINT}

Fault Is Routed To Diagnostics
    [Documentation]    An injected failure during charging routes the
    ...                machine into the FAULT state instead of hanging or
    ...                crashing the run.
    Initialize Battery Simulation
    Set Suite Variable    ${INJECT_FAULT}    ${True}
    Run State Machine    initial=INIT    machine=BATTERY
    ...    resume=False    poll_interval=0.1 s
    ${state}=    Get Current State    machine=BATTERY
    Should Be Equal    ${state}    FAULT
    [Teardown]    Set Suite Variable    ${INJECT_FAULT}    ${False}

*** Keywords ***
Initialize Battery Simulation
    Set Suite Variable    ${DUT_READY}       ${False}
    Set Suite Variable    ${SOC}             ${50}
    Set Suite Variable    ${CYCLES}          ${0}
    Set Suite Variable    ${INJECT_FAULT}    ${False}

Setup DUT
    Log    Connecting and initializing the device under test
    Set Suite Variable    ${DUT_READY}    ${True}

Start Charging
    Log    Charger enabled (SOC ${SOC} %)

Charge Step
    IF    $INJECT_FAULT
        Fail    Simulated cell overtemperature
    END
    ${soc}=    Evaluate    min($SOC + 9, 100)
    Set Suite Variable    ${SOC}    ${soc}
    Feed Watchdog If Running

Start Discharging
    Log    Load enabled (SOC ${SOC} %)

Discharge Step
    ${soc}=    Evaluate    max($SOC - 9, 0)
    Set Suite Variable    ${SOC}    ${soc}
    Feed Watchdog If Running

Complete Cycle
    ${c}=    Evaluate    $CYCLES + 1
    Set Suite Variable    ${CYCLES}    ${c}
    Log    Completed charge/discharge cycle ${CYCLES}

Collect Diagnostics
    Log    Reading fault registers and dumping cell voltages    WARN

Feed Watchdog If Running
    ${status}=    Run Keyword And Ignore Error    Get Watchdog Status
    IF    $status[0] == 'PASS' and $status[1] == 'RUNNING'
        Feed Watchdog
    END

File Should Not Exist If Supported
    [Arguments]    ${path}
    [Documentation]    Completed machines remove their checkpoint file.
    ${exists}=    Evaluate    os.path.isfile(r'''${path}''')    modules=os
    Should Not Be True    ${exists}    Checkpoint should be removed on success
