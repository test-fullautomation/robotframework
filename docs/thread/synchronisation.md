# Thread synchronisation

**Hand results back from a thread without shared-variable races.**
{ .lead }

!!! pain "The pain"
    A background thread computed something the test needs — or the test must
    wait until the stimulus has reached a certain point. Polling a shared
    variable is racy, and a `Sleep` is a guess.

!!! fix "The fix"
    Named notifications with a payload, a timeout and a bounded queue, plus
    reentrant locks for the rare case two threads must not overlap.

```plantuml
!include diagrams/sync_notify.puml
```

Workers are isolated (each has its own variable copy), so the fork provides
explicit primitives to coordinate between threads and hand data back.

## Thread notifications

A notification is a named signal, optionally carrying a payload, sent to a
target thread's queue. `MainThread` is a valid target.

```robotframework
*** Keywords ***
Producer
    Do Some Work
    Send Thread Notification    work_done    ${result}    MainThread

*** Test Cases ***
Wait For The Worker
    THREAD    PRODUCER    ${False}
        Producer
    END
    ${payload}=    Wait Thread Notification    work_done    timeout=10
    Log    worker returned ${payload}
```

| Keyword | Purpose |
|---------|---------|
| `Send Thread Notification    name    [payload]    [target]` | Signal `name` (with optional payload) to `target` (default: the current thread's partner). |
| `Wait Thread Notification    name    timeout=<time>` | Block until `name` arrives or the timeout elapses; returns the payload. Times out with an error you can catch. |

Sending to a thread that does not exist logs a warning and does nothing else.

Notifications are delivered through a bounded per-thread queue: if a queue is
never drained during a very long run it cannot grow without limit — the oldest
entry is dropped once the cap is reached, for both FIFO and LIFO queues.

## Reentrant locks

For mutual exclusion between workers, use the reentrant lock keywords. The same
thread may acquire a lock it already holds (reentrant), and acquisition can be
blocking, time-limited or non-blocking.

```robotframework
*** Keywords ***
Append Safely
    [Arguments]    ${value}
    Thread RLock Acquire    my_lock
    Append To List    ${SHARED}    ${value}
    Thread RLock Release    my_lock
```

| Keyword | Purpose |
|---------|---------|
| `Thread RLock Acquire    name    [timeout=]    [blocking=]` | Acquire the named reentrant lock. With `timeout=` waits at most that long; with `blocking=${False}` returns immediately. Returns whether the lock was acquired. |
| `Thread RLock Release    name` | Release one level of the named lock held by this thread. |

## Waiting for completion

```robotframework
Wait For Thread    WORKER    timeout=30s
```

`Wait For Thread` blocks the caller until the named worker has finished (or the
timeout elapses), which is the simplest way to join a worker before asserting on
its results.
