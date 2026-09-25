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

"""Turning a validated flow graph into a structured program.

The graph is walked from its ``start`` node and folded into phases and
steps. Only *structured* graphs are accepted: an action node has one way
out, both branches of a decision re-join, a loop or try body returns to its
container with ``next``, a recovery region ends with ``continue`` or
``abort``, and every node is reachable. Anything else is a state machine in
disguise and is rejected with a message naming the node.
"""

from .schema import (ABORT, ACTION_KINDS, BODY, CONTAINER_KINDS, CONTINUE,
                     DECISION, DONE, END, FlowError, GATE, KEYWORD, LOOP, NEXT, NO,
                     ON_FAILURE, PHASE, SETUP, SLEEP, START, TEARDOWN, TEST, THEN, YES)


class Step:
    """Base class of all steps. ``node`` is the originating schema node."""

    def __init__(self, node):
        self.node = node

    @property
    def id(self):
        return self.node.id


class KeywordStep(Step):

    def __init__(self, node):
        super().__init__(node)
        self.keyword = node.keyword
        self.args = node.args
        self.assign = node.assign


class GateStep(KeywordStep):

    def __init__(self, node):
        super().__init__(node)
        self.timeout = node.timeout
        self.interval = node.interval
        self.on_timeout = node.on_timeout


class SleepStep(Step):

    def __init__(self, node):
        super().__init__(node)
        self.duration = node.duration


class Decision(Step):

    def __init__(self, node, yes, no):
        super().__init__(node)
        self.condition = node.condition
        self.yes = yes
        self.no = no


class Guarded(Step):
    """A body with an optional recovery region (``try`` and ``loop``)."""

    def __init__(self, node, body, recovery=None, then=None):
        super().__init__(node)
        self.body = body
        self.recovery = recovery    # list of steps, or None when no on_failure edge
        self.then = then            # CONTINUE or ABORT when recovery is set


class Try(Guarded):
    pass


class Loop(Guarded):

    def __init__(self, node, body, recovery=None, then=None):
        super().__init__(node, body, recovery, then)
        self.max_loops = node.max_loops
        self.max_seconds = node.max_seconds
        self.every = node.every


class Phase:

    def __init__(self, node, role, name, steps):
        self.node = node
        self.role = role
        self.name = name
        self.steps = steps


class Flow:
    """The structured program: optional setup, tests and optional teardown."""

    def __init__(self, data, setup, tests, teardown):
        self.data = data
        self.name = data.name
        self.setup = setup
        self.tests = tests
        self.teardown = teardown


def structure(data):
    """Fold validated :class:`~robot.flow.schema.FlowData` into a :class:`Flow`."""
    return _Structurer(data).structure()


class _Structurer:

    def __init__(self, data):
        self.data = data
        self.nodes = data.nodes
        self.out = {id: {} for id in self.nodes}
        for edge in data.edges:
            if edge.label in self.out[edge.source]:
                raise FlowError(f"Has two outgoing edges labelled "
                                f"'{edge.label or 'then'}'.", edge.source)
            self.out[edge.source][edge.label] = edge.target
        self.seen = set()
        self._joining = set()

    def structure(self):
        start = self._validate_degrees()
        self.seen.add(start.id)
        node_id = self.out[start.id][THEN]
        phases = []
        if self.nodes[node_id].kind not in (PHASE, END):
            # No phases at all: the whole flow is one test named after the flow.
            steps, node_id, via = self._sequence(node_id, set(), set())
            self._phase_must_end_plainly(via, node_id)
            phases.append(Phase(None, TEST, self.data.name, steps))
        while self.nodes[node_id].kind == PHASE:
            phase = self.nodes[node_id]
            self.seen.add(phase.id)
            steps, node_id, via = self._sequence(self.out[phase.id][THEN], set(), set())
            self._phase_must_end_plainly(via, node_id)
            phases.append(Phase(phase, phase.role, phase.name or phase.role, steps))
        self.seen.add(node_id)     # the end node
        unreachable = [id for id in self.nodes if id not in self.seen]
        if unreachable:
            raise FlowError(f"Unreachable node(s): {', '.join(unreachable)}.")
        return self._assemble(phases)

    def _phase_must_end_plainly(self, via, node_id):
        if via != THEN:
            raise FlowError(f"Edge labelled '{via}' must lead to the enclosing loop "
                            f"or try node, not to '{node_id}'.")

    def _validate_degrees(self):
        starts = [node for node in self.nodes.values() if node.kind == START]
        if len(starts) != 1:
            raise FlowError(f'Flow must have exactly one start node, found '
                            f'{len(starts)}.')
        if not any(node.kind == END for node in self.nodes.values()):
            raise FlowError('Flow must have at least one end node.')
        for node in self.nodes.values():
            labels = set(self.out[node.id])
            if node.kind in (START, PHASE):
                self._expect(node, labels, exactly={THEN},
                             hint='exactly one unlabelled outgoing edge')
            elif node.kind == END:
                self._expect(node, labels, exactly=set(),
                             hint='no outgoing edges')
            elif node.kind in ACTION_KINDS:
                if len(labels) != 1 or not labels <= {THEN, NEXT, CONTINUE, ABORT}:
                    self._degree_error(node, labels, "exactly one outgoing edge, "
                                       "unlabelled or labelled 'next', 'continue' "
                                       "or 'abort'")
            elif node.kind == DECISION:
                self._expect(node, labels, exactly={YES, NO},
                             hint="exactly two outgoing edges labelled 'yes' and 'no'")
            elif node.kind in CONTAINER_KINDS:
                if not {BODY, DONE} <= labels <= {BODY, DONE, ON_FAILURE}:
                    self._degree_error(node, labels, "outgoing edges labelled 'body' "
                                       "and 'done', optionally 'on_failure'")
        return starts[0]

    def _expect(self, node, labels, exactly, hint):
        if labels != exactly:
            self._degree_error(node, labels, hint)

    def _degree_error(self, node, labels, hint):
        have = ', '.join(repr(label or 'then') for label in sorted(labels)) or 'none'
        raise FlowError(f"A {node.kind} node must have {hint}; it has {have}.",
                        node.id)

    def _sequence(self, node_id, stop, visited):
        """Fold the plain sequence starting at ``node_id``.

        Returns ``(steps, exit_node_id, exit_label)``. The walk ends at a node
        in ``stop``, at a phase or end node, or when a labelled edge leaves
        the sequence; the label is returned so the caller can validate it.
        """
        steps = []
        via = THEN
        while True:
            node = self.nodes[node_id]
            if node_id in stop or node.kind in (PHASE, END):
                return steps, node_id, via
            if via != THEN:
                raise FlowError(f"Edge labelled '{via}' must lead to the enclosing "
                                f"loop or try node, not to '{node_id}'.")
            if node_id in visited:
                raise FlowError("Is reached twice in the same sequence. Only edges "
                                "labelled 'next' or 'continue' may go backwards.",
                                node_id)
            visited.add(node_id)
            self.seen.add(node_id)
            if node.kind in ACTION_KINDS:
                steps.append(self._action(node))
                (via, node_id), = self.out[node_id].items()
            elif node.kind == DECISION:
                step, node_id, via = self._decision(node, stop, visited)
                steps.append(step)
            else:
                steps.append(self._container(node, stop, visited))
                node_id = self.out[node.id][DONE]
                via = THEN

    def _action(self, node):
        return {KEYWORD: KeywordStep, GATE: GateStep, SLEEP: SleepStep}[node.kind](node)

    def _decision(self, node, stop, visited):
        join = self._join(node)
        yes, yes_exit, yes_via = self._sequence(self.out[node.id][YES],
                                                stop | {join}, visited)
        no, no_exit, no_via = self._sequence(self.out[node.id][NO],
                                             stop | {join}, visited)
        if yes_exit != join or no_exit != join:
            raise FlowError(f"The 'yes' and 'no' branches must re-join at "
                            f"'{join}' before anything else happens.", node.id)
        if yes_via != no_via:
            raise FlowError(f"The 'yes' branch leaves via '{yes_via or 'then'}' but "
                            f"the 'no' branch via '{no_via or 'then'}'; both "
                            f"branches must leave the same way.", node.id)
        return Decision(node, yes, no), join, yes_via

    def _join(self, decision):
        if decision.id in self._joining:
            raise FlowError('Decision branches form a cycle.', decision.id)
        self._joining.add(decision.id)
        try:
            yes_chain = self._chain(self.out[decision.id][YES])
            no_chain = set(self._chain(self.out[decision.id][NO]))
        finally:
            self._joining.discard(decision.id)
        for node_id in yes_chain:
            if node_id in no_chain:
                return node_id
        raise FlowError("The 'yes' and 'no' branches never re-join.", decision.id)

    def _chain(self, node_id):
        """Nodes met by following the structural successor from ``node_id``."""
        chain = []
        while node_id is not None and node_id not in chain:
            chain.append(node_id)
            node = self.nodes[node_id]
            if node.kind in ACTION_KINDS:
                (_, node_id), = self.out[node_id].items()
            elif node.kind == DECISION:
                node_id = self._join(node)
            elif node.kind in CONTAINER_KINDS:
                node_id = self.out[node_id][DONE]
            else:
                node_id = None
        return chain

    def _container(self, node, stop, visited):
        out = self.out[node.id]
        body, exit, via = self._sequence(out[BODY], stop | {node.id}, visited)
        if exit != node.id or via != NEXT:
            raise FlowError(f"The body of {node.kind} '{node.id}' must return to it "
                            f"with an edge labelled 'next'; it ends at '{exit}' via "
                            f"'{via or 'then'}'.", node.id)
        recovery = then = None
        if ON_FAILURE in out:
            recovery, exit, via = self._sequence(out[ON_FAILURE],
                                                 stop | {node.id, out[DONE]}, visited)
            if exit == node.id and via == CONTINUE:
                then = CONTINUE
            elif via == ABORT and (exit == out[DONE] or self.nodes[exit].kind == END):
                then = ABORT
                self.seen.add(exit)
            else:
                raise FlowError(f"The recovery of '{node.id}' must end with an edge "
                                f"labelled 'continue' back to '{node.id}', or "
                                f"'abort' to its 'done' target or an end node; it "
                                f"ends at '{exit}' via '{via or 'then'}'.", node.id)
        cls = Loop if node.kind == LOOP else Try
        return cls(node, body, recovery, then)

    def _assemble(self, phases):
        setup = teardown = None
        tests = []
        for index, phase in enumerate(phases):
            if phase.role == SETUP:
                if index != 0:
                    raise FlowError('The setup phase must be the first phase.',
                                    phase.node.id)
                setup = phase
            elif phase.role == TEARDOWN:
                if index != len(phases) - 1:
                    raise FlowError('The teardown phase must be the last phase.',
                                    phase.node.id)
                teardown = phase
            else:
                tests.append(phase)
        if not tests:
            raise FlowError('Flow must have at least one test phase.')
        names = set()
        for phase in tests:
            if phase.name in names:
                raise FlowError(f"Duplicate test phase name '{phase.name}'.",
                                phase.node.id)
            names.add(phase.name)
        return Flow(self.data, setup, tests, teardown)
