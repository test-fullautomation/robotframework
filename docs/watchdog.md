# Watchdog library

The `Watchdog` library supervises long-running work — typically a worker thread
— and detects when it stalls. It is a separate feature from the StateMachine
library; the two are often combined but neither requires the other.

```robotframework
*** Settings ***
Library    Watchdog
```

## Idea

A watchdog is fed periodically by the work it supervises. If it is not fed
within a configured stall timeout, the watchdog *fires* — that condition is
recorded and can be asserted on. A heartbeat can also log progress at a fixed
interval so a long run shows signs of life in the log.

```robotframework
*** Test Cases ***
Supervised Run
    Configure Watchdog    stall_timeout=5s    heartbeat=2s
    THREAD    SUPERVISOR    ${True}
        Run Watchdog
    END
    Do Long Work        # calls Feed Watchdog as it makes progress
    Stop Watchdog
    Watchdog Should Not Have Fired
```

## Keywords

| Keyword | Purpose |
|---------|---------|
| `Configure Watchdog    stall_timeout=<time>    heartbeat=<time>` | Set the maximum allowed gap between feeds and the heartbeat interval. |
| `Run Watchdog` | Run the supervision loop (typically inside a `THREAD` block). |
| `Feed Watchdog` | Signal progress; resets the stall timer. Call it from the supervised work. |
| `Get Watchdog Status` | Return the watchdog's state (e.g. running, and whether it has fired). |
| `Stop Watchdog` | Stop the supervision loop. |
| `Watchdog Should Not Have Fired` | Assert that no stall was detected. |
| `Reset Watchdogs` | Clear watchdog state (suite teardown). |

## Feeding safely from supervised work

A common pattern is to feed only while the watchdog is actually running, so the
same keyword works with or without supervision:

```robotframework
*** Keywords ***
Feed If Running
    ${status}=    Run Keyword And Ignore Error    Get Watchdog Status
    IF    $status[0] == 'PASS' and $status[1] == 'RUNNING'
        Feed Watchdog
    END
```

See the [StateMachine](statemachine.md) battery-endurance example for a watchdog
supervising a state machine's `during` keywords.
