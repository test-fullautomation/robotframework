*** Settings ***
Resource          try_except_resource.robot
Suite Setup       Run Tests    ${EMPTY}    running/try_except/invalid_try_except.robot
Test Template     Verify try except and block statuses

*** Test Cases ***
TRY without END
    TRY:UNKNOWN    EXCEPT:NOT RUN    FINALLY:NOT RUN

TRY without body
    TRY:UNKNOWN    EXCEPT:NOT RUN    FINALLY:NOT RUN

TRY without EXCEPT or FINALLY
    TRY:UNKNOWN

TRY with ELSE without EXCEPT or FINALLY
    TRY:UNKNOWN    ELSE:NOT RUN

TRY with argument
    TRY:UNKNOWN    EXCEPT:NOT RUN    FINALLY:NOT RUN

EXCEPT without body
    TRY:UNKNOWN    EXCEPT:NOT RUN    EXCEPT:NOT RUN    FINALLY:NOT RUN

Default EXCEPT not last
    TRY:UNKNOWN    EXCEPT:NOT RUN    EXCEPT:NOT RUN    FINALLY:NOT RUN

Multiple default EXCEPTs
    TRY:UNKNOWN    EXCEPT:NOT RUN    EXCEPT:NOT RUN    ELSE:NOT RUN

AS requires variable
    TRY:UNKNOWN    EXCEPT:NOT RUN

AS accepts only one variable
    TRY:UNKNOWN    EXCEPT:NOT RUN

Invalid AS variable
    TRY:UNKNOWN    EXCEPT:NOT RUN

ELSE with argument
    TRY:UNKNOWN    EXCEPT:NOT RUN    ELSE:NOT RUN    FINALLY:NOT RUN

ELSE without body
    TRY:UNKNOWN    EXCEPT:NOT RUN    ELSE:NOT RUN    FINALLY:NOT RUN

Multiple ELSE blocks
    TRY:UNKNOWN    EXCEPT:NOT RUN    ELSE:NOT RUN    ELSE:NOT RUN    FINALLY:NOT RUN

FINALLY with argument
    TRY:UNKNOWN    EXCEPT:NOT RUN    FINALLY:NOT RUN

FINALLY without body
    TRY:UNKNOWN    FINALLY:NOT RUN

Multiple FINALLY blocks
    TRY:UNKNOWN    EXCEPT:NOT RUN    FINALLY:NOT RUN    FINALLY:NOT RUN

ELSE before EXCEPT
    TRY:UNKNOWN    EXCEPT:NOT RUN    ELSE:NOT RUN    EXCEPT:NOT RUN   FINALLY:NOT RUN

FINALLY before EXCEPT
    TRY:UNKNOWN    EXCEPT:NOT RUN    FINALLY:NOT RUN    EXCEPT:NOT RUN

FINALLY before ELSE
    TRY:UNKNOWN    EXCEPT:NOT RUN    FINALLY:NOT RUN    ELSE:NOT RUN

Template with TRY
    TRY:UNKNOWN    EXCEPT:NOT RUN

Template with TRY inside IF
    TRY:UNKNOWN    EXCEPT:NOT RUN    path=body[0].body[0].body[0]

Template with IF inside TRY
    TRY:UNKNOWN    FINALLY:NOT RUN

BREAK in FINALLY
    TRY:PASS    FINALLY:UNKNOWN    tc_status=UNKNOWN    path=body[0].body[0].body[0]

CONTINUE in FINALLY
    TRY:PASS    FINALLY:UNKNOWN    tc_status=UNKNOWN    path=body[0].body[0].body[0]

RETURN in FINALLY
    TRY:PASS    FINALLY:UNKNOWN    tc_status=UNKNOWN    path=body[0].body[0]

Invalid TRY/EXCEPT causes syntax error that cannot be caught
    TRY:FAIL    EXCEPT:NOT RUN    ELSE:NOT RUN

Dangling FINALLY
    [Template]    Check Test Case
    ${TEST NAME}
