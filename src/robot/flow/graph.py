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
container with ``next``, every branch of a parallel region returns to it
with ``join``, a recovery region ends with ``continue`` or ``abort``, and
every node is reachable. Anything else is a state machine in disguise and
is rejected with a message naming the node.
"""

from .schema import (ABORT, ACTION_KINDS, BODY, BRANCH, CONTAINER_KINDS, CONTINUE,
                     DECISION, DONE, END, FLOW, FlowError, GATE, JOIN, KEYWORD, LOOP, NEXT,
                     NO, ON_FAILURE, PARALLEL, PHASE, SETUP, SLEEP, START, TEARDOWN, TEST,
                     THEN, YES)


# How a loop, try or parallel region continues after its body: 'done'
# normally, or the label an action node would carry when the container is
# the last node of an enclosing body ('next'), recovery region ('continue',
# 'abort') or branch ('join').
EXIT_LABELS = frozenset((DONE, NEXT, CONTINUE, ABORT, JOIN))


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
        self.retry = node.retry         # policy dict, or None
        self.timeout = node.timeout     # time limit of one attempt, or None


class GateStep(KeywordStep):

    def __init__(self, node):
        super().__init__(node)
        self.timeout = node.timeout
        self.interval = node.interval
        self.on_timeout = node.on_timeout


class FlowStep(Step):
    """A call of a sub-flow file; the builder turns the file into a keyword."""

    def __init__(self, node):
        super().__init__(node)
        self.file = node.file
        self.args = node.args       # parameter name -> value


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


class Branch:
    """One branch of a parallel region: named after its first node."""

    def __init__(self, id, steps):
        self.id = id
        self.steps = steps


class Parallel(Guarded):
    """Branches that run at the same time and join before the plan goes on.

    ``body`` is ``None``: the steps are in ``branches``. The recovery, when
    there is one, runs after the join like a try node's.
    """

    def __init__(self, node, branches, recovery=None, then=None):
        super().__init__(node, None, recovery, then)
        self.branches = branches        # list of Branch
        self.timeout = node.timeout
        self.stop_others = node.stop_others


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
        # 'branch' is the one label a node may carry several times: the
        # branches of a parallel region, in file order.
        self.branches = {id: [] for id in self.nodes}
        for edge in data.edges:
            if edge.label == BRANCH:
                if self.nodes[edge.source].kind != PARALLEL:
                    raise FlowError("Only a parallel node has edges labelled 'branch'.",
                                    edge.source)
                self.branches[edge.source].append(edge.target)
                continue
            if edge.label in self.out[edge.source]:
                raise FlowError(f"Has two outgoing edges labelled "
                                f"'{edge.label or 'then'}'.", edge.source)
            self.out[edge.source][edge.label] = edge.target
        self.seen = set()
        self._joining = set()
        self._region = None         # the parallel node whose branch is being folded

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
            raise FlowError(f"Edge labelled '{via}' must lead to the enclosing loop, "
                            f"try or parallel node, not to '{node_id}'.")

    def _validate_degrees(self):
        starts = [node for node in self.nodes.values() if node.kind == START]
        if len(starts) != 1:
            raise FlowError(f'Flow must have exactly one start node, found '
                            f'{len(starts)}.')
        if not any(node.kind == END for node in self.nodes.values()):
            raise FlowError('Flow must have at least one end node.')
        into_start = [edge.source for edge in self.data.edges
                      if edge.target == starts[0].id]
        if into_start:
            raise FlowError(f"The start node cannot have incoming edges (from "
                            f"{', '.join(repr(s) for s in into_start)}).", starts[0].id)
        for node in self.nodes.values():
            labels = set(self.out[node.id])
            if node.kind in (START, PHASE):
                self._expect(node, labels, exactly={THEN},
                             hint='exactly one unlabelled outgoing edge')
            elif node.kind == END:
                self._expect(node, labels, exactly=set(),
                             hint='no outgoing edges')
            elif node.kind in ACTION_KINDS:
                if len(labels) != 1 or not labels <= {THEN, NEXT, CONTINUE, ABORT, JOIN}:
                    self._degree_error(node, labels, "exactly one outgoing edge, "
                                       "unlabelled or labelled 'next', 'continue', "
                                       "'abort' or 'join'")
            elif node.kind == DECISION:
                self._expect(node, labels, exactly={YES, NO},
                             hint="exactly two outgoing edges labelled 'yes' and 'no'")
            elif node.kind in CONTAINER_KINDS:
                exits = labels & EXIT_LABELS
                if BODY not in labels or len(exits) != 1 \
                        or not labels <= {BODY, ON_FAILURE} | exits:
                    self._degree_error(node, labels, "outgoing edges labelled 'body' "
                                       "and exactly one of 'done', 'next', 'continue' "
                                       "or 'abort', optionally 'on_failure'")
            elif node.kind == PARALLEL:
                exits = labels & EXIT_LABELS
                if len(self.branches[node.id]) < 2 or len(exits) != 1 \
                        or not labels <= {ON_FAILURE} | exits:
                    have = [BRANCH] * len(self.branches[node.id]) + sorted(labels)
                    self._degree_error(node, have, "two or more outgoing edges labelled "
                                       "'branch' and exactly one of 'done', 'next', "
                                       "'continue' or 'abort', optionally 'on_failure'")
        return starts[0]

    def _exit(self, node_id):
        """The (label, target) a loop or try continues with after its body.

        Normally ``done``; ``next``, ``continue`` or ``abort`` when the
        container is itself the last node of an enclosing body or recovery.
        """
        (label, target), = [(label, target) for label, target in self.out[node_id].items()
                            if label in EXIT_LABELS]
        return label, target

    def _expect(self, node, labels, exactly, hint):
        if labels != exactly:
            self._degree_error(node, labels, hint)

    def _degree_error(self, node, labels, hint):
        labels = labels if isinstance(labels, list) else sorted(labels)
        have = ', '.join(repr(label or 'then') for label in labels) or 'none'
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
                                f"loop, try or parallel node, not to '{node_id}'.")
            if node_id in visited:
                raise FlowError("Is reached twice in the same sequence. Only edges "
                                "labelled 'next', 'continue' or 'join' may go backwards.",
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
                if node.kind == PARALLEL:
                    steps.append(self._parallel(node, stop, visited))
                else:
                    steps.append(self._container(node, stop, visited))
                label, node_id = self._exit(node.id)
                via = THEN if label == DONE else label

    def _action(self, node):
        return {KEYWORD: KeywordStep, GATE: GateStep, SLEEP: SleepStep,
                FLOW: FlowStep}[node.kind](node)

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
            elif node.kind in CONTAINER_KINDS or node.kind == PARALLEL:
                _, node_id = self._exit(node_id)
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
        recovery, then = self._recovery(node, stop, visited)
        cls = Loop if node.kind == LOOP else Try
        return cls(node, body, recovery, then)

    def _parallel(self, node, stop, visited):
        """A parallel region: every branch is a sequence that returns to the
        node with 'join'; a node belongs to one branch; no region inside a
        branch (the report, the stop ladder and the verdicts would need a
        second level for a case no bench has asked for)."""
        if self._region is not None:
            raise FlowError(f"A parallel region cannot be inside a branch of another "
                            f"parallel region ('{self._region}'); split the plan or "
                            f"run the inner branches one after the other.", node.id)
        self._region = node.id
        branches = []
        try:
            for first in self.branches[node.id]:
                steps, exit, via = self._sequence(first, stop | {node.id}, visited)
                if exit != node.id or via != JOIN:
                    raise FlowError(f"The branch of '{node.id}' starting at '{first}' "
                                    f"must return to it with an edge labelled 'join'; "
                                    f"it ends at '{exit}' via '{via or 'then'}'.",
                                    node.id)
                if not steps:
                    raise FlowError(f"The branch starting at '{first}' is empty.",
                                    node.id)
                branches.append(Branch(first, steps))
        finally:
            self._region = None
        recovery, then = self._recovery(node, stop, visited)
        return Parallel(node, branches, recovery, then)

    def _recovery(self, node, stop, visited):
        """The recovery region of a loop, try or parallel node, and how it
        ends: ``(steps, 'continue' | 'abort')``, or ``(None, None)``."""
        out = self.out[node.id]
        if ON_FAILURE not in out:
            return None, None
        _, after = self._exit(node.id)
        recovery, exit, via = self._sequence(out[ON_FAILURE],
                                             stop | {node.id, after}, visited)
        if exit == node.id and via == CONTINUE:
            return recovery, CONTINUE
        if via == ABORT and (exit == after or self.nodes[exit].kind == END):
            self.seen.add(exit)
            return recovery, ABORT
        raise FlowError(f"The recovery of '{node.id}' must end with an edge "
                        f"labelled 'continue' back to '{node.id}', or "
                        f"'abort' to the node after it or an end node; it "
                        f"ends at '{exit}' via '{via or 'then'}'.", node.id)

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
