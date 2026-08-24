*** Settings ***
Resource        cli_resource.robot

*** Test Cases ***
Default colors
    [Template]  Report should have correct background
    ${EMPTY}

Two custom colors
    [Template]  Report should have correct background
    --reportbackground blue:red  blue  red

Three custom colors
    [Template]  Report should have correct background
    --reportback green:red:yellow  green  red  yellow

Invalid Colors
    Run Should Fail    --reportback invalid ${SUITE_SOURCE}
    ...    Invalid value for option '--reportbackground': Expected format 'pass:fail:unknown:skip' or 'pass:fail:unknown' or 'pass:fail', got 'invalid'.

*** Keywords ***
Report should have correct background
    # Argument order matches the 'pass:fail:unknown:skip' option format.
    [Arguments]  ${opt}  ${pass}=#9e9  ${fail}=#f66  ${unknown}=#66c7ff  ${skip}=#fed84f
    Run Tests  ${opt} --report rep.html  misc/pass_and_fail.robot
    ${report} =  Get File  ${OUTDIR}/rep.html
    # Keys are written in alphabetical order by the JSON writer.
    Should Contain  ${report}  "background":{"fail":"${fail}","pass":"${pass}","skip":"${skip}","unknown":"${unknown}"},
