*** Settings ***
Documentation     A configurable endurance run for demonstrating segmented output,
...               bounded memory and crash recovery. Ten minutes by default;
...               ``--variable DURATION:8h`` for the real thing, and
...               ``--variable CRASH_AFTER:90s`` to simulate a power loss.
Library           endurance_support.py

*** Variables ***
${DURATION}       10 min
${CYCLE}          2 s
${CRASH AFTER}    ${NONE}

*** Test Cases ***
Endurance Run
    [Documentation]    Runs measurement cycles until the deadline, with a MONITOR
    ...    thread reporting progress and output size every 5 seconds.
    ${deadline}=    Deadline After    ${DURATION}
    THREAD    MONITOR    True
        Monitor Until    ${deadline}    every=5 s
    END
    ${n}=    Set Variable    ${0}
    WHILE    time.time() < ${deadline}    limit=NONE
        ${n}=    Evaluate    $n + 1
        Run Cycle    ${n}
        Maybe Crash    ${CRASH AFTER}
        Sleep    ${CYCLE}
    END
    Log    Completed ${n} cycles in ${DURATION}.

*** Keywords ***
Run Cycle
    [Arguments]    ${n}
    ${m}=    Measure    ${n}
    Log    Cycle ${n}: ${m}[voltage] V, ${m}[current] A, ${m}[temperature] °C
    Should Be True    11.0 <= ${m}[voltage] <= 13.5    Voltage out of range in cycle ${n}
    Should Be True    ${m}[temperature] < 95    Overtemperature in cycle ${n}
    Run Keyword If    ${n} % 25 == 0    Log    Sensor drift check in cycle ${n}: ${m}[drift] mV    WARN
    Run Keyword If    ${n} % 100 == 0    Log Snapshot    ${n}
