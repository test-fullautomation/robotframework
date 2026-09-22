# UNKNOWN status

Robot Framework classifies a test as PASS, FAIL or SKIP. This fork adds a fourth
status, **UNKNOWN**, for outcomes that are neither a pass nor a genuine
assertion failure.

## The model

The distinction is between *the product is wrong* and *we could not tell*:

| Situation | Status |
|-----------|--------|
| An assertion was evaluated and did not hold (`Should Be Equal`, `Fail`, …) | **FAIL** |
| The expected condition held | **PASS** |
| A keyword or variable was not found, an import failed, a `DataError` or an unexpected exception was raised, the system under test crashed | **UNKNOWN** |
| The test was skipped | **SKIP** |

Internally: `AssertionError` maps to FAIL, while `DataError`,
`UnknownAssertionError`, `AttributeError` and other unexpected exceptions map to
UNKNOWN. The point is diagnostic honesty — a broken environment or a missing
keyword should not be counted as a product defect, because doing so buries real
regressions in noise on a long endurance run.

!!! note "Calculator example"
    Testing `3 + 2`:

    - result `5` → **PASS**
    - result `4` → **FAIL** (the product computed the wrong answer)
    - crash, exception, or no result shown → **UNKNOWN** (there is nothing to judge)

UNKNOWN appears everywhere a status appears: the console summary
(`… , N unknown`), `log.html`, `report.html`, the statistics in `output.xml`
(an `unknown` attribute on each `stat`), and the return code.

## Import failures

An import error (`Library`, `Resource` or `Variables`) does not mean any test
assertion failed, so such tests become UNKNOWN. Which tests are affected is
controlled by an option:

```bash
robot --importfailure suite    my_suite.robot     # default
robot --importfailure test     my_suite.robot
```

- **`suite`** (default): the whole suite is UNKNOWN, with the import error as
  the suite setup message, because the suite environment is not as specified.
- **`test`**: only the tests that actually use keywords or variables from the
  failed import become UNKNOWN; the rest run normally.

`--ExitOnError` also triggers on these UNKNOWN-level errors, so a broken import
can stop the run early just like a fatal error.

## Return code

The process exit code encodes **both** the failed and the UNKNOWN test counts,
so a run that only produced UNKNOWN results is still non-zero and cannot look
green to CI:

```
rc = (min(unknown, 14) << 4) | min(failed, 15)
```

- `0` means everything passed (no failures, no unknowns).
- Decode with `failed = rc & 0x0F` and `unknown = (rc >> 4) & 0x0F`.
- The maximum is `239`, so the reserved Robot Framework codes `251`–`255` are
  never touched, and the value stays within the 8-bit range POSIX shells use.

| Example | failed | unknown | rc |
|---------|:------:|:-------:|:--:|
| all pass | 0 | 0 | 0 |
| 3 failed | 3 | 0 | 3 |
| 2 unknown | 0 | 2 | 32 |
| 6 failed, 2 unknown | 6 | 2 | 38 |

## Effect on output validity

Because UNKNOWN is a first-class status, the `output.xml` schema
(`doc/schema/robot.xsd`) is extended with the UNKNOWN status value, the `USER`
and `UNKNOWN` message levels, the `unknown` statistics attribute and the
`thread` element. Any tool validating fork output against a stock Robot
Framework schema must use the extended one.
