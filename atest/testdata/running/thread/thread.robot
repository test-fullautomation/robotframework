*** Variables ***
${variable}    ${1}

*** Test Cases ***
Thread executed
    THREAD    TEST_THREAD    False
        SLEEP  2
        LOG   inside threading
    END
    LOG    CONTINUE IN MAIN THREAD
#    SLEEP    5

Thread change variable
    ${var}=   set variable       ${1}
    THREAD    TEST_THREAD    False
        SLEEP  1
        ${var}=   set variable       ${2}
    END
    log    Variable before: ${var}
    sleep    2
    log    Variable after: ${var}

Thread wait for thread notification
    THREAD    TEST_THREAD    False
        SLEEP  2
        send thread notification        thread notify      Thread Done
    END
    ${var}=     wait_thread_notification       thread notify      5
    LOG      Get notification from thread. Payloads: ${var}

Thread wait for thread notification timeout
    [Documentation]  FAIL Unable to received thread notification 'thread notify' in '2' seconds.
    THREAD    TEST_THREAD    False
        SLEEP  5
        send thread notification        thread notify      Thread Done
    END
    ${var}=     wait_thread_notification       thread notify      2
    LOG TO CONSOLE      ${var}
