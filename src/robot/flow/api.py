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

"""Writing a flow in Python (experimental).

The same graph as a flow file, written as code: an IDE completes it, checks
it and refactors it, and the result is exactly the dictionary a
``.flow.json`` file holds -- so the validator, the diagram, the live view
and the reports stay the same::

    from robot.flow.api import Flow

    with Flow('Lab Cycle', libraries=['checks.py'], variables={'MODE': 'EMC'}) as f:
        with f.setup():
            f.keyword('power', 'Power On', '${VOLTS}')
        with f.test('Cycles'):
            with f.loop('cycles', max_loops=3, every='1s') as loop:
                f.keyword('measure', 'Measure Current', assign='${I}')
                with f.decision('emc', "$MODE == 'EMC'") as d:
                    with d.yes():
                        f.keyword('burst', 'Run Emc Burst')
                    with d.no():
                        f.keyword('climate', 'Run Climate Step')
                with loop.on_failure('continue'):
                    f.keyword('recover', 'Recover Dut')
    f.save('lab_cycle.flow.json')          # or: f.to_dict()

Blocks are ``with`` statements, so the code has the shape of the plan. Every
node needs an id: it is what the diagram, the live view and the log show.
"""

import json
from contextlib import contextmanager

from .schema import (ABORT, BODY, CONTINUE, DECISION, DONE, END, FLOW, GATE, KEYWORD, LOOP,
                     NEXT, NO, ON_FAILURE, PHASE, SETUP, SLEEP, START, TEARDOWN, TEST, THEN,
                     TRY, YES, FlowError, load_flow)


class Flow:
    """Builds the nodes and edges of one flow. Use as a context manager."""

    def __init__(self, name, libraries=(), resources=(), variable_files=(), variables=None,
                 checkpoint=None, checkpoint_every=None):
        self.name = name
        self._checkpoint = checkpoint
        self._checkpoint_every = checkpoint_every
        self._imports = {'libraries': list(libraries), 'resources': list(resources),
                         'variables': list(variable_files)}
        self._variables = dict(variables or {})
        self._nodes = [{'id': 'start', 'kind': START}]
        self._edges = []
        self._ids = {'start'}
        # Open ends waiting for the next node: (node id, edge label).
        self._open = [('start', THEN)]
        self._closed = False

    # ---------------------------------------------------------------- blocks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self._finish()
        return False

    @contextmanager
    def setup(self):
        yield from self._phase({'role': SETUP}, 'setup')

    @contextmanager
    def test(self, name, id=None):
        yield from self._phase({'role': TEST, 'name': name}, id or _slug(name))

    @contextmanager
    def teardown(self):
        yield from self._phase({'role': TEARDOWN}, 'teardown')

    def _phase(self, attrs, id):
        self._add(dict(attrs, id=id, kind=PHASE))
        self._open = [(id, THEN)]
        yield self

    @contextmanager
    def loop(self, id, max_loops=None, max_seconds=None, every=None):
        attrs = {k: v for k, v in (('max_loops', max_loops), ('max_seconds', max_seconds),
                                   ('every', every)) if v is not None}
        yield from self._container(dict(attrs, id=id, kind=LOOP))

    @contextmanager
    def try_(self, id):
        yield from self._container({'id': id, 'kind': TRY})

    def _container(self, node):
        self._add(node)
        block = _Container(self, node['id'])
        self._open = [(node['id'], BODY)]
        yield block
        block._close_body()

    @contextmanager
    def decision(self, id, condition):
        self._add({'id': id, 'kind': DECISION, 'condition': condition})
        block = _Decision(self, id)
        yield block
        block._close()

    # ----------------------------------------------------------------- steps

    def keyword(self, id, keyword, *args, assign=None):
        node = {'id': id, 'kind': KEYWORD, 'keyword': keyword}
        if args:
            node['args'] = list(args)
        if assign:
            node['assign'] = assign
        self._step(node)

    def gate(self, id, keyword, *args, timeout, interval=None, on_timeout=None, assign=None):
        node = {'id': id, 'kind': GATE, 'keyword': keyword, 'timeout': timeout}
        if args:
            node['args'] = list(args)
        for key, value in (('interval', interval), ('on_timeout', on_timeout), ('assign', assign)):
            if value is not None:
                node[key] = value
        self._step(node)

    def sleep(self, id, duration):
        self._step({'id': id, 'kind': SLEEP, 'duration': duration})

    def flow(self, id, file, **args):
        node = {'id': id, 'kind': FLOW, 'file': file}
        if args:
            node['args'] = args
        self._step(node)

    def _step(self, node):
        self._add(node)
        self._open = [(node['id'], THEN)]

    # ---------------------------------------------------------------- output

    def to_dict(self):
        """The flow file's content, validated."""
        self._finish()
        data = {'flow': {'name': self.name, 'version': 1}}
        if self._checkpoint is not None:
            data['flow']['checkpoint'] = self._checkpoint
        if self._checkpoint_every is not None:
            data['flow']['checkpoint_every'] = self._checkpoint_every
        imports = {k: v for k, v in self._imports.items() if v}
        if imports:
            data['imports'] = imports
        if self._variables:
            data['variables'] = self._variables
        data['nodes'] = self._nodes
        data['edges'] = self._edges
        load_flow(data)
        return data

    def save(self, path):
        with open(path, 'w', encoding='UTF-8', newline='\n') as file:
            json.dump(self.to_dict(), file, indent=2, ensure_ascii=False)
            file.write('\n')
        return path

    # -------------------------------------------------------------- plumbing

    def _add(self, node):
        if self._closed:
            raise FlowError('The flow is finished; no more nodes can be added.')
        if node['id'] in self._ids:
            raise FlowError('Duplicate node id.', node['id'])
        self._ids.add(node['id'])
        self._nodes.append(node)
        for source, label in self._open:
            self._edge(source, node['id'], label)
        self._open = []

    def _edge(self, source, target, label):
        self._edges.append([source, target] if label == THEN else
                           {'from': source, 'to': target, 'label': label})

    def _finish(self):
        if not self._closed:
            self._add({'id': 'end', 'kind': END})
            self._closed = True


class _Decision:

    def __init__(self, flow, id):
        self._flow = flow
        self._id = id
        self._ends = []
        self._branches = set()

    @contextmanager
    def yes(self):
        yield from self._branch(YES)

    @contextmanager
    def no(self):
        yield from self._branch(NO)

    def _branch(self, label):
        if label in self._branches:
            raise FlowError(f"The '{label}' branch is given twice.", self._id)
        self._branches.add(label)
        self._flow._open = [(self._id, label)]
        yield self._flow
        self._ends.extend(self._flow._open)

    def _close(self):
        for label in (YES, NO):
            if label not in self._branches:      # an empty branch goes straight to the join
                self._ends.append((self._id, label))
        self._flow._open = self._ends


class _Container:
    """The body of a loop or try, and its optional recovery."""

    def __init__(self, flow, id):
        self._flow = flow
        self._id = id
        self._recovery_ends = None
        self._then = None

    def _close_body(self):
        flow = self._flow
        if flow._open == [(self._id, BODY)]:
            raise FlowError('The body is empty.', self._id)
        for source, label in flow._open:
            flow._edge(source, self._id, _ending(label, NEXT, self._id))
        flow._open = [(self._id, DONE)]
        if self._recovery_ends:
            flow._open += self._recovery_ends    # 'abort' ends lead to the node after it

    @contextmanager
    def on_failure(self, then=CONTINUE):
        """The recovery: ``then`` is 'continue' (back to the loop or try) or 'abort'."""
        if then not in (CONTINUE, ABORT):
            raise FlowError("on_failure 'then' must be 'continue' or 'abort'.", self._id)
        flow = self._flow
        body_ends = flow._open
        flow._open = [(self._id, ON_FAILURE)]
        yield flow
        if flow._open == [(self._id, ON_FAILURE)]:
            raise FlowError('The recovery is empty.', self._id)
        if then == CONTINUE:
            for source, label in flow._open:
                flow._edge(source, self._id, _ending(label, CONTINUE, self._id))
        else:
            self._recovery_ends = [(source, _ending(label, ABORT, self._id))
                                   for source, label in flow._open]
        flow._open = body_ends


def _ending(label, last, container):
    """The label an open end gets when it is the last one of a region.

    A plain step ends a loop body with 'next', a recovery with 'continue' or
    'abort'; a loop or try that is itself the last node leaves the same way
    instead of 'done'. A decision branch left empty cannot: it needs a step.
    """
    if label in (THEN, DONE):
        return last
    raise FlowError(f"A region of '{container}' ends in an empty decision branch "
                    f"('{label}'); add a step to it.")


def _slug(text):
    return ''.join(c if c.isalnum() else '_' for c in text.lower()).strip('_') or 'test'
