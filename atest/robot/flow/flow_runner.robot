*** Settings ***
Documentation     Flow files executed as runtime-built suites via ``--parser robot.flow``.
Resource          atest_resource.robot

*** Variables ***
${PARSER}         --parser robot.flow

*** Test Cases ***
Full example runs setup, cycles, recovery and teardown
    Run Tests    ${PARSER}    flow/permanent_run.flow.json
    Check Test Case    Cycle    PASS
    Should Be Equal    ${SUITE.setup.name}    Flow Setup
    Should Be Equal    ${SUITE.teardown.name}    Flow Teardown
    Check Log Message    ${SUITE.setup.body[1].msgs[0]}    Power on for IVI.
    ${loop} =    Set Variable    ${SUITE.tests[0].body[1]}
    Should Be Equal    ${loop.type}    WHILE
    # Three iterations, then the INFO message about reaching the limit.
    ${iterations} =    Evaluate    [item for item in $loop.body if item.type == 'ITERATION']
    Length Should Be    ${iterations}    3
    # Iteration 1 passes: the EXCEPT branch is not run.
    Should Be Equal    ${loop.body[0].body[0].body[1].status}    NOT RUN
    # Iteration 2 fails on purpose and runs the recovery, then the loop continues.
    ${recovery} =    Set Variable    ${loop.body[1].body[0].body[1]}
    Should Be Equal    ${recovery.type}    EXCEPT
    Check Log Message    ${recovery.body[0].msgs[0]}    Recovery 1 for IVI.
    Check Log Message    ${loop.body[2].body[0].body[0].body[0].msgs[0]}    Cycle 3 for IVI.
    Check Log Message    ${SUITE.teardown.body[0].msgs[0]}    Bench released.

Parser can be given as the class as well
    Run Tests    --parser robot.flow.FlowParser    flow/decision.flow.json
    Check Test Case    Decision    PASS

Gate timeout is UNKNOWN by default and FAIL on request
    Run Tests    ${PARSER}    flow/gate_timeout.flow.json
    Check Test Case    Unknown On Timeout    UNKNOWN
    ...    GLOB:Gate 'Signal Should Be' did not pass within 300 milliseconds (* attempts, waited * milliseconds). Last error: bench.never is 0, expected != 0.
    Check Test Case    Fail On Timeout    FAIL
    ...    GLOB:Gate 'Signal Should Be' did not pass within 300 milliseconds (* attempts, waited * milliseconds). Last error: bench.never is 0, expected != 0.
    Check Test Case    Passes After Retries    PASS
    Should Be Equal    ${SUITE.status}    UNKNOWN

Gate timeout in setup makes the suite unknown and still runs teardown
    Run Tests    ${PARSER}    flow/setup_gate_timeout.flow.json
    Check Test Case    Never Runs    UNKNOWN
    ...    GLOB:Parent suite setup unknown:\nGate 'Signal Should Be' did not pass within 200 milliseconds (* attempts, waited * milliseconds). Last error: bench.never is 0, expected != 0.
    Should Be Equal    ${SUITE.status}    UNKNOWN
    Check Log Message    ${SUITE.teardown.body[0].msgs[0]}    Bench released.

Abort runs the recovery and keeps the original error
    Run Tests    ${PARSER}    flow/recovery_abort.flow.json
    ${tc} =    Check Test Case    Abort After Recovery    FAIL    Flash failed.
    Check Log Message    ${tc.body[0].body[1].body[0].msgs[0]}    Recovery 1 for ECU.
    Should Be Equal    ${tc.body[0].body[1].body[1].name}    BuiltIn.Fail
    Should Be Equal    ${tc.body[1].status}    NOT RUN

Decision takes the branch the condition selects
    Run Tests    ${PARSER} --variable MODE:fast    flow/decision.flow.json
    ${tc} =    Check Test Case    Decision    PASS
    Check Log Message    ${tc.body[0].body[0].body[0].msgs[0]}    Took the fast branch.
    Should Be Equal    ${tc.body[0].body[1].status}    NOT RUN
    Run Tests    ${PARSER}    flow/decision.flow.json
    ${tc} =    Check Test Case    Decision    PASS
    Should Be Equal    ${tc.body[0].body[0].status}    NOT RUN
    Check Log Message    ${tc.body[0].body[1].body[0].msgs[0]}    Took the slow branch.
    Check Log Message    ${tc.body[1].msgs[0]}    joined

Loop bounded only by a deadline ends with PASS
    Run Tests    ${PARSER}    flow/deadline_loop.flow.json
    ${tc} =    Check Test Case    Deadline Loop    PASS
    Should Be Equal    ${tc.body[0].name}    BuiltIn.Evaluate
    Should Be Equal    ${tc.body[1].type}    WHILE
    Should Be True    3 <= len($tc.body[1].body) <= 8

Dry run validates keywords and gate arguments
    Run Tests    ${PARSER} --dryrun    flow/permanent_run.flow.json
    Check Test Case    Cycle    PASS
    Run Tests    ${PARSER} --dryrun    flow/invalid_keyword.flow.json
    Check Test Case    Invalid Keyword    UNKNOWN
    ...    Several failures occurred:\n\n1) No keyword with name 'No Such Keyword' found.\n\n2) Keyword 'FlowStubs.Signal Should Be' expected 3 arguments, got 1.

Invalid flow files are rejected naming the node
    [Template]    Parsing Should Fail
    invalid_no_join            Node 'which': The 'yes' and 'no' branches never re-join.
    invalid_body_no_next       Node 'loop': The body of loop 'loop' must return to it with an edge labelled 'next'; it ends at 'loop' via 'then'.
    invalid_unreachable        Unreachable node(s): orphan.
    invalid_unbounded_loop     Node 'loop': A loop needs 'max_loops' and/or 'max_seconds'; unbounded loops are not allowed.

*** Keywords ***
Parsing Should Fail
    [Arguments]    ${file}    ${error}
    Run Tests Without Processing Output    ${PARSER}    flow/${file}.flow.json
    Stderr Should Contain    ${error}
