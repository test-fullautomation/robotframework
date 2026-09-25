# THREAD keyword — lifecycle and scopes

The `THREAD` keyword runs a block of a test in a background worker thread. It is
a block keyword, closed by `END`, and takes a **name** and a **daemon** flag:

```robotframework
*** Test Cases ***
Example
    THREAD    WORKER    ${False}
        Do Something Long
    END
    Log    main thread continues here immediately
```

The name identifies the thread for synchronisation, merging and the timeline.
The daemon flag selects the thread's **lifecycle scope**.

## Scopes

```plantuml
!include diagrams/thread_scopes.puml
```

| `daemon` | Scope | Behaviour |
|----------|-------|-----------|
| `${True}`  | **TEST** | The worker is stopped when its test case ends. Use it for background activity that belongs to one test. |
| `${False}` | **SUITE** | The worker keeps running across test cases and is stopped when the suite ends. Use it for a stimulus or monitor shared by several tests. |

Workers are always Python daemon threads, so they can never block interpreter
exit; their real lifecycle is managed by the scope reaper.

## Cooperative stopping

When a thread's scope ends, it is asked to stop cooperatively and is given a
**5-second grace period** to finish the keyword it is running. If it stops in
time it is reaped normally; if it does not, it is abandoned and reported with
the **UNKNOWN** status, so an unstoppable worker is visible in the report rather
than silently lost.

## Variable scoping

Each worker gets its **own copy** of the variables that existed when the thread
was started (snapshotted in the spawning thread, so no race with the main
thread's own scope changes). Variables set inside a worker stay local to that
worker; use [thread notifications](synchronisation.md) to hand data back to the
main thread.

!!! warning "Guards inside a worker"
    A condition evaluated inside a worker thread does not see variable writes
    made in the same worker after the guard was set up. When a StateMachine runs
    inside a THREAD, control it from the main flow with `Stop State Machine`
    rather than a same-thread variable guard.

## Where to go next

- [Result merging and timeline](merging-timeline.md) — how a worker's keywords
  appear in `log.html` and the interactive timeline.
- [Synchronisation](synchronisation.md) — notifications and reentrant locks.
