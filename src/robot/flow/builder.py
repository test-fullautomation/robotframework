#  Copyright 2008-2015 Nokia Networks
#  Copyright 2016-     Robot Framework Foundation
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Building an executable :class:`~robot.running.TestSuite` from a :class:`~robot.flow.graph.Flow`.

Mapping:

- setup phase     -> suite setup calling the generated user keyword ``Flow Setup``
- test phase      -> one test case
- teardown phase  -> suite teardown calling ``Flow Teardown``
- keyword         -> keyword call
- gate            -> ``Flow Gate`` from ``robot.flow.keywords``
- sleep           -> ``Sleep``
- decision        -> ``IF`` / ``ELSE``
- loop            -> ``WHILE`` (``limit=max_loops on_limit=pass``, a deadline
                     condition for ``max_seconds``, ``Sleep every`` last)
- on_failure      -> ``TRY`` / ``EXCEPT AS ${flow_error}``; ``abort`` re-raises
                     with ``Fail`` after the recovery ran
"""

import re

from robot.model import BodyItem
from robot.running import TestSuite
from robot.utils import timestr_to_secs

from .graph import Decision, GateStep, KeywordStep, Loop, SleepStep, Try
from .schema import ABORT


FLOW_LIBRARY = 'robot.flow.keywords'
GATE_KEYWORD = 'Flow Gate'
SETUP_KEYWORD = 'Flow Setup'
TEARDOWN_KEYWORD = 'Flow Teardown'
ERROR_VARIABLE = '${flow_error}'
DEADLINE_VARIABLE = '${flow_deadline_%s}'


def build_suite(flow, source=None):
    """Return a runnable suite for ``flow``. ``source`` is the flow file path."""
    data = flow.data
    suite = TestSuite(name=data.name, source=source)
    resource = suite.resource
    # Imports given as paths resolve relative to the flow file, like in .robot files.
    resource.source = source
    resource.imports.library(FLOW_LIBRARY)
    for name, args in data.libraries:
        resource.imports.library(name, args)
    for path in data.resources:
        resource.imports.resource(path)
    for name, args in data.variable_files:
        resource.imports.variables(name, args)
    for name, value in data.variables.items():
        resource.variables.create(name='${%s}' % name, value=[value])
    emitter = _Emitter()
    if flow.setup:
        emitter.user_keyword(resource, SETUP_KEYWORD, flow.setup.steps)
        suite.setup.config(name=SETUP_KEYWORD)
    for phase in flow.tests:
        test = suite.tests.create(name=phase.name)
        emitter.emit(test.body, phase.steps)
    if flow.teardown:
        emitter.user_keyword(resource, TEARDOWN_KEYWORD, flow.teardown.steps)
        suite.teardown.config(name=TEARDOWN_KEYWORD)
    return suite


class _Emitter:
    """Emits steps into bodies. One instance per built suite, so that the
    variable names it invents are unique within that suite."""

    def __init__(self):
        self._deadlines = {}     # loop node id -> deadline variable name

    def user_keyword(self, resource, name, steps):
        keyword = resource.keywords.create(name=name)
        self.emit(keyword.body, steps)
        return keyword

    def emit(self, body, steps):
        if not steps:
            body.create_keyword(name='No Operation')
            return
        for step in steps:
            self._emitters[type(step)](self, body, step)

    def _keyword(self, body, step):
        body.create_keyword(name=step.keyword, args=step.args, assign=step.assign)

    def _gate(self, body, step):
        args = [step.timeout, step.interval, step.on_timeout, step.keyword, *step.args]
        body.create_keyword(name=GATE_KEYWORD, args=args, assign=step.assign)

    def _sleep(self, body, step):
        body.create_keyword(name='Sleep', args=[step.duration])

    def _decision(self, body, step):
        if_ = body.create_if()
        yes = if_.body.create_branch(BodyItem.IF, condition=step.condition)
        self.emit(yes.body, step.yes)
        no = if_.body.create_branch(BodyItem.ELSE)
        self.emit(no.body, step.no)

    def _loop(self, body, step):
        condition = 'True'
        if step.max_seconds:
            deadline = self._deadline_variable(step)
            seconds = timestr_to_secs(step.max_seconds)
            body.create_keyword(name='Evaluate', args=[f'time.time() + {seconds}'],
                                assign=[deadline])
            condition = f'time.time() < {deadline}'
        if step.max_loops:
            limit, on_limit = str(step.max_loops), 'pass'
        else:
            # Only a deadline bounds the loop; lift Robot's default iteration limit.
            limit, on_limit = 'NONE', None
        while_ = body.create_while(condition=condition, limit=limit, on_limit=on_limit)
        self._guarded(while_.body, step)
        if step.every:
            while_.body.create_keyword(name='Sleep', args=[step.every])

    def _deadline_variable(self, step):
        """A variable name derived from the loop id, unique within the suite.

        Sanitising ids can make different ids equal (``a-b`` and ``a_b``);
        a nested loop reusing its parent's variable would move the parent's
        deadline, so collisions get a numeric suffix.
        """
        base = re.sub(r'\W', '_', step.id) or 'loop'
        name, number = base, 1
        while name in self._deadlines.values():
            number += 1
            name = f'{base}_{number}'
        self._deadlines[step.id] = name
        return DEADLINE_VARIABLE % name

    def _try(self, body, step):
        self._guarded(body, step)

    def _guarded(self, body, step):
        if step.recovery is None:
            self.emit(body, step.body)
            return
        try_ = body.create_try()
        main = try_.body.create_branch(BodyItem.TRY)
        self.emit(main.body, step.body)
        recovery = try_.body.create_branch(BodyItem.EXCEPT, variable=ERROR_VARIABLE)
        self.emit(recovery.body, step.recovery)
        if step.then == ABORT:
            recovery.body.create_keyword(name='Fail', args=[ERROR_VARIABLE])

    _emitters = {
        KeywordStep: _keyword,
        GateStep: _gate,
        SleepStep: _sleep,
        Decision: _decision,
        Loop: _loop,
        Try: _try,
    }
