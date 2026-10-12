*** Settings ***
Resource    helpers.resource

*** Variables ***
${ITEMS}    one
@{LIST}      a    b    c

*** Test Cases ***
Greets
    Greet    bench
    Log    after greet

Fails
    Log    before the failure
    Should Be Equal    1    2
    Log    never
