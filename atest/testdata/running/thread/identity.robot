*** Settings ***
Documentation     A thread is identified by a key of its own, not by its name,
...               and what it runs with is its own: see docs/thread/lifecycle.md.

*** Variables ***
${LEAK}           not-set

*** Test Cases ***
Same Name In A Loop
    FOR    ${i}    IN RANGE    3
        THREAD    dup    True
            Log    dup ${i}
            Sleep    0.2
        END
    END
    FOR    ${i}    IN RANGE    3
        Wait For Thread    dup
    END

Thread Inside IF False
    IF    ${False}
        THREAD    never    True
            Fail    never runs
        END
    END
    Log    no thread started

Same Name Across Tests A
    THREAD    shared    False
        Sleep    1
        Log    shared from A
    END
    Log    started shared in A

Same Name Across Tests B
    THREAD    shared    False
        Log    shared from B
    END
    Wait For Thread    shared
    Wait For Thread    shared
    Log    both done

Nested Threads
    THREAD    outer    True
        Log    outer start
        THREAD    inner    True
            Log    inner body
        END
        Wait For Thread    inner
        Log    outer end
    END
    Wait For Thread    outer

Worker Timeout Stays In The Worker
    THREAD    timed    True
        Timed Worker
    END
    Sleep    1
    Log    main slept through
    Wait For Thread    timed    fail_on_error=False

Worker Continue On Failure Stays In The Worker
    THREAD    cof    True
        Continue Worker
    END
    Sleep    0.2
    Run Keyword And Expect Error    first failure    Main Fails Then Logs
    Wait For Thread    cof

Variables Set In A Worker Reach The Main Thread
    THREAD    setter    True
        Set Test Variable    ${FROM_WORKER}    set-in-worker
        Set Suite Variable    ${SUITE_FROM_WORKER}    suite-set-in-worker
    END
    Wait For Thread    setter
    Should Be Equal    ${FROM_WORKER}    set-in-worker
    Should Be Equal    ${SUITE_FROM_WORKER}    suite-set-in-worker

A Suite Thread Sets A Test Variable After Its Test
    THREAD    leaker    False
        Sleep    1
        Set Test Variable    ${LEAK}    from-thread
        Log    leaker done
    END
    Log    leaker started

The Next Test Does Not See It
    Wait For Thread    leaker
    Should Be Equal    ${LEAK}    not-set
    Should Be Equal    ${SUITE_FROM_WORKER}    suite-set-in-worker

Flatten In The Main Thread Leaves The Worker Log Intact
    THREAD    logger    True
        FOR    ${i}    IN RANGE    6
            Log    worker msg ${i}
            Sleep    0.2
        END
    END
    Sleep    0.1
    Flat Main
    Wait For Thread    logger

*** Keywords ***
Timed Worker
    [Timeout]    0.3s
    Sleep    2

Continue Worker
    [Tags]    robot:continue-on-failure
    Sleep    1.5

Main Fails Then Logs
    Fail    first failure
    Log    AFTER FAIL

Flat Main
    [Tags]    robot:flatten
    Sleep    0.8
    Log    flat main
