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
    # Flow Phase, Flow Loop, then the loop; an iteration holds the node's TRY only.
    ${loop} =    Set Variable    ${SUITE.tests[0].body[2]}
    Should Be Equal    ${loop.type}    WHILE
    # Three iterations, then the INFO message about reaching the limit.
    ${iterations} =    Evaluate    [item for item in $loop.body if item.type == 'ITERATION']
    Length Should Be    ${iterations}    3
    # Iteration 1 passes: the EXCEPT branch is not run.
    Should Be Equal    ${loop.body[0].body[0].body[1].status}    NOT RUN
    # Iteration 2 fails on purpose and runs the recovery, then the loop continues.
    ${recovery} =    Set Variable    ${loop.body[1].body[0].body[1]}
    Should Be Equal    ${recovery.type}    EXCEPT
    Check Log Message    ${recovery.body[0].messages[0]}    Recovery 1 for RIG_A.
    Check Log Message    ${loop.body[2].body[0].body[0].body[0].messages[0]}    Cycle 3 for RIG_A.
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

Loop bounds can be variables sized per run
    [Documentation]    The file names the bound, the run sizes it: the default comes from
    ...    the file's 'variables', --variable overrides it, and a value that is not a
    ...    bound ends the loop's phase UNKNOWN (nothing was tested) when the loop starts.
    Run Tests    ${PARSER}    flow/variable_bounds.flow.json
    ${tc} =    Check Test Case    Variable Bounds    PASS
    Should Be True    len([i for i in $tc.body[2].body if i.type == 'ITERATION']) == 2
    Run Tests    ${PARSER} --variable CYCLES:3 --variable MAX_TIME:1h --variable FLOW_CHECKPOINT_EVERY:2    flow/variable_bounds.flow.json
    ${tc} =    Check Test Case    Variable Bounds    PASS
    Should Be True    len([i for i in $tc.body[2].body if i.type == 'ITERATION']) == 3
    # No marker keyword in the iterations: the runner counts them, and an
    # iteration that writes the checkpoint (every second one here) says so.
    ${loop} =    Set Variable    ${tc.body[2]}
    Should Be True    [i.type for i in $loop.body[0].body] == ['MESSAGE', 'KEYWORD', 'KEYWORD']
    Should Be True    [m.message for m in $loop.body[0].body if m.type == 'MESSAGE'] == ["Iteration 1 of loop 'loop'; checkpoint written."]
    Should Be True    [i.type for i in $loop.body[1].body] == ['KEYWORD', 'KEYWORD']
    Should Be True    [m.message for m in $loop.body[2].body if m.type == 'MESSAGE'] == ["Iteration 3 of loop 'loop'; checkpoint written."]
    Run Tests    ${PARSER} --variable CYCLES:many    flow/variable_bounds.flow.json
    Check Test Case    Variable Bounds    UNKNOWN    'max_loops' of loop 'loop' must be a positive integer, got 'many'.

Keyword nodes retry under a policy and time out per attempt
    [Documentation]    'retry' runs a keyword node again when it fails (attempts, interval,
    ...    backoff, the verdict when the last attempt fails too); 'timeout' is a keyword
    ...    timeout on each attempt. The attempts are the Flow Retry call's children in the
    ...    log; a timeout of the step itself is a plain failure the recovery can route.
    Run Tests    ${PARSER}    flow/retry_timeout.flow.json
    ${tc} =    Check Test Case    Passes After Retries    PASS
    ${call} =    Set Variable    ${tc.body[0]}
    Should Be Equal    ${call.name}    Flow Retry
    Should Be True    [k.status for k in $call.body if k.type == 'KEYWORD'] == ['FAIL', 'FAIL', 'PASS']
    Check Log Message    ${call.messages[0]}    Attempt 1 of 3 of 'Flaky Reading' failed, next attempt in 50 milliseconds: Reading 1 is noise.
    Check Log Message    ${call.messages[1]}    Attempt 2 of 3 of 'Flaky Reading' failed, next attempt in 100 milliseconds: Reading 2 is noise.
    Check Log Message    ${call.messages[2]}    'Flaky Reading' passed on attempt 3 of 3.
    Check Log Message    ${tc.body[1].messages[0]}    Reading 3
    Check Test Case    Exhausted Is Fail    FAIL    'Break Something' failed after 2 attempts. Last error: Still broken.
    Check Test Case    Exhausted Is Unknown    UNKNOWN    'Break Something' failed after 2 attempts. Last error: No reply.
    ${tc} =    Check Test Case    Timed Out Attempt Is Retried    PASS
    Should Be True    [k.status for k in $tc.body[0].body if k.type == 'KEYWORD'] == ['FAIL', 'PASS']
    Check Log Message    ${tc.body[0].messages[0]}    Attempt 1 of 2 of 'Slow Then Fast' timed out, next attempt in 10 milliseconds: Keyword timeout 300 milliseconds exceeded.
    Check Test Case    Timeout Alone Fails The Step    FAIL    'Take Time' timed out after 300 milliseconds. Last error: Keyword timeout 300 milliseconds exceeded.
    ${tc} =    Check Test Case    Timeout Is Routed To The Recovery    PASS
    Check Log Message    ${tc.body[0].body[1].body[0].messages[0]}    Recovery 1 for RIG_T.
    Should Be Equal    ${SUITE.status}    UNKNOWN

Parallel regions run their branches as threads and join before the plan goes on
    [Documentation]    A 'parallel' node starts one THREAD per branch (named after the plan,
    ...    numbered from the region's second run on) and joins them: the join fails naming
    ...    each failed branch, with the worst status; a failed branch stops the others at
    ...    their next step unless 'stop_others' is false; a region times out naming the
    ...    branches still running; 'on_failure' recovers the join's failure; a flow stop
    ...    stops the branches first. The join reports the branches: no thread warnings.
    Run Tests    ${PARSER}    flow/parallel.flow.json
    ${tc} =    Check Test Case    Two Branches Join    PASS
    Should Be Equal    ${tc.body[0].name}    Flow Parallel Start
    Should Be Equal    ${tc.body[1].type}    THREAD
    Should Be Equal    ${tc.body[1].name}    flow.p1.a1
    Should Be Equal    ${tc.body[2].name}    flow.p1.b1
    Should Be Equal    ${tc.body[1].body[0].name}    Flow Branch
    Should Be Equal    ${tc.body[1].body[0].body[0].name}    Flow Branch: p1/a1
    Check Log Message    ${tc.body[1].body[0].body[0].body[2].messages[0]}    A ends
    Check Log Message    ${tc.body[2].body[0].body[0].body[1].messages[0]}    B ends
    Should Be Equal    ${tc.body[3].name}    Flow Join
    Check Log Message    ${tc.body[3].messages[0]}    Region 'p1': 2 branches joined after * milliseconds.    pattern=True
    Check Log Message    ${tc.body[4].messages[0]}    joined
    ${tc} =    Check Test Case    Failed Branch Stops The Other    FAIL    Branch 'c1' of 'p2' failed: PSU tripped.
    Should Be Equal    ${tc.body[1].body[0].status}    FAIL
    Should Be Equal    ${tc.body[1].body[0].message}    PSU tripped.
    # The other branch was stopped after its first step: the two after it did not run.
    Should Be Equal    ${tc.body[2].body[0].status}    UNKNOWN
    Should Be Equal    ${tc.body[2].body[0].message}    Branch 'd1' of 'p2' was stopped.
    Length Should Be    ${tc.body[2].body[0].body[0].body}    1
    Check Log Message    ${tc.body[3].messages[0]}    Branch 'c1' of 'p2' failed; stopping the other branch(es): d1.
    ${tc} =    Check Test Case    Failed Branch Lets The Other Finish    FAIL    Branch 'e1' of 'p3' failed: E broke.
    Check Log Message    ${tc.body[2].body[0].body[0].body[1].messages[0]}    F finished
    Check Test Case    Gate Timeout In A Branch Is Unknown    UNKNOWN
    ...    GLOB:Branch 'g1' of 'p4' is unknown: Gate 'Signal Should Be' did not pass within 200 milliseconds (* attempts, waited * milliseconds). Last error: bench.never is 0, expected != 0.
    Check Test Case    Unknown Wins Over Fail    UNKNOWN
    ...    GLOB:Branch 'g2' of 'p4b' failed: G2 failed.\nBranch 'h3' of 'p4b' is unknown: Gate 'Signal Should Be' did not pass within 100 milliseconds (*
    ${tc} =    Check Test Case    Region Timeout    FAIL    Region 'p5' did not join within 300 milliseconds; still running: i1.
    Should Be Equal    ${tc.body[1].body[0].message}    Branch 'i1' of 'p5' was stopped.
    ${tc} =    Check Test Case    Recovery After A Failed Branch    PASS
    Should Be Equal    ${tc.body[0].body[0].status}    FAIL
    Check Log Message    ${tc.body[0].body[1].body[0].messages[0]}    Recovery 1 for RIG_P.
    Check Log Message    ${tc.body[1].messages[0]}    after recovery
    ${tc} =    Check Test Case    Region In A Loop    PASS
    ${second} =    Set Variable    ${tc.body[0].body[1]}
    Should Be Equal    ${second.body[1].name}    flow.p7.m1.2
    Should Be Equal    ${second.body[2].name}    flow.p7.n1.2
    Check Log Message    ${second.body[1].body[0].body[0].body[0].messages[0]}    Iteration 2.
    ${tc} =    Check Test Case    Stop During A Region    UNKNOWN    Stopped by operator.
    Should Be Equal    ${tc.body[2].body[0].message}    Branch 'q1' of 'p8' was stopped.
    Should Be Equal    ${tc.body[4].status}    NOT RUN
    Should Be Equal    ${SUITE.status}    UNKNOWN
    Length Should Be    ${ERRORS}    0

Finished loop iterations leave memory, the output keeps them
    [Documentation]    A flow's loops run for hours: once an iteration is in output.xml its
    ...    results are dropped from the model (only the last one stays). Suites keep
    ...    everything unless --pruneresults says otherwise.
    Run Tests    ${PARSER} --listener ${DATADIR}${/}flow${/}BodySizes.py --variable CYCLES:40    flow/variable_bounds.flow.json
    Stdout Should Contain    BODY SIZE: WHILE in 'Variable Bounds' keeps 1 iteration(s)
    ${tc} =    Check Test Case    Variable Bounds    PASS
    Should Be True    len([i for i in $tc.body[2].body if i.type == 'ITERATION']) == 40
    Run Tests    --listener ${DATADIR}${/}flow${/}BodySizes.py    flow/plain_loops.robot
    Stdout Should Contain    BODY SIZE: WHILE in 'Plain Loops' keeps 31 iteration(s)
    Stdout Should Contain    BODY SIZE: FOR in 'Plain Loops' keeps 30 iteration(s)
    Run Tests    --pruneresults --listener ${DATADIR}${/}flow${/}BodySizes.py    flow/plain_loops.robot
    Stdout Should Contain    BODY SIZE: WHILE in 'Plain Loops' keeps 1 iteration(s)
    Stdout Should Contain    BODY SIZE: FOR in 'Plain Loops' keeps 1 iteration(s)
    ${tc} =    Check Test Case    Plain Loops    PASS
    Should Be True    len([i for i in $tc.body[0].body if i.type == 'ITERATION' and i.status == 'PASS']) == 30
    Should Be True    len([i for i in $tc.body[1].body if i.type == 'ITERATION']) == 30

Dry run validates keywords and gate arguments
    Run Tests    ${PARSER} --dryrun    flow/endurance_cycle.flow.json
    Check Test Case    Cycle    PASS
    Run Tests    ${PARSER} --dryrun    flow/retry_timeout.flow.json
    Check Test Case    Passes After Retries    PASS
    Should Be Equal    ${SUITE.status}    PASS
    Run Tests    ${PARSER} --dryrun    flow/parallel.flow.json
    Check Test Case    Two Branches Join    PASS
    Should Be Equal    ${SUITE.status}    PASS
    Run Tests    ${PARSER} --dryrun    flow/invalid_keyword.flow.json
    Check Test Case    Invalid Keyword    UNKNOWN
    ...    Several failures occurred:\n\n1) No keyword with name 'No Such Keyword' found.\n\n2) Keyword 'FlowStubs.Signal Should Be' expected 3 arguments, got 1.

Invalid flow files are rejected naming the node
    [Template]    Parsing Should Fail
    invalid_no_join            Node 'which': The 'yes' and 'no' branches never re-join.
    invalid_body_no_next       Node 'loop': The body of loop 'loop' must return to it with an edge labelled 'next'; it ends at 'loop' via 'then'.
    invalid_unreachable        Unreachable node(s): orphan.
    invalid_unbounded_loop     Node 'loop': A loop needs 'max_loops' and/or 'max_seconds'; unbounded loops are not allowed.
    invalid_branch_no_join     Node 'region': The branch of 'region' starting at 'b' must return to it with an edge labelled 'join'; it ends at 'end' via 'then'.
    invalid_nested_parallel    Node 'inner': A parallel region cannot be inside a branch of another parallel region ('outer'); split the plan or run the inner branches one after the other.

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
    Check Log Message    ${loop.body[0].body[0].body[0].body[0].messages[0]}    Work 4.
    Check Log Message    ${loop.body[2].body[0].body[0].body[0].messages[0]}    Work 6.
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
