*** Settings ***
Library    Collections
Library    String

*** Variables ***
${variable}    ${1}

*** Keywords ***

#
#    Threading base keywords
#
################################################################################
threading_base.thread1
    Log    entering thrd1    console=True
    Sleep    1
    Set Test Variable    ${var_thrd_1}    ${11}

threading_base.thread2
    Log    entering thrd2    console=True
    Sleep    1
    Set Test Variable    ${var_thrd_2}    ${21}

threading_base.thread3
    Log    entering thrd3    console=True
    Sleep    1
    Set Test Variable    ${var_thrd_3}    ${31}

#
#    Threading sync using RLock keywords
#
################################################################################
threading_rlock_base.thread1
    Log    Thread 1 is acquiring lock    console=True
    Thread RLock Acquire    test_lock
    Append To List    ${var_thrd}    11
    Sleep    2
    Log    Thread 1 is releasing lock    console=True
    Thread RLock Release    test_lock
    Send Thread Notification    threading_rlock_base.thread1_finished

threading_rlock_base.thread2
    Log    Thread 2 is acquiring lock    console=True
    Thread RLock Acquire    test_lock
    Append To List    ${var_thrd}    21
    Sleep    2
    Log    Thread 2 is releasing lock    console=True
    Thread RLock Release    test_lock
    Send Thread Notification    threading_rlock_base.thread2_finished


threading_rlock_base.thread2_with_time_out
    Log    Thread 2 is acquiring lock    console=True
    ${is_lock_acquired}=  Thread RLock Acquire    test_lock    timeout=1
    Run Keyword If    '${is_lock_acquired}'=='False'
    ...    Fail
    Append To List    ${var_thrd}    21
    Sleep    2
    Log    Thread 2 is releasing lock    console=True
    Thread RLock Release    test_lock
    Send Thread Notification    threading_rlock_base.thread2_finished

threading_rlock_base.thread2_non_block
    Log    Thread 2 is acquiring lock    console=True
    ${is_lock_acquired}=  Thread RLock Acquire    test_lock    blocking=${False}
    Run Keyword If    '${is_lock_acquired}'=='False'
    ...    Fail
    Append To List    ${var_thrd}    21
    Sleep    2
    Log    Thread 2 is releasing lock    console=True
    Thread RLock Release    test_lock
    Send Thread Notification    threading_rlock_base.thread2_finished

threading_rlock_base.thread1_reacquire
    Log    Thread 1 is acquiring lock for first append    console=True
    Thread RLock Acquire    test_lock
    Append To List    ${var_thrd}    Thread_1_First
    Log    Thread 1 is releasing lock after first append    console=True
    Thread RLock Release    test_lock
    Sleep    1  # Simulate waiting to acquire lock again
    
    Log    Thread 1 is acquiring lock for second append    console=True
    Thread RLock Acquire    test_lock
    Append To List    ${var_thrd}    Thread_1_Second
    Log    Thread 1 is releasing lock after second append    console=True
    Thread RLock Release    test_lock
    Send Thread Notification    threading_rlock_base.thread1_finished

threading_rlock_base.thread2_reacquire
    Log    Thread 2 is acquiring lock    console=True
    Thread RLock Acquire    test_lock
    Append To List    ${var_thrd}    Thread_2_First
    Log    Thread 2 is releasing lock    console=True
    Thread RLock Release    test_lock
    Send Thread Notification    threading_rlock_base.thread2_finished


#
#    Threading sync base keywords
#
################################################################################
threading_sync_base.thread1
    Log    entering thrd1    console=True
    Sleep    3
    Append To List    ${var_thrd}    11
    Send Thread Notification    threading_sync_base.thread1_finished    0

threading_sync_base.thread2
    Log    entering thrd2    console=True
    Wait Thread Notification    threading_sync_base.thread1_finished    timeout=5000
    Sleep    2
    Append To List    ${var_thrd}    21
    Send Thread Notification    threading_sync_base.thread2_finished    0

threading_sync_base.thread3
    Log    entering thrd3    console=True
    Wait Thread Notification    threading_sync_base.thread2_finished    timeout=5000
    Sleep    1
    Append To List    ${var_thrd}    31
    Send Thread Notification    threading_sync_base.thread3_finished    0


#
#    Threading sync named base keywords
#
################################################################################
threading_sync_named_base.thread1
    Log    entering thrd1    console=True
    Sleep    3
    Append To List    ${var_thrd}    11
    Send Thread Notification    threading_sync_named_base.thread1_finished    0    TEST_THREAD2

threading_sync_named_base.thread2
    Log    entering thrd2    console=True
    Wait Thread Notification    threading_sync_named_base.thread1_finished    timeout=5000
    Sleep    2
    Append To List    ${var_thrd}    21
    Send Thread Notification    threading_sync_named_base.thread2_finished    0    TEST_THREAD3

threading_sync_named_base.thread3
    Log    entering thrd3    console=True
    Wait Thread Notification    threading_sync_named_base.thread2_finished    timeout=5000
    Sleep    1
    Append To List    ${var_thrd}    31
    Send Thread Notification    threading_sync_named_base.thread3_finished    0    MainThread

#
#    Threading payload base keywords
#
################################################################################
threading_payload_base.thread1
    Log    entering thrd1    console=True
    ${payload} =    Evaluate    {'CLIENT':{'NAME':'NAME','ID':1},'SERVER': {'ID':2,'IP':'172.17.0.4','PORT':'12345'},'SERVICE':{'ID':'ECHOSERVICE'}}
    #Sleep    0.25
    Send Thread Notification    threading_payload_base.thread1_finished    ${payload}

#
#    Threading payload two notifications keywords
#
################################################################################
threading_payload_two_notifications.thread1
    Log    entering thrd1    console=True
    ${payload1} =    Evaluate    {'CLIENT':{'NAME':'NAME','ID':1},'SERVER': {'ID':2,'IP':'172.17.0.4','PORT':'12345'},'SERVICE':{'ID':'ECHOSERVICE'}}
    ${payload2} =    Evaluate    {'CLIENT':{'NAME':'NAME','ID':11},'SERVER': {'ID':22,'IP':'172.17.0.4','PORT':'12345'},'SERVICE':{'ID':'ECHOSERVICE'}}
    
    # notifications are intendedly swapped (first payload2, then  payload1)
    Send Thread Notification    threading_payload_two_notifications.thread1_payload2_finished    ${payload2}
    Send Thread Notification    threading_payload_two_notifications.thread1_payload1_finished    ${payload1}

#
#   Threading From Keyword base
#
################################################################################
threading_from_keyword_base.dispatch
    ## THREAD temporary deactivated due to https://github.com/test-fullautomation/robotframework/issues/49
    #THREAD    TEST_THREAD1    False
        Log    This is a message    console=True
    #END

#
#   Threading Keyword Multiple Time Called
#
################################################################################
threading_keyword_multiple_time_called.keyword
    [Arguments]    ${msg}=""    ${waittime}=5
    Log    Entering Thread ${msg}    console=True
    sleep    ${waittime}
    Append To List     ${var_thrd}    ${msg}
    Log    Closing Thread ${msg}    console=True


*** Test Cases ***
Thread executed
    THREAD    TEST_THREAD    False
        SLEEP  2
        LOG   inside threading
    END
    LOG    CONTINUE IN MAIN THREAD
    SLEEP    5



Thread change variable
    ${var}=   set variable       ${1}
    THREAD    TEST_THREAD    False
        SLEEP  1
        set test variable      ${var}     ${2}
    END
    Log To Console    Variable before: ${var}
    Should be equal     ${var}    ${1}
    sleep    2
    Log To Console    Variable after: ${var}
    Should be equal    ${var}    ${2}



Thread wait for thread notification
    THREAD    TEST_THREAD    False
        SLEEP  2
        send thread notification        thread notify      Thread Done
    END
    ${var}=     wait_thread_notification       thread notify      5
    LOG      Get notification from thread. Payloads: ${var}
    Should contain    ${var}    Thread Done



Thread wait for thread notification timeout
    [Documentation]  FAIL Unable to received thread notification 'thread notify' in '2' seconds.
    THREAD    TEST_THREAD    False
        SLEEP  5
        send thread notification        thread notify      Thread Done
    END
    ${err_msg}=    Run Keyword And Expect Error    *    wait_thread_notification    thread notify    timeout=2    
    LOG TO CONSOLE      ${err_msg}
    Should Contain    ${err_msg}    'thread notify' within '2.0' seconds.



Threading Base
    #Skip    Temporary deactivated
    Set Test Variable    ${var_thrd_1}    ${10}
    Set Test Variable    ${var_thrd_2}    ${10}
    Set Test Variable    ${var_thrd_3}    ${10}
    THREAD    TEST_THREAD1    False
        threading_base.thread1
    END

    THREAD    TEST_THREAD2    False
        threading_base.thread2
    END

   THREAD    TEST_THREAD3   False
        threading_base.thread3
    END

    sleep    3

    Log    value var_thrd_1 ${var_thrd_1}   console=True
    Log    value var_thrd_2 ${var_thrd_2}   console=True
    Log    value var_thrd_3 ${var_thrd_3}   console=True

    Should Be Equal As Integers    ${var_thrd_1}    11
    Should Be Equal As Integers    ${var_thrd_2}    21
    Should Be Equal As Integers    ${var_thrd_3}    31



Threading Sync Base
    #Skip    Temporary deactivated
    @{var_thrd}=    Create List
    #elevate scope
    Set Test Variable    @{var_thrd}

    THREAD    TEST_THREAD1    False
        threading_sync_base.thread1
    END

    THREAD    TEST_THREAD2    False
        threading_sync_base.thread2
    END

   THREAD    TEST_THREAD3   False
        threading_sync_base.thread3
    END

    Wait Thread Notification    threading_sync_base.thread3_finished    timeout=10

    FOR    ${element}    IN    @{var_thrd}
        Log    ${element}    console=True 
    END

    @{ref}=    Create List    11    21    31
    Lists Should Be Equal    ${var_thrd}    ${ref}



Threading Sync Named Base
    #Skip    Temporary deactivated
    @{var_thrd}=    Create List
    #elevate scope
    Set Test Variable    @{var_thrd}

    THREAD    TEST_THREAD1    False
        threading_sync_named_base.thread1
    END

    Sleep    1

    THREAD    TEST_THREAD2    False
        threading_sync_named_base.thread2
    END

    Sleep    1

    THREAD    TEST_THREAD3   False
        threading_sync_named_base.thread3
    END

    Wait Thread Notification    threading_sync_named_base.thread3_finished    timeout=20

    FOR    ${element}    IN    @{var_thrd}
        Log    ${element}    console=True 
    END

    @{ref}=    Create List    11    21    31
    Lists Should Be Equal    ${var_thrd}    ${ref}



Threading Payload Base
    # Skip    Temporary deactivated due to https://github.com/test-fullautomation/robotframework/issues/47
    THREAD    TEST_THREAD1    False
        threading_payload_base.thread1
    END

    ${payload}=    Evaluate    {}
    ${payload}=    Wait Thread Notification    threading_payload_base.thread1_finished    timeout=10
    Log    wait thread successfully passed    console=True
    Log    payload is: '${payload}''    console=True

    #it's enough to test two values
    Should Be Equal    ${payload}[CLIENT][ID]    ${1}
    Should Be Equal    ${payload}[SERVER][ID]    ${2}



Threading Payload Two Notifications
    # Skip    Temporary deactivated due to https://github.com/test-fullautomation/robotframework/issues/47
    THREAD    TEST_THREAD1    False
        threading_payload_two_notifications.thread1
    END

    ${payload1}=    Evaluate    {}
    ${payload1}=    Wait Thread Notification    threading_payload_two_notifications.thread1_payload1_finished    timeout=10
    Log    wait thread successfully passed    console=True
    Log    payload1 is: '${payload1}''    console=True

    ${payload2}=    Evaluate    {}
    ${payload2}=    Wait Thread Notification    threading_payload_two_notifications.thread1_payload2_finished    timeout=10
    Log    wait thread successfully passed    console=True
    Log    payload2 is: '${payload2}''    console=True

    #it's enough to test two values
    Should Be Equal    ${payload1}[CLIENT][ID]    ${1}
    Should Be Equal    ${payload1}[SERVER][ID]    ${2}

    Should Be Equal    ${payload2}[CLIENT][ID]    ${11}
    Should Be Equal    ${payload2}[SERVER][ID]    ${22}



Threading Wait Thread Notification Base
    #Skip    Temporary deactivated
    ${payload1} =    Evaluate    {'CLIENT':{'NAME':'NAME','ID':1},'SERVER': {'ID':2,'IP':'172.17.0.4','PORT':'12345'},'SERVICE':{'ID':'ECHOSERVICE'}}
    ${payload2} =    Evaluate    {'CLIENT':{'NAME':'NAME','ID':11},'SERVER': {'ID':22,'IP':'172.17.0.4','PORT':'12345'},'SERVICE':{'ID':'ECHOSERVICE'}}
    #Sleep    0.25   
    Send Thread Notification    threading_wait_thread_notification_base    ${payload2}    MainThread
    Send Thread Notification    threading_wait_thread_notification_base    ${payload1}    MainThread

    ${payload1_recv}=    Wait Thread Notification    threading_wait_thread_notification_base    timeout=10
    Log    payload1 is: '${payload1_recv}''    console=True
    ${payload2_recv}=    Wait Thread Notification    threading_wait_thread_notification_base    timeout=10
    Log    payload2 is: '${payload2_recv}''    console=True

    #it's enough to test two values
    Should Be Equal    ${payload1_recv}[CLIENT][ID]    ${11}
    Should Be Equal    ${payload1_recv}[SERVER][ID]    ${22}

    Should Be Equal    ${payload2_recv}[CLIENT][ID]    ${1}
    Should Be Equal    ${payload2_recv}[SERVER][ID]    ${2}



Threading From Keyword Base
    # Skip    temporary deactivated due to https://github.com/test-fullautomation/robotframework/issues/49
    threading_from_keyword_base.dispatch
    #
    Sleep    5



Threading Keyword Multiple Time Called
    # Skip    temporary deactivated due to https://github.com/test-fullautomation/robotframework/issues/50
    @{var_thrd}=    Create List
    #elevate scope
    Set Test Variable    @{var_thrd}

    THREAD    TEST_THREAD1    False
        threading_keyword_multiple_time_called.keyword    msg="thrd1"    waittime=10
    END

    THREAD    TEST_THREAD2    False
        threading_keyword_multiple_time_called.keyword    msg="thrd2"    waittime=7
    END

    THREAD    TEST_THREAD3    False
        threading_keyword_multiple_time_called.keyword    msg="thrd3"    waittime=4
    END

    THREAD    TEST_THREAD4    False
        threading_keyword_multiple_time_called.keyword    msg="thrd4"    waittime=1
    END

    Sleep    15

    FOR    ${element}    IN    @{var_thrd}
        Log    ${element}    console=True 
    END

    List Should Contain Value    ${var_thrd}    "thrd1"
    List Should Contain Value    ${var_thrd}    "thrd2"
    List Should Contain Value    ${var_thrd}    "thrd3"
    List Should Contain Value    ${var_thrd}    "thrd4"    

Threading RLock Basic
    # Testing acquiring and releasing lock in two threads
    @{var_thrd}=    Create List
    Set Test Variable    @{var_thrd}

    THREAD    TEST_THREAD1    False
        threading_rlock_base.thread1
    END

    THREAD    TEST_THREAD2    False
        threading_rlock_base.thread2
    END

    Wait Thread Notification    threading_rlock_base.thread2_finished    timeout=10

    FOR    ${element}    IN    @{var_thrd}
        Log    ${element}    console=True
    END

    @{ref}=    Create List    11    21
    Lists Should Be Equal    ${var_thrd}    ${ref}

Threading RLock Exceeded timeout 
    # Testing acquiring and releasing lock in two threads
    @{var_thrd}=    Create List
    Set Test Variable    @{var_thrd}

    THREAD    TEST_THREAD1    False
        threading_rlock_base.thread1
    END

    THREAD    TEST_THREAD2    False
        threading_rlock_base.thread2_with_time_out
    END

    Run Keyword And Expect Error   *    Wait Thread Notification    threading_rlock_base.thread2_finished    timeout=10

    FOR    ${element}    IN    @{var_thrd}
        Log    ${element}    console=True
    END

    @{ref}=    Create List    11
    Lists Should Be Equal    ${var_thrd}    ${ref}

Threading RLock Non blocking 
    # Testing acquiring and releasing lock in two threads
    @{var_thrd}=    Create List
    Set Test Variable    @{var_thrd}

    THREAD    TEST_THREAD1    False
        threading_rlock_base.thread1
    END

    THREAD    TEST_THREAD2    False
        threading_rlock_base.thread2_non_block
    END

    Run Keyword And Expect Error   *    Wait Thread Notification    threading_rlock_base.thread2_finished    timeout=10

    FOR    ${element}    IN    @{var_thrd}
        Log    ${element}    console=True
    END

    @{ref}=    Create List    11
    Lists Should Be Equal    ${var_thrd}    ${ref}

Threading RLock Reacquire Append
    # Testing threads alternating appending with lock reacquisition
    @{var_thrd}=    Create List
    Set Test Variable    @{var_thrd}

    THREAD    TEST_THREAD1    False
        threading_rlock_base.thread1_reacquire
    END

    THREAD    TEST_THREAD2    False
        threading_rlock_base.thread2_reacquire
    END

    Wait Thread Notification    threading_rlock_base.thread1_finished    timeout=10
    Wait Thread Notification    threading_rlock_base.thread2_finished    timeout=10

    FOR    ${element}    IN    @{var_thrd}
        Log    ${element}    console=True
    END

    @{ref}=    Create List    Thread_1_First    Thread_2_First    Thread_1_Second
    Lists Should Be Equal    ${var_thrd}    ${ref}