*** Settings ***
Documentation     Flow files executed as runtime-built suites via ``--parser robot.flow``.
Resource          atest_resource.robot

*** Variables ***
${PARSER}         --parser robot.flow
${CHECKPOINT}     %{TEMPDIR}${/}flow_atest.checkpoint.json
${FLOW REPORT}    ${OUTDIR}${/}flow.html

*** Test Cases ***
Full example runs setup, cycles, recovery and teardown
    Run Tests    ${PARSER}    flow/endurance_cycle.flow.json
    Check Test Case    Cycle    PASS
    Should Be Equal    ${SUITE.setup.name}    Flow Setup
    Should Be Equal    ${SUITE.teardown.name}    Flow Teardown
    Check Log Message    ${SUITE.setup.body[1].messages[0]}    Power on for RIG_A.
    # Flow Phase, Flow Loop, then the loop; Flow Iteration leads every iteration.
    ${loop} =    Set Variable    ${SUITE.tests[0].body[2]}
    Should Be Equal    ${loop.type}    WHILE
    # Three iterations, then the INFO message about reaching the limit.
    ${iterations} =    Evaluate    [item for item in $loop.body if item.type == 'ITERATION']
    Length Should Be    ${iterations}    3
    # Iteration 1 passes: the EXCEPT branch is not run.
    Should Be Equal    ${loop.body[0].body[1].body[1].status}    NOT RUN
    # Iteration 2 fails on purpose and runs the recovery, then the loop continues.
    ${recovery} =    Set Variable    ${loop.body[1].body[1].body[1]}
    Should Be Equal    ${recovery.type}    EXCEPT
    Check Log Message    ${recovery.body[0].messages[0]}    Recovery 1 for RIG_A.
    Check Log Message    ${loop.body[2].body[1].body[0].body[0].messages[0]}    Cycle 3 for RIG_A.
    Check Log Message    ${SUITE.teardown.body[0].messages[0]}    Bench released.

Signals between flows: set, wait at a gate, read back
    Run Tests    ${PARSER}    flow/signals.flow.json
    Check Test Case    Set And Wait    PASS
    Check Test Case    Unset Signal Keeps The Gate Waiting    UNKNOWN
    ...    GLOB:Gate 'Signal Should Be' did not pass within 300 milliseconds (* attempts, waited * milliseconds). Last error: Signal 'atest.never' has not been set.

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
    Check Log Message    ${SUITE.teardown.body[0].messages[0]}    Bench released.

Abort runs the recovery and keeps the original error
    Run Tests    ${PARSER}    flow/recovery_abort.flow.json
    ${tc} =    Check Test Case    Abort After Recovery    FAIL    Flash failed.
    Check Log Message    ${tc.body[1].body[1].body[0].messages[0]}    Recovery 1 for ECU.
    Should Be Equal    ${tc.body[1].body[1].body[1].full_name}    BuiltIn.Fail
    Should Be Equal    ${tc.body[2].status}    NOT RUN

Decision takes the branch the condition selects
    Run Tests    ${PARSER} --variable MODE:fast    flow/decision.flow.json
    ${tc} =    Check Test Case    Decision    PASS
    Check Log Message    ${tc.body[1].body[0].body[0].messages[0]}    Took the fast branch.
    Should Be Equal    ${tc.body[1].body[1].status}    NOT RUN
    Run Tests    ${PARSER}    flow/decision.flow.json
    ${tc} =    Check Test Case    Decision    PASS
    Should Be Equal    ${tc.body[1].body[0].status}    NOT RUN
    Check Log Message    ${tc.body[1].body[1].body[0].messages[0]}    Took the slow branch.
    Check Log Message    ${tc.body[2].messages[0]}    joined

Loop bounded only by a deadline ends with PASS
    Run Tests    ${PARSER}    flow/deadline_loop.flow.json
    ${tc} =    Check Test Case    Deadline Loop    PASS
    Should Be Equal    ${tc.body[1].full_name}    robot.flow.keywords.Flow Loop
    Should Be Equal    ${tc.body[2].type}    WHILE
    Should Be True    3 <= len($tc.body[2].body) <= 8

Dry run validates keywords and gate arguments
    Run Tests    ${PARSER} --dryrun    flow/endurance_cycle.flow.json
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

Sub-flows run as one keyword each, with parameters, nesting and recovery
    Run Tests    ${PARSER}    flow/subflow.flow.json
    ${tc} =    Check Test Case    Defaults And Arguments    PASS
    Should Be Equal    ${tc.body[1].name}    Flow: Power On
    Check Log Message    ${tc.body[1].body[0].messages[0]}    Power on at 12 V.
    Should Be Equal    ${tc.body[2].args}    ${{('VOLTS=\${V}',)}}
    Check Log Message    ${tc.body[2].body[0].messages[0]}    Power on at 9 V.
    ${tc} =    Check Test Case    Nested Subflow    PASS
    Should Be Equal    ${tc.body[1].body[0].name}    Flow: Power On
    Check Log Message    ${tc.body[1].body[0].body[0].messages[0]}    Power on at 5 V.
    ${tc} =    Check Test Case    Failure Inside Subflow Is Recovered    PASS
    ${try} =    Set Variable    ${tc.body[1]}
    Should Be Equal    ${try.body[0].body[0].status}    FAIL
    Check Log Message    ${try.body[1].body[0].messages[0]}    Recovered: Step is broken.: yes != no

Pause holds the flow and its clocks
    Run Tests    ${PARSER} --variable HOLD:3 --test Hold    flow/pause_stop.flow.json
    ${tc} =    Check Test Case    Hold    PASS
    ${loop} =    Set Variable    ${tc.body[2]}
    Should Be Equal    ${loop.type}    WHILE
    # The two second loop was held for three seconds in its first iteration.
    # Had the hold counted, the loop would have ended there.
    Should Be True    len($loop.body) >= 3
    ${output} =    Get File    ${OUTFILE}
    Should Contain    ${output}    Flow paused by operator at iteration 1 of loop 'timed'.
    Should Match Regexp    ${output}    Flow resumed after [23] seconds

Stop leaves a checkpoint and the next run continues from it
    Remove File    ${CHECKPOINT}
    Run Tests    ${PARSER} --variable STOP_AT:3 --variable FLOW_CHECKPOINT:${CHECKPOINT}    flow/pause_stop.flow.json
    Check Test Case    Precheck    PASS
    Check Test Case    Hold    PASS
    # The third iteration ran to its end, so it counts; the recovery region
    # of the loop did not swallow the stop.
    ${tc} =    Check Test Case    Cycle    UNKNOWN
    ...    GLOB:Stopped by operator at iteration 4 of loop 'cycles'; resumable from *flow_atest.checkpoint.json.
    Length Should Be    ${tc.body[3].body}    4
    Check Test Case    Report    UNKNOWN    Test execution stopped due to a fatal error.
    Check Log Message    ${SUITE.teardown.body[0].messages[0]}    Bench released.
    Should Be Equal    ${SUITE.status}    UNKNOWN
    File Should Exist    ${CHECKPOINT}
    Run Tests    ${PARSER} --variable FLOW_CHECKPOINT:${CHECKPOINT}    flow/pause_stop.flow.json
    Check Test Case    Precheck    SKIP    GLOB:Completed with status PASS in the run that ended ????-??-?? ??:??:?? (see *).
    Check Test Case    Hold    SKIP    GLOB:Completed with status PASS in the run that ended *
    ${tc} =    Check Test Case    Cycle    PASS
    Check Log Message    ${tc.body[2].messages[1]}    Loop 'cycles' continues after 3 completed iteration(s) of the run saved *: 3 iteration(s) left.    pattern=True
    Check Log Message    ${tc.body[2].messages[2]}    Restored variables: \${done}.
    ${loop} =    Set Variable    ${tc.body[3]}
    # Three iterations were left; the message about the limit follows them.
    Should Be True    len([item for item in $loop.body if item.type == 'ITERATION']) == 3
    Check Log Message    ${loop.body[0].body[1].body[0].body[0].messages[0]}    Work 4.
    Check Log Message    ${loop.body[2].body[1].body[0].body[0].messages[0]}    Work 6.
    Check Test Case    Report    PASS
    Should Be Equal    ${SUITE.status}    PASS
    File Should Not Exist    ${CHECKPOINT}

Resume can be demanded
    Remove File    ${CHECKPOINT}
    Run Tests    ${PARSER} --variable FLOW_RESUME:always --variable FLOW_CHECKPOINT:${CHECKPOINT} --test Precheck    flow/pause_stop.flow.json
    Check Test Case    Precheck    UNKNOWN
    ...    GLOB:No checkpoint at '*flow_atest.checkpoint.json'; cannot resume (FLOW_RESUME is 'always').

Flow without a checkpoint has no bookkeeping keywords
    Run Tests    ${PARSER}    flow/no_checkpoint.flow.json
    ${tc} =    Check Test Case    Plain    PASS
    Should Be Equal    ${tc.body[0].type}    WHILE
    # Only the node itself in an iteration: no Flow Iteration before it.
    Length Should Be    ${tc.body[0].body[0].body}    1

Flow report is written with the run and by rebot
    [Documentation]    --flowreport draws the plan with the run's results and links it
    ...    into the log; rebot writes the same report from output.xml. A stopped run
    ...    shows where it stopped and what a restart would do (from its checkpoint).
    Remove File    ${CHECKPOINT}
    Remove File    ${FLOW REPORT}
    Run Tests    ${PARSER} --flowreport ${FLOW REPORT} --variable STOP_AT:3 --variable FLOW_CHECKPOINT:${CHECKPOINT}    flow/pause_stop.flow.json
    Check Test Case    Cycle    UNKNOWN
    ...    GLOB:Stopped by operator at iteration 4 of loop 'cycles'; resumable from *flow_atest.checkpoint.json.
    Stdout Should Contain    Flow report: ${FLOW REPORT}
    ${html} =    Get File    ${FLOW REPORT}
    Should Contain    ${html}    <title>Flow report: Pause Stop</title>
    Should Contain    ${html}    Stopped by operator at iteration 4 of loop 'cycles'
    # The loop node has its iterations and the edges of the plan counted.
    Should Match Regexp    ${html}    "cycles": \{"runs": 1, "status": \{"UNKNOWN": 1\}.*"edges": \{"body": 4, "next": 3
    # The run left a checkpoint: the report says what is left for the restart.
    Should Contain    ${html}    "checkpoint": {"path":
    Should Contain    ${html}    "matches": true
    # rebot writes the same report from the output; the log link is relative.
    Remove File    ${FLOW REPORT}
    Copy Previous Outfile
    Run Rebot    --flowreport ${FLOW REPORT} --log ${OUTDIR}${/}log.html    ${OUTFILE COPY}
    Stdout Should Contain    Flow report: ${FLOW REPORT}
    ${html} =    Get File    ${FLOW REPORT}
    Should Contain    ${html}    <title>Flow report: Pause Stop</title>
    Should Contain    ${html}    var LOG = "log.html"
    [Teardown]    Run Keywords    Remove File    ${CHECKPOINT}    AND    Remove File    ${FLOW REPORT}

Flow report needs a suite run from a flow file
    [Documentation]    The report is built next to the suite rebuilt from the flow file;
    ...    an output whose suite did not come from one is refused with an error.
    Run Tests    ${EMPTY}    misc/pass_and_fail.robot
    Copy Previous Outfile
    Run Rebot Without Processing Output    --flowreport ${FLOW REPORT}    ${OUTFILE COPY}
    Stderr Should Contain    [ ERROR ] No flow suite in
    File Should Not Exist    ${FLOW REPORT}

Flow report is not created if output is disabled
    [Documentation]    Like the log and the timeline, the flow report is generated from
    ...    output.xml, so --output NONE disables it with an error.
    Run Tests Without Processing Output    ${PARSER} --output NONE --flowreport ${FLOW REPORT}    flow/no_checkpoint.flow.json
    Stderr Should Contain    [ ERROR ] FlowReport file cannot be created if output.xml is disabled.
    File Should Not Exist    ${FLOW REPORT}

*** Keywords ***
Parsing Should Fail
    [Arguments]    ${file}    ${error}
    Run Tests Without Processing Output    ${PARSER}    flow/${file}.flow.json
    Stderr Should Contain    ${error}
