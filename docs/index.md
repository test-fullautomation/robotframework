# RobotFramework AIO

**Robot Framework for tests that run for hours or days, in parallel, against
real benches — and still give an honest verdict.**
{ .lead }

A fork of [Robot Framework](https://robotframework.org) 7.5 by the
[test-fullautomation](https://github.com/test-fullautomation) project. Your
existing suites run unchanged; everything below is opt-in.

<div class="cards" markdown>

<div class="card" markdown>
<p class="pain-line">"My test plan is a flowchart, but I maintain it as .robot by hand."</p>
### [Flow files](flow.md)
Run the flowchart itself. Gates, bounded loops and recovery are built in — and
two flows in two processes can meet and move in lockstep.
</div>

<div class="card" markdown>
<p class="pain-line">"A 48-hour run crashed at hour 40. We start again from zero."</p>
### [StateMachine](statemachine.md)
States, guarded transitions and a checkpoint after every step. A crash costs
one transition, not two days.
</div>

<div class="card" markdown>
<p class="pain-line">"The report is red — but half of it is the bench, not the product."</p>
### [UNKNOWN status](unknown-status.md)
A fourth verdict for "could not tell". Real regressions stand out, and the
return code tells CI which is which.
</div>

<div class="card" markdown>
<p class="pain-line">"I need a stimulus running beside my test — and then its log is gone."</p>
### [THREAD keyword](thread/lifecycle.md)
Background work with test or suite lifetime, merged back into `log.html`, plus
a timeline that shows who ran when.
</div>

<div class="card" markdown>
<p class="pain-line">"Something hung on dead hardware and nobody noticed for a day."</p>
### [Watchdog](watchdog.md)
A supervisor thread with heartbeat, stall detection and a deadline. It fails
the test in minutes, with the reason.
</div>

<div class="card" markdown>
<p class="pain-line">"The PC rebooted and output.xml is unreadable. Two days of log, gone."</p>
### [Segmented output](segmented-output.md)
The log is sealed into safe pieces as it grows. A crash loses one interval at
most.
</div>

</div>

## Built to be combined

```plantuml
!include diagrams/compose_48h.puml
```

Each feature works alone; together they cover a multi-day endurance run end to
end. [Long-running tests](long-running.md) maps each risk to the feature that
covers it.

## Try it in five minutes

```bash
git clone https://github.com/test-fullautomation/robotframework.git
cd robotframework && pip install -e .
robot --timeline timeline.html my_suite.robot
```

Then read [Getting started](getting-started.md), or open one of the runnable
demos next to the repository: `demo/flow/`, `demo/statemachine/`,
`demo/longrun/`.
