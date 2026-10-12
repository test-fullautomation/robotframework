*** Settings ***
Documentation     Example 1 - the smallest possible state machine.
...
...               Two states and one transition: HELLO runs its enter
...               keyword, moves on unconditionally, and WORLD is final,
...               which completes the machine. Look at log.html to see
...               each state visit as its own collapsible block.
Library           StateMachine
Suite Teardown    Reset State Machine

*** Test Cases ***
My First State Machine
    Define State         HELLO    enter=Say Hello
    Define State         WORLD    enter=Say World    final=True
    # No condition = always taken (the default branch).
    Define Transition    HELLO    WORLD
    Run State Machine    initial=HELLO    poll_interval=0.1 s
    ${state}=    Get Current State
    Should Be Equal    ${state}    WORLD

*** Keywords ***
Say Hello
    Log    Hello from the HELLO state!

Say World
    Log    ... and goodbye from the final WORLD state.
