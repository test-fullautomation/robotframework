*** Test Cases ***
For
    [Documentation]    UNKNOWN    'For' is a reserved keyword. It must be an upper case 'FOR' when used as a marker.
    For    ${x}    IN    invalid

Valid END after For
    [Documentation]    UNKNOWN
    ...    Several failures occurred:
    ...
    ...    1) 'For' is a reserved keyword. It must be an upper case 'FOR' when used as a marker.
    ...
    ...    2) END is not allowed in this context.
    For    ${x}    IN    invalid
        Log    ${x}
    END

If
    [Documentation]    UNKNOWN    'If' is a reserved keyword. It must be an upper case 'IF' when used as a marker.
    If    invalid

Else If
    [Documentation]    UNKNOWN    'Else If' is a reserved keyword. It must be an upper case 'ELSE IF' and follow an opening 'IF' when used as a marker.
    Else If    invalid

Else
    [Documentation]    UNKNOWN    'Else' is a reserved keyword. It must be an upper case 'ELSE' and follow an opening 'IF' when used as a marker.
    Else

Else inside valid IF
    [Documentation]    UNKNOWN    'Else' is a reserved keyword. It must be an upper case 'ELSE' and follow an opening 'IF' when used as a marker.
    IF    False
        No operation
    Else
        No operation
    END

Else If inside valid IF
    [Documentation]    UNKNOWN    'Else If' is a reserved keyword. It must be an upper case 'ELSE IF' and follow an opening 'IF' when used as a marker.
    IF    False
        No operation
    Else If    invalid
        No operation
    END

End
    [Documentation]    UNKNOWN    'End' is a reserved keyword. It must be an upper case 'END' when used as a marker to close a block.
    End

End after valid FOR header
    [Documentation]    UNKNOWN    'End' is a reserved keyword. It must be an upper case 'END' when used as a marker to close a block.
    FOR    ${x}   IN    whatever
        Log    ${x}
    End

End after valid If header
    [Documentation]    UNKNOWN    'End' is a reserved keyword. It must be an upper case 'END' when used as a marker to close a block.
    IF    True
        No operation
    End

Reserved inside FOR
    [Documentation]    UNKNOWN    'If' is a reserved keyword. It must be an upper case 'IF' when used as a marker.
    FOR    ${x}    IN    whatever
        If    ${x}
    END

Reserved inside IF
    [Documentation]    UNKNOWN
    ...    Several failures occurred:
    ...
    ...    1) 'For' is a reserved keyword. It must be an upper case 'FOR' when used as a marker.
    ...
    ...    2) 'If' is a reserved keyword. It must be an upper case 'IF' when used as a marker.
    ...
    ...    3) END is not allowed in this context.
    ...
    ...    4) 'Return' is a reserved keyword.
    ...
    ...    5) END is not allowed in this context.
    IF    True
        For    ${x}    IN    invalid
            Log     ${x}
        END
        If    False
            No Operation
        END
        Return
    END
