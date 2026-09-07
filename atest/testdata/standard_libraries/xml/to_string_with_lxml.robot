*** Settings ***
Suite Setup       Set lxml availability to suite metadata
Library           XML    use_lxml=yes
Resource          xml_resource.robot

*** Variables ***
${TÄG}            <täg attr="hyvä">sisältö</täg>
${T&#xE4;G}       <täg attr="hyv&#xE4;">sisältö</täg>
${XML}            <root>\n\t${TÄG}\n</root>
# lxml < 5 escapes non-ASCII attribute values when serializing to unicode,
# newer lxml keeps them as-is. Both forms are valid XML for the same data.
@{TÄG FORMS}      ${TÄG}    ${T&#xE4;G}

*** Test Cases ***
Element to string
    ${string}=    Element To String    ${XML}
    Should Be Equal    ${string}    ${XML}
    ${string}=    Element To String    ${TEST}
    Elements Should Be Equal    ${string}    ${TEST}

Element to string with encoding
    ${string}=    Element To String    ${TÄG}    encoding=latin-1
    ${expected}=    Encode String To Bytes    ${TÄG}    encoding=latin-1
    Should Be Equal    ${string}    ${expected}

Child element to string
    ${string}=    Element To String    ${XML}    xpath=täg
    Should Contain    ${TÄG FORMS}    ${string}
    ${string}=    Element To String    ${XML}    täg    latin-1
    ${expected 1}=    Encode String To Bytes    ${TÄG}    encoding=latin-1
    ${expected 2}=    Encode String To Bytes    ${T&#xE4;G}    encoding=latin-1
    Should Be True    $string in ($expected_1, $expected_2)

Log element
    ${string}=    Log Element    ${XML}
    Should Be Equal    ${string}    ${XML}
    Log Element    <root><tag a="1" c="3">päivää</tag></root>    DEBUG
    Log Element    ${TEST}

Log child element
    ${string}=    Log Element    ${XML}    xpath=täg
    Should Contain    ${TÄG FORMS}    ${string}
