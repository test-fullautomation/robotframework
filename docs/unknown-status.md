# UNKNOWN status

**Stop chasing product bugs that are really bench problems.**
{ .lead }

!!! pain "The pain"
    The nightly report is red: 40 failures. Three are real regressions; the rest
    are a library that failed to import, a power supply that was switched off, a
    keyword that was renamed. Stock Robot Framework calls them all FAIL, so every
    morning someone digs through 40 logs to find the three that matter — and CI
    cannot tell the difference either.

!!! fix "The fix"
    A fourth verdict beside PASS, FAIL and SKIP. **FAIL** means an assertion was
    checked and the product was wrong. **UNKNOWN** means nothing could be judged
    — the environment broke. The report, the statistics and the return code keep
    them apart.

```plantuml
!include diagrams/unknown_verdict.puml
```

| The same night | Stock Robot Framework | RobotFramework AIO |
|----------------|-----------------------|--------------------|
| Report | 40 failed | **3 failed**, 37 unknown |
| First question | "What broke in the product?" | "Fix the bench, then look at 3 tests" |
| Return code | 40 | `(min(37,14) << 4) \| 3` = 227 → *3 failed, 14+ unknown* |

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
UNKNOWN. One exception is kept from earlier versions: an invalid `TRY/EXCEPT`
structure (for example a missing `END`) marks the `TRY` branch UNKNOWN but
fails the test. The point is diagnostic honesty — a broken environment or a missing
keyword should not be counted as a product defect, because doing so buries real
regressions in noise on a long endurance run.

!!! note "Calculator example"
    Testing `3 + 2`:

    - result `5` → **PASS**
    - result `4` → **FAIL** (the product computed the wrong answer)
    - crash, exception, or no result shown → **UNKNOWN** (there is nothing to judge)

UNKNOWN appears everywhere a status appears: the console summary
(`… , N unknown`), `log.html`, `report.html`, the statistics in `output.xml`
(an `unknown` attribute on each `stat`), and the return code. Messages logged
at the UNKNOWN level are also written to the console, like errors. The legacy
output format (`--legacyoutput`) has no UNKNOWN statistics attribute.

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

- `0` means no test failed and none is unknown. Skipped tests are not counted,
  so a run in which every test was skipped also returns `0`.
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

Because UNKNOWN is a first-class status, the output schemas
(`doc/schema/result.xsd` for XML and `doc/schema/result.json` for JSON output)
are extended with the UNKNOWN status value, the `USER` and `UNKNOWN` message
levels, the `unknown` statistics attribute and the `thread` element. Any tool validating fork output against a stock Robot
Framework schema must use the extended one.
