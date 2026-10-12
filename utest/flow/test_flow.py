import contextlib
import copy
import unittest

import threading

from robot.errors import DataError
from robot.flow import FlowError, build_suite, load_flow, render_robot, structure
from robot.flow import keywords
from robot.flow.graph import Decision, GateStep, KeywordStep, Loop, Parallel, Try
from robot.model import BodyItem


def flow(nodes, edges, **extra):
    data = {'flow': {'name': 'Test Flow'}, 'nodes': nodes, 'edges': edges}
    data.update(extra)
    return data


def kw(id, keyword='Log', *args):
    return {'id': id, 'kind': 'keyword', 'keyword': keyword, 'args': list(args)}


START = {'id': 'start', 'kind': 'start'}
END = {'id': 'end', 'kind': 'end'}


LOOP_FLOW = flow(
    [START,
     {'id': 'loop', 'kind': 'loop', 'max_loops': 3, 'every': '1s'},
     kw('work', 'Work'),
     kw('recover', 'Recover'),
     END],
    [['start', 'loop'],
     {'from': 'loop', 'to': 'work', 'label': 'body'},
     {'from': 'work', 'to': 'loop', 'label': 'next'},
     {'from': 'loop', 'to': 'recover', 'label': 'on_failure'},
     {'from': 'recover', 'to': 'loop', 'label': 'continue'},
     {'from': 'loop', 'to': 'end', 'label': 'done'}]
)

PHASED_FLOW = flow(
    [START,
     {'id': 'setup', 'kind': 'phase', 'role': 'setup'},
     {'id': 'gate', 'kind': 'gate', 'keyword': 'Ready', 'args': ['x'], 'timeout': '5s'},
     {'id': 'test', 'kind': 'phase', 'role': 'test', 'name': 'Cycle'},
     kw('work', 'Work', '${ARG}'),
     {'id': 'teardown', 'kind': 'phase', 'role': 'teardown'},
     kw('clean', 'Clean'),
     END],
    [['start', 'setup'], ['setup', 'gate'], ['gate', 'test'], ['test', 'work'],
     ['work', 'teardown'], ['teardown', 'clean'], ['clean', 'end']],
    imports={'libraries': ['Bench', ['Signals', '127.0.0.1:9000']],
             'resources': ['bench.resource']},
    variables={'ARG': 'value', 'N': 3}
)


PARALLEL_FLOW = flow(
    [START,
     {'id': 'ramp', 'kind': 'parallel', 'timeout': '10 min', 'label': 'Ramp and sweep'},
     kw('chamber', 'Set Chamber', '-40'),
     {'id': 'wait', 'kind': 'gate', 'keyword': 'Chamber At', 'args': ['-40'], 'timeout': '5 min'},
     kw('psu', 'Sweep PSU', '8', '16'),
     kw('recover', 'Recover'),
     kw('measure', 'Measure'),
     END],
    [['start', 'ramp'],
     {'from': 'ramp', 'to': 'chamber', 'label': 'branch'}, ['chamber', 'wait'],
     {'from': 'wait', 'to': 'ramp', 'label': 'join'},
     {'from': 'ramp', 'to': 'psu', 'label': 'branch'},
     {'from': 'psu', 'to': 'ramp', 'label': 'join'},
     {'from': 'ramp', 'to': 'recover', 'label': 'on_failure'},
     {'from': 'recover', 'to': 'ramp', 'label': 'continue'},
     {'from': 'ramp', 'to': 'measure', 'label': 'done'},
     ['measure', 'end']]
)


def build(data):
    return build_suite(structure(load_flow(data)))


class TestSchema(unittest.TestCase):

    def _error(self, data, expected):
        with self.assertRaises(FlowError) as cm:
            structure(load_flow(data))
        self.assertIn(expected, str(cm.exception))

    def test_flow_error_is_data_error(self):
        self.assertTrue(issubclass(FlowError, DataError))

    def test_flow_section_required(self):
        self._error({'nodes': [START], 'edges': []}, "'flow' section")

    def test_unsupported_version(self):
        data = flow([START, END], [['start', 'end']])
        data['flow']['version'] = 2
        self._error(data, 'Unsupported flow version 2')

    def test_unknown_kind(self):
        self._error(flow([START, {'id': 'x', 'kind': 'state'}, END], []),
                    "Node 'x': Unknown kind 'state'")

    def test_duplicate_id(self):
        self._error(flow([START, kw('a'), kw('a'), END], []), "Node 'a': Duplicate")

    def test_keyword_required(self):
        self._error(flow([START, {'id': 'a', 'kind': 'keyword'}, END], []),
                    "Node 'a': 'keyword' is required")

    def test_gate_needs_timeout_and_valid_on_timeout(self):
        gate = {'id': 'g', 'kind': 'gate', 'keyword': 'K'}
        self._error(flow([START, gate, END], []), "Node 'g': 'timeout' is required")
        gate.update(timeout='1s', on_timeout='skip')
        self._error(flow([START, gate, END], []), "'on_timeout' must be 'unknown' or 'fail'")

    def test_invalid_time_string(self):
        self._error(flow([START, {'id': 's', 'kind': 'sleep', 'duration': 'soon'}, END], []),
                    "Node 's': 'duration' must be a valid time string, got 'soon'")

    def test_bounds_and_times_may_be_variables(self):
        node = {'id': 'l', 'kind': 'loop', 'max_loops': '${CYCLES}',
                'max_seconds': ' ${MAX_TIME} ', 'every': '${EVERY}'}
        loop = load_flow(flow([START, node, END], [])).nodes['l']
        self.assertEqual((loop.max_loops, loop.max_seconds, loop.every),
                         ('${CYCLES}', '${MAX_TIME}', '${EVERY}'))
        gate = {'id': 'g', 'kind': 'gate', 'keyword': 'Ready', 'timeout': '${T}'}
        self.assertEqual(load_flow(flow([START, gate, END], [])).nodes['g'].timeout, '${T}')
        for bad in ('${A}${B}', 'x${A}', '${}', '2 hours or ${MORE}'):
            self._error(flow([START, dict(node, max_seconds=bad), END], []),
                        "'max_seconds' must be a valid time string")
        self._error(flow([START, dict(node, max_loops='3 ${X}'), END], []),
                    "'max_loops' must be a positive integer or a '${variable}'")

    def test_loop_must_be_bounded(self):
        self._error(flow([START, {'id': 'l', 'kind': 'loop'}, END], []),
                    "unbounded loops are not allowed")
        self._error(flow([START, {'id': 'l', 'kind': 'loop', 'max_loops': 0}, END], []),
                    "'max_loops' must be a positive integer")

    def test_retry_policy_shapes(self):
        def node(retry=None, timeout=None):
            item = kw('k', 'Read')
            if retry is not None:
                item['retry'] = retry
            if timeout is not None:
                item['timeout'] = timeout
            return load_flow(flow([START, item, END], [])).nodes['k']

        self.assertEqual((node().retry, node().timeout), (None, None))
        self.assertEqual(node(retry=3).retry, {'max_attempts': 3, 'interval': '1s',
                                               'backoff': 1, 'on_exhausted': 'fail'})
        full = node(retry={'max_attempts': '${TRIES}', 'interval': '${WAIT}', 'backoff': 2.5,
                           'on_exhausted': 'unknown'}, timeout='30s')
        self.assertEqual(full.retry, {'max_attempts': '${TRIES}', 'interval': '${WAIT}',
                                      'backoff': 2.5, 'on_exhausted': 'unknown'})
        self.assertEqual(full.timeout, '30s')
        self.assertEqual(node(timeout='${LIMIT}').timeout, '${LIMIT}')
        for bad, expected in [
                ({'interval': '1s'}, "'retry' needs 'max_attempts'"),
                (0, "'retry.max_attempts' must be a positive integer"),
                ('many', "'retry.max_attempts' must be a positive integer"),
                ({'max_attempts': 2, 'backoff': 0.5}, "'retry.backoff' must be a number of at least 1"),
                ({'max_attempts': 2, 'interval': 'soon'}, "'retry.interval' must be a valid time"),
                ({'max_attempts': 2, 'on_exhausted': 'skip'}, "'retry.on_exhausted' must be 'fail' or 'unknown'"),
                ({'max_attempts': 2, 'attempts': 3}, "'retry' has unknown key(s) attempts")]:
            with self.subTest(bad):
                self._error(flow([START, dict(kw('k', 'Read'), retry=bad), END], []),
                            "Node 'k': " + expected)
        self._error(flow([START, dict(kw('k', 'Read'), timeout='soon'), END], []),
                    "Node 'k': 'timeout' must be a valid time string")

    def test_negative_duration(self):
        gate = {'id': 'g', 'kind': 'gate', 'keyword': 'K', 'timeout': '-5s'}
        self._error(flow([START, gate, END], []),
                    "Node 'g': 'timeout' must not be negative, got '-5s'")

    def test_imports_must_be_lists(self):
        data = flow([START, END], [['start', 'end']], imports={'libraries': 'Bench'})
        self._error(data, "'imports.libraries' must be a list")

    def test_missing_file_is_a_flow_error(self):
        with self.assertRaises(FlowError) as cm:
            load_flow('no/such/file.flow.json')
        self.assertIn("Reading flow file", str(cm.exception))

    def test_edge_to_unknown_node(self):
        self._error(flow([START, END], [['start', 'nowhere']]),
                    "refers to unknown node 'nowhere'")

    def test_unknown_edge_label(self):
        self._error(flow([START, END], [{'from': 'start', 'to': 'end', 'label': 'when'}]),
                    "Unknown edge label 'when'")

    def test_args_and_variables_are_stringified(self):
        data = flow([START, kw('a', 'K', 1, 2.5, True, None), END],
                    [['start', 'a'], ['a', 'end']], variables={'N': 3, 'B': False})
        loaded = load_flow(data)
        self.assertEqual(loaded.nodes['a'].args, ['1', '2.5', 'True', ''])
        self.assertEqual(loaded.variables, {'N': '3', 'B': 'False'})


class TestStructure(unittest.TestCase):

    def _error(self, data, expected):
        with self.assertRaises(FlowError) as cm:
            structure(load_flow(data))
        self.assertIn(expected, str(cm.exception))

    def test_implicit_test_phase_named_after_flow(self):
        result = structure(load_flow(flow([START, kw('a'), END],
                                          [['start', 'a'], ['a', 'end']])))
        self.assertIsNone(result.setup)
        self.assertIsNone(result.teardown)
        self.assertEqual([t.name for t in result.tests], ['Test Flow'])
        self.assertIsInstance(result.tests[0].steps[0], KeywordStep)

    def test_phases(self):
        result = structure(load_flow(PHASED_FLOW))
        self.assertIsInstance(result.setup.steps[0], GateStep)
        self.assertEqual(result.tests[0].name, 'Cycle')
        self.assertEqual(result.teardown.steps[0].keyword, 'Clean')

    def test_setup_must_be_first_and_teardown_last(self):
        data = copy.deepcopy(PHASED_FLOW)
        data['nodes'][1]['role'] = 'teardown'
        data['nodes'][5]['role'] = 'setup'
        self._error(data, "Node 'setup': The teardown phase must be the last phase")

    def test_at_least_one_test_phase(self):
        self._error(flow([START, {'id': 'p', 'kind': 'phase', 'role': 'setup'}, kw('a'), END],
                         [['start', 'p'], ['p', 'a'], ['a', 'end']]),
                    'at least one test phase')

    def test_duplicate_test_names(self):
        self._error(flow([START, {'id': 'p1', 'kind': 'phase', 'role': 'test', 'name': 'T'},
                          kw('a'), {'id': 'p2', 'kind': 'phase', 'role': 'test', 'name': 'T'},
                          kw('b'), END],
                         [['start', 'p1'], ['p1', 'a'], ['a', 'p2'], ['p2', 'b'], ['b', 'end']]),
                    "Duplicate test phase name 'T'")

    def test_loop_with_recovery(self):
        result = structure(load_flow(LOOP_FLOW))
        loop = result.tests[0].steps[0]
        self.assertIsInstance(loop, Loop)
        self.assertEqual(loop.max_loops, 3)
        self.assertEqual([s.keyword for s in loop.body], ['Work'])
        self.assertEqual([s.keyword for s in loop.recovery], ['Recover'])
        self.assertEqual(loop.then, 'continue')

    def test_try_with_abort_to_end(self):
        data = flow([START, {'id': 't', 'kind': 'try'}, kw('a', 'A'), kw('r', 'R'),
                     kw('after', 'After'), END],
                    [['start', 't'],
                     {'from': 't', 'to': 'a', 'label': 'body'},
                     {'from': 'a', 'to': 't', 'label': 'next'},
                     {'from': 't', 'to': 'r', 'label': 'on_failure'},
                     {'from': 'r', 'to': 'end', 'label': 'abort'},
                     {'from': 't', 'to': 'after', 'label': 'done'},
                     ['after', 'end']])
        try_ = structure(load_flow(data)).tests[0].steps[0]
        self.assertIsInstance(try_, Try)
        self.assertEqual(try_.then, 'abort')

    def test_abort_to_done_target(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['edges'][4] = {'from': 'recover', 'to': 'end', 'label': 'abort'}
        self.assertEqual(structure(load_flow(data)).tests[0].steps[0].then, 'abort')

    def test_body_must_return_with_next(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['edges'][2] = ['work', 'loop']
        self._error(data, "must return to it with an edge labelled 'next'; "
                          "it ends at 'loop' via 'then'")

    def test_recovery_must_continue_or_abort(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['edges'][4] = ['recover', 'end']
        self._error(data, "The recovery of 'loop' must end with an edge labelled "
                          "'continue'")

    def test_next_outside_loop(self):
        self._error(flow([START, kw('a'), kw('b'), END],
                         [['start', 'a'], {'from': 'a', 'to': 'b', 'label': 'next'},
                          ['b', 'end']]),
                    "Edge labelled 'next' must lead to the enclosing loop, try or parallel node")

    def test_decision_rejoins(self):
        data = flow([START, {'id': 'd', 'kind': 'decision', 'condition': '$X'},
                     kw('y', 'Yes'), kw('n', 'No'), kw('j', 'Join'), END],
                    [['start', 'd'], {'from': 'd', 'to': 'y', 'label': 'yes'},
                     {'from': 'd', 'to': 'n', 'label': 'no'}, ['y', 'j'], ['n', 'j'],
                     ['j', 'end']])
        steps = structure(load_flow(data)).tests[0].steps
        self.assertIsInstance(steps[0], Decision)
        self.assertEqual(steps[0].yes[0].keyword, 'Yes')
        self.assertEqual(steps[0].no[0].keyword, 'No')
        self.assertEqual(steps[1].keyword, 'Join')

    def test_decision_with_empty_branch(self):
        data = flow([START, {'id': 'd', 'kind': 'decision', 'condition': '$X'},
                     kw('y', 'Yes'), END],
                    [['start', 'd'], {'from': 'd', 'to': 'y', 'label': 'yes'},
                     {'from': 'd', 'to': 'end', 'label': 'no'}, ['y', 'end']])
        decision = structure(load_flow(data)).tests[0].steps[0]
        self.assertEqual(len(decision.yes), 1)
        self.assertEqual(decision.no, [])

    def test_decision_branches_must_rejoin(self):
        data = flow([START, {'id': 'd', 'kind': 'decision', 'condition': '$X'},
                     kw('y'), kw('n'), END, {'id': 'end2', 'kind': 'end'}],
                    [['start', 'd'], {'from': 'd', 'to': 'y', 'label': 'yes'},
                     {'from': 'd', 'to': 'n', 'label': 'no'}, ['y', 'end'], ['n', 'end2']])
        self._error(data, "Node 'd': The 'yes' and 'no' branches never re-join")

    def test_decision_needs_yes_and_no(self):
        self._error(flow([START, {'id': 'd', 'kind': 'decision', 'condition': '1'}, END],
                         [['start', 'd'], {'from': 'd', 'to': 'end', 'label': 'yes'}]),
                    "Node 'd': A decision node must have exactly two outgoing edges")

    def test_unreachable_node(self):
        self._error(flow([START, kw('a'), kw('orphan'), END],
                         [['start', 'a'], ['a', 'end'], ['orphan', 'end']]),
                    'Unreachable node(s): orphan')

    def test_start_cannot_have_incoming_edges(self):
        self._error(flow([START, kw('a'), END],
                         [['start', 'a'], ['a', 'start']]),
                    "Node 'start': The start node cannot have incoming edges (from 'a')")

    def test_nested_loops(self):
        data = flow([START,
                     {'id': 'outer', 'kind': 'loop', 'max_loops': 2},
                     {'id': 'inner', 'kind': 'loop', 'max_loops': 3},
                     kw('w', 'W'), END],
                    [['start', 'outer'],
                     {'from': 'outer', 'to': 'inner', 'label': 'body'},
                     {'from': 'inner', 'to': 'w', 'label': 'body'},
                     {'from': 'w', 'to': 'inner', 'label': 'next'},
                     {'from': 'inner', 'to': 'outer', 'label': 'next'},
                     {'from': 'outer', 'to': 'end', 'label': 'done'}])
        outer = structure(load_flow(data)).tests[0].steps[0]
        self.assertIsInstance(outer, Loop)
        self.assertIsInstance(outer.body[0], Loop)
        self.assertEqual(outer.body[0].body[0].keyword, 'W')

    def test_exactly_one_start(self):
        self._error(flow([START, {'id': 's2', 'kind': 'start'}, END],
                         [['start', 'end'], ['s2', 'end']]),
                    'exactly one start node, found 2')

    def test_parallel_region(self):
        region, measure = structure(load_flow(PARALLEL_FLOW)).tests[0].steps
        self.assertIsInstance(region, Parallel)
        self.assertEqual([b.id for b in region.branches], ['chamber', 'psu'])
        self.assertEqual([s.id for s in region.branches[0].steps], ['chamber', 'wait'])
        self.assertIsInstance(region.branches[0].steps[1], GateStep)
        self.assertEqual([s.id for s in region.branches[1].steps], ['psu'])
        self.assertEqual((region.timeout, region.stop_others), ('10 min', True))
        self.assertEqual([s.id for s in region.recovery], ['recover'])
        self.assertEqual(region.then, 'continue')
        self.assertIsNone(region.body)
        self.assertEqual(measure.id, 'measure')

    def test_parallel_defaults_and_shape(self):
        data = copy.deepcopy(PARALLEL_FLOW)
        data['nodes'][1] = {'id': 'ramp', 'kind': 'parallel', 'stop_others': False}
        region = structure(load_flow(data)).tests[0].steps[0]
        self.assertEqual((region.timeout, region.stop_others), (None, False))
        data['nodes'][1]['stop_others'] = 'no'
        with self.assertRaises(FlowError) as cm:
            load_flow(data)
        self.assertIn("'stop_others' must be true or false", str(cm.exception))

    def test_parallel_errors(self):
        def edges(*changes):
            data = copy.deepcopy(PARALLEL_FLOW)
            for index, edge in changes:
                data['edges'][index] = edge
            return data

        # The psu branch leaves with 'then' instead of 'join'.
        self._error(edges((5, ['psu', 'ramp'])),
                    "Node 'ramp': The branch of 'ramp' starting at 'psu' must return to it "
                    "with an edge labelled 'join'; it ends at 'ramp' via 'then'")
        # A branch that runs into the node after the region.
        self._error(edges((5, ['psu', 'measure'])),
                    "The branch of 'ramp' starting at 'psu' must return to it with an edge "
                    "labelled 'join'; it ends at 'end' via 'then'")
        # One branch only.
        data = copy.deepcopy(PARALLEL_FLOW)
        del data['edges'][4:6]
        data['nodes'].remove(next(n for n in data['nodes'] if n['id'] == 'psu'))
        self._error(data, "A parallel node must have two or more outgoing edges labelled "
                          "'branch'")
        # 'branch' from a keyword, 'join' outside a region.
        self._error(flow([START, kw('a'), kw('b'), END],
                         [['start', 'a'], {'from': 'a', 'to': 'b', 'label': 'branch'},
                          ['b', 'end']]),
                    "Node 'a': Only a parallel node has edges labelled 'branch'")
        self._error(flow([START, kw('a'), kw('b'), END],
                         [['start', 'a'], {'from': 'a', 'to': 'b', 'label': 'join'},
                          ['b', 'end']]),
                    "Edge labelled 'join' must lead to the enclosing loop, try or parallel "
                    "node, not to 'b'")
        # A node in two branches.
        self._error(edges((4, {'from': 'ramp', 'to': 'chamber', 'label': 'branch'})),
                    "Node 'chamber': Is reached twice in the same sequence")

    def test_region_as_the_last_node_of_a_loop_body_leaves_with_next(self):
        data = copy.deepcopy(PARALLEL_FLOW)
        data['nodes'].insert(1, {'id': 'loop', 'kind': 'loop', 'max_loops': 2})
        data['edges'][0] = ['start', 'loop']
        data['edges'][8] = {'from': 'ramp', 'to': 'loop', 'label': 'next'}
        data['edges'] += [{'from': 'loop', 'to': 'ramp', 'label': 'body'},
                          {'from': 'loop', 'to': 'measure', 'label': 'done'}]
        loop = structure(load_flow(data)).tests[0].steps[0]
        self.assertIsInstance(loop.body[0], Parallel)
        self.assertEqual(loop.body[0].then, 'continue')

    def test_nested_parallel_is_rejected(self):
        data = flow([START, {'id': 'outer', 'kind': 'parallel'}, kw('a'),
                     {'id': 'inner', 'kind': 'parallel'}, kw('b'), kw('c'), END],
                    [['start', 'outer'],
                     {'from': 'outer', 'to': 'a', 'label': 'branch'},
                     {'from': 'a', 'to': 'outer', 'label': 'join'},
                     {'from': 'outer', 'to': 'inner', 'label': 'branch'},
                     {'from': 'inner', 'to': 'outer', 'label': 'join'},
                     {'from': 'inner', 'to': 'b', 'label': 'branch'},
                     {'from': 'b', 'to': 'inner', 'label': 'join'},
                     {'from': 'inner', 'to': 'c', 'label': 'branch'},
                     {'from': 'c', 'to': 'inner', 'label': 'join'},
                     {'from': 'outer', 'to': 'end', 'label': 'done'}])
        self._error(data, "Node 'inner': A parallel region cannot be inside a branch of "
                          "another parallel region ('outer')")

    def test_loop_inside_a_branch_leaves_with_join(self):
        data = flow([START, {'id': 'p', 'kind': 'parallel'},
                     {'id': 'loop', 'kind': 'loop', 'max_loops': 2}, kw('w', 'W'),
                     kw('other', 'Other'), END],
                    [['start', 'p'],
                     {'from': 'p', 'to': 'loop', 'label': 'branch'},
                     {'from': 'loop', 'to': 'w', 'label': 'body'},
                     {'from': 'w', 'to': 'loop', 'label': 'next'},
                     {'from': 'loop', 'to': 'p', 'label': 'join'},
                     {'from': 'p', 'to': 'other', 'label': 'branch'},
                     {'from': 'other', 'to': 'p', 'label': 'join'},
                     {'from': 'p', 'to': 'end', 'label': 'done'}])
        region = structure(load_flow(data)).tests[0].steps[0]
        self.assertIsInstance(region.branches[0].steps[0], Loop)
        self.assertEqual(region.branches[1].steps[0].keyword, 'Other')

    def test_action_needs_one_edge(self):
        self._error(flow([START, kw('a'), kw('b'), END],
                         [['start', 'a'], ['a', 'b'], ['a', 'end'], ['b', 'end']]),
                    "Node 'a': Has two outgoing edges labelled 'then'")


class TestBuilder(unittest.TestCase):

    def test_suite_from_phases(self):
        suite = build(PHASED_FLOW)
        self.assertEqual(suite.name, 'Test Flow')
        self.assertEqual(suite.setup.name, 'Flow Setup')
        self.assertEqual(suite.teardown.name, 'Flow Teardown')
        self.assertEqual([t.name for t in suite.tests], ['Cycle'])
        self.assertEqual([k.name for k in suite.resource.keywords],
                         ['Flow Setup', 'Flow Teardown'])
        imports = [(i.type, i.name, list(i.args)) for i in suite.resource.imports]
        self.assertEqual(imports, [('LIBRARY', 'robot.flow.keywords', []),
                                   ('LIBRARY', 'Bench', []),
                                   ('LIBRARY', 'Signals', ['127.0.0.1:9000']),
                                   ('RESOURCE', 'bench.resource', [])])
        variables = [(v.name, v.value) for v in suite.resource.variables]
        self.assertEqual(variables, [('${ARG}', ('value',)), ('${N}', ('3',))])

    def test_keyword_with_assign(self):
        node = {'id': 'v', 'kind': 'keyword', 'keyword': 'Read Version', 'assign': '${V}'}
        data = flow([START, node, END], [['start', 'v'], ['v', 'end']])
        phase, call = build(data).tests[0].body
        self.assertEqual((phase.name, list(phase.args)), ('Flow Phase', ['Test Flow']))
        self.assertEqual((call.name, list(call.assign)), ('Read Version', ['${V}']))
        node['assign'] = 42
        with self.assertRaises(FlowError):
            build(data)

    def test_gate_call(self):
        gate = build(PHASED_FLOW).resource.keywords[0].body[0]
        self.assertEqual(gate.name, 'Flow Gate')
        self.assertEqual(list(gate.args), ['5s', '2s', 'unknown', 'Ready', 'x'])

    def test_loop_with_recovery(self):
        test = build(LOOP_FLOW).tests[0]
        phase, start, loop = test.body
        self.assertEqual((phase.name, list(phase.args)), ('Flow Phase', ['Test Flow']))
        self.assertEqual((start.name, list(start.args)), ('Flow Loop', ['loop', '3', 'NONE']))
        self.assertEqual(list(start.assign), ['${flow_limit_loop}', '${flow_deadline_loop}'])
        self.assertEqual(loop.type, BodyItem.WHILE)
        # Bounded by iterations only: no clock in the condition, as in the plain shape.
        self.assertEqual((loop.condition, loop.limit, loop.on_limit),
                         ('True', '${flow_limit_loop}', 'pass'))
        try_, sleep = loop.body         # no marker keyword: the runner counts iterations
        self.assertEqual(try_.type, BodyItem.TRY_EXCEPT_ROOT)
        main, recovery = try_.body
        self.assertEqual(main.type, BodyItem.TRY)
        self.assertEqual(main.body[0].name, 'Work')
        self.assertEqual(recovery.type, BodyItem.EXCEPT)
        self.assertEqual(recovery.assign, '${flow_error}')
        self.assertEqual([k.name for k in recovery.body], ['Recover'])
        self.assertEqual((sleep.name, list(sleep.args)), ('Sleep', ['1s']))

    def test_abort_reraises(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['edges'][4] = {'from': 'recover', 'to': 'end', 'label': 'abort'}
        recovery = build(data).tests[0].body[2].body[0].body[1]
        self.assertEqual([(k.name, list(k.args)) for k in recovery.body],
                         [('Recover', []), ('Fail', ['${flow_error}'])])

    def test_deadline_loop(self):
        data = flow([START, {'id': 'my-loop', 'kind': 'loop', 'max_seconds': '2h'},
                     kw('w', 'W'), END],
                    [['start', 'my-loop'], {'from': 'my-loop', 'to': 'w', 'label': 'body'},
                     {'from': 'w', 'to': 'my-loop', 'label': 'next'},
                     {'from': 'my-loop', 'to': 'end', 'label': 'done'}])
        _, start, loop = build(data).tests[0].body
        self.assertEqual((start.name, list(start.args)), ('Flow Loop', ['my-loop', 'NONE', '2h']))
        self.assertEqual(list(start.assign),
                         ['${flow_limit_my_loop}', '${flow_deadline_my_loop}'])
        self.assertEqual(loop.condition, 'robot.flow.control.clock() < $flow_deadline_my_loop')
        self.assertEqual((loop.limit, loop.on_limit), ('${flow_limit_my_loop}', 'pass'))

    def test_variable_bounds_reach_the_loop(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['nodes'][1].update(max_loops='${CYCLES}', max_seconds='${MAX_TIME}', every='${EVERY}')
        _, start, loop = build(data).tests[0].body
        self.assertEqual(list(start.args), ['loop', '${CYCLES}', '${MAX_TIME}'])
        self.assertEqual(list(loop.body[-1].args), ['${EVERY}'])
        # Without a checkpoint the loop is a plain WHILE: the bounds are resolved in it.
        data['flow']['checkpoint'] = False
        deadline, loop = build(data).tests[0].body
        self.assertEqual(list(deadline.args),
                         ['robot.flow.control.clock() + robot.utils.timestr_to_secs($MAX_TIME)'])
        self.assertEqual((loop.limit, loop.on_limit), ('${CYCLES}', 'pass'))

    def test_loop_variables_are_passed_by_name(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['nodes'][2]['assign'] = '${count}'
        start = build(data).tests[0].body[1]
        self.assertEqual(list(start.args), ['loop', '3', 'NONE', '\\${count}'])

    def test_without_checkpoint_nothing_is_added(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['flow']['checkpoint'] = False
        loop, = build(data).tests[0].body
        self.assertEqual((loop.condition, loop.limit, loop.on_limit), ('True', '3', 'pass'))
        self.assertEqual([item.type for item in loop.body],
                         [BodyItem.TRY_EXCEPT_ROOT, BodyItem.KEYWORD])

    def test_checkpoint_settings_become_variables(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['flow'].update(checkpoint='state/plan.json', checkpoint_every=100)
        variables = {v.name: v.value for v in build(data).resource.variables}
        self.assertEqual(variables, {'${FLOW_CHECKPOINT}': ('state/plan.json',),
                                     '${FLOW_CHECKPOINT_EVERY}': ('100',)})
        for bad in ({'checkpoint': 42}, {'checkpoint': ''}, {'checkpoint_every': 0},
                    {'checkpoint_every': '10'}):
            wrong = copy.deepcopy(LOOP_FLOW)
            wrong['flow'].update(bad)
            with self.assertRaises(FlowError):
                build(wrong)

    def test_colliding_loop_ids_get_distinct_deadlines(self):
        data = flow([START,
                     {'id': 'a-b', 'kind': 'loop', 'max_seconds': '1h'},
                     {'id': 'a_b', 'kind': 'loop', 'max_seconds': '1min'},
                     kw('w', 'W'), END],
                    [['start', 'a-b'],
                     {'from': 'a-b', 'to': 'a_b', 'label': 'body'},
                     {'from': 'a_b', 'to': 'w', 'label': 'body'},
                     {'from': 'w', 'to': 'a_b', 'label': 'next'},
                     {'from': 'a_b', 'to': 'a-b', 'label': 'next'},
                     {'from': 'a-b', 'to': 'end', 'label': 'done'}])
        # The outer loop is checkpointed, the nested one is a plain loop.
        _, outer_start, outer = build(data).tests[0].body
        inner_eval, inner = outer.body
        self.assertEqual(list(outer_start.assign),
                         ['${flow_limit_a_b}', '${flow_deadline_a_b}'])
        self.assertEqual(list(inner_eval.assign), ['${flow_deadline_a_b_2}'])
        self.assertEqual(list(inner_eval.args), ['robot.flow.control.clock() + 60.0'])
        self.assertEqual(outer.condition, 'robot.flow.control.clock() < $flow_deadline_a_b')
        self.assertEqual(inner.condition, 'robot.flow.control.clock() < ${flow_deadline_a_b_2}')

    def test_retry_and_timeout_wrap_the_keyword(self):
        def call(**policy):
            node = dict(kw('k', 'Read Level', 'ch1'), assign='${LEVEL}', **policy)
            return build(flow([START, node, END], [['start', 'k'], ['k', 'end']])).tests[0].body[1]

        plain = call()
        self.assertEqual((plain.name, list(plain.args)), ('Read Level', ['ch1']))
        retried = call(retry={'max_attempts': 5, 'interval': '2s', 'backoff': 2,
                              'on_exhausted': 'unknown'})
        self.assertEqual(retried.name, 'Flow Retry')
        self.assertEqual(list(retried.args), ['5', '2s', '2', 'unknown', 'NONE', 'Read Level', 'ch1'])
        self.assertEqual(list(retried.assign), ['${LEVEL}'])
        limited = call(timeout='30s')
        self.assertEqual(list(limited.args), ['1', '0s', '1', 'fail', '30s', 'Read Level', 'ch1'])
        both = call(retry='${TRIES}', timeout='${LIMIT}')
        self.assertEqual(list(both.args), ['${TRIES}', '1s', '1', 'fail', '${LIMIT}', 'Read Level', 'ch1'])

    def test_gate_with_assign(self):
        gate = {'id': 'g', 'kind': 'gate', 'keyword': 'Read Level', 'timeout': '1s',
                'assign': '${LEVEL}'}
        data = flow([START, gate, END], [['start', 'g'], ['g', 'end']])
        call = build(data).tests[0].body[1]
        self.assertEqual((call.name, list(call.assign)), ('Flow Gate', ['${LEVEL}']))

    def test_parser_applies_inherited_defaults(self):
        import json
        import os
        import tempfile
        from robot.flow import FlowParser
        from robot.running.builder.settings import TestDefaults
        data = flow([START, kw('a'), END], [['start', 'a'], ['a', 'end']])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'x.flow.json')
            with open(path, 'w', encoding='UTF-8') as file:
                json.dump(data, file)
            defaults = TestDefaults(tags=['bench'], timeout='2h',
                                    setup={'name': 'Prepare'})
            suite = FlowParser().parse(path, defaults)
        test = suite.tests[0]
        self.assertEqual(list(test.tags), ['bench'])
        self.assertEqual(test.timeout, '2h')
        self.assertEqual(test.setup.name, 'Prepare')

    def test_decision_and_empty_branch(self):
        data = flow([START, {'id': 'd', 'kind': 'decision', 'condition': "$MODE == 'fast'"},
                     kw('y', 'Yes'), END],
                    [['start', 'd'], {'from': 'd', 'to': 'y', 'label': 'yes'},
                     {'from': 'd', 'to': 'end', 'label': 'no'}, ['y', 'end']])
        if_ = build(data).tests[0].body[1]
        self.assertEqual(if_.type, BodyItem.IF_ELSE_ROOT)
        yes, no = if_.body
        self.assertEqual((yes.type, yes.condition), (BodyItem.IF, "$MODE == 'fast'"))
        self.assertEqual(yes.body[0].name, 'Yes')
        self.assertEqual(no.type, BodyItem.ELSE)
        self.assertEqual(no.body[0].name, 'No Operation')

    def test_parallel_region_builds_threads_and_branch_keywords(self):
        suite = build(PARALLEL_FLOW)
        try_ = suite.tests[0].body[1]
        self.assertEqual(try_.type, BodyItem.TRY_EXCEPT_ROOT)
        start, chamber, psu, join = try_.body[0].body
        self.assertEqual(start.name, 'Flow Parallel Start')
        self.assertEqual(list(start.args), ['ramp', '10 min', 'True', 'chamber', 'psu'])
        self.assertEqual(list(start.assign), ['${flow_thread_ramp_chamber}', '${flow_thread_ramp_psu}'])
        self.assertEqual((chamber.type, chamber.name, chamber.daemon),
                         (BodyItem.THREAD, '${flow_thread_ramp_chamber}', True))
        self.assertEqual(psu.name, '${flow_thread_ramp_psu}')
        call = chamber.body[0]
        self.assertEqual((call.name, list(call.args)),
                         ('Flow Branch', ['${flow_thread_ramp_chamber}', 'Flow Branch: ramp/chamber']))
        self.assertEqual((join.name, list(join.args)), ('Flow Join', ['ramp']))
        keywords_ = {k.name: k for k in suite.resource.keywords}
        self.assertEqual([s.name for s in keywords_['Flow Branch: ramp/chamber'].body],
                         ['Set Chamber', 'Flow Gate'])
        self.assertEqual([s.name for s in keywords_['Flow Branch: ramp/psu'].body], ['Sweep PSU'])
        self.assertEqual(try_.body[1].body[0].name, 'Recover')
        # The checkpoint collects no variable a branch assigns: it stays in the thread.
        data = copy.deepcopy(PARALLEL_FLOW)
        data['nodes'][4]['assign'] = '${I}'
        data['nodes'].insert(1, {'id': 'phase', 'kind': 'phase', 'role': 'test', 'name': 'T'})
        data['nodes'].insert(2, {'id': 'loop', 'kind': 'loop', 'max_loops': 2})
        data['edges'][0] = ['start', 'phase']
        data['edges'][8] = {'from': 'ramp', 'to': 'loop', 'label': 'next'}
        data['edges'] += [['phase', 'loop'],
                          {'from': 'loop', 'to': 'ramp', 'label': 'body'},
                          {'from': 'loop', 'to': 'measure', 'label': 'done'}]
        loop_start = build(data).tests[0].body[1]
        self.assertEqual(loop_start.name, 'Flow Loop')
        self.assertEqual(list(loop_start.args), ['loop', '2', 'NONE'])

    def test_render_parallel(self):
        data = copy.deepcopy(PARALLEL_FLOW)
        del data['edges'][6:8]
        data['nodes'].remove(next(n for n in data['nodes'] if n['id'] == 'recover'))
        text = render_robot(build(data))
        self.assertIn('''\
    ${flow_thread_ramp_chamber}    ${flow_thread_ramp_psu}=    Flow Parallel Start    ramp    10 min    True    chamber    psu
    THREAD    ${flow_thread_ramp_chamber}    ${True}
        Flow Branch    ${flow_thread_ramp_chamber}    Flow Branch: ramp/chamber
    END
    THREAD    ${flow_thread_ramp_psu}    ${True}
        Flow Branch    ${flow_thread_ramp_psu}    Flow Branch: ramp/psu
    END
    Flow Join    ramp
    Measure
''', text)
        self.assertIn('''\
Flow Branch: ramp/chamber
    Set Chamber    -40
    Flow Gate    5 min    2s    unknown    Chamber At    -40
''', text)

    def test_render(self):
        text = render_robot(build(LOOP_FLOW))
        expected = '''\
*** Settings ***
Library    robot.flow.keywords

*** Test Cases ***
Test Flow
    Flow Phase    Test Flow
    ${flow_limit_loop}    ${flow_deadline_loop}=    Flow Loop    loop    3    NONE
    WHILE    True    limit=${flow_limit_loop}    on_limit=pass
        TRY
            Work
        EXCEPT    AS    ${flow_error}
            Recover
        END
        Sleep    1s
    END
'''
        self.assertEqual(text, expected)


class FakeBuiltIn:
    results = []

    def run_keyword_and_ignore_error(self, name, *args):
        # The last scripted result sticks, however many attempts the gate makes.
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class TestFlowGate(unittest.TestCase):

    def setUp(self):
        self._builtin = keywords.BuiltIn
        keywords.BuiltIn = FakeBuiltIn
        # The gate sleeps on the flow clock; here it does not sleep at all.
        keywords._CONTROL.sleep = lambda seconds: None

    def tearDown(self):
        keywords.BuiltIn = self._builtin
        del keywords._CONTROL.sleep

    def test_passes_after_retries(self):
        FakeBuiltIn.results = [('FAIL', 'not yet'), ('FAIL', 'not yet'), ('PASS', 42)]
        self.assertEqual(keywords.flow_gate('10s', '0.01s', 'unknown', 'K'), 42)

    def test_timeout_is_unknown_by_default(self):
        FakeBuiltIn.results = [('FAIL', 'value is 0')] * 1000
        with self.assertRaises(keywords.UnknownAssertionError) as cm:
            keywords.flow_gate('0.01s', '0.001s', 'unknown', 'K', 'a')
        message = str(cm.exception)
        self.assertTrue(message.startswith("Gate 'K' did not pass within 10 milliseconds ("))
        self.assertIn('waited', message)
        self.assertTrue(message.endswith('Last error: value is 0'))

    def test_timeout_can_fail(self):
        FakeBuiltIn.results = [('FAIL', 'nope')] * 1000
        with self.assertRaises(AssertionError):
            keywords.flow_gate('0.01s', '0.001s', 'fail', 'K')

    def test_invalid_on_timeout(self):
        with self.assertRaises(ValueError):
            keywords.flow_gate('1s', '1s', 'skip', 'K')


class FakeContext:
    timeout_occurred = False

    @contextlib.contextmanager
    def keyword_timeout(self, timeout):
        timeout.start()         # as the real context does when it adds one
        yield


class RetryBuiltIn:
    """``run_keyword`` scripted per attempt: a value to return, or an exception to raise."""
    results = []
    calls = []

    def run_keyword(self, name, *args):
        self.calls.append((name, args))
        outcome = self.results.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def failed(message, **flags):
    from robot.errors import ExecutionFailed
    return ExecutionFailed(message, **flags)


class TestFlowRetry(unittest.TestCase):

    def setUp(self):
        self._builtin, self._contexts = keywords.BuiltIn, keywords.EXECUTION_CONTEXTS
        keywords.BuiltIn = RetryBuiltIn
        keywords.EXECUTION_CONTEXTS = type('Contexts', (), {'current': FakeContext()})
        RetryBuiltIn.calls = []
        self.sleeps = []
        keywords._CONTROL.sleep = self.sleeps.append

    def tearDown(self):
        keywords.BuiltIn, keywords.EXECUTION_CONTEXTS = self._builtin, self._contexts
        del keywords._CONTROL.sleep

    def retry(self, attempts='3', interval='1s', backoff='1', on_exhausted='fail', timeout='NONE'):
        return keywords.flow_retry(attempts, interval, backoff, on_exhausted, timeout, 'K', 'a')

    def test_passes_after_failures_with_backoff(self):
        RetryBuiltIn.results = [failed('no'), failed('still no'), 42]
        self.assertEqual(self.retry(attempts='5', interval='2s', backoff='2'), 42)
        self.assertEqual(RetryBuiltIn.calls, [('K', ('a',))] * 3)
        self.assertEqual(self.sleeps, [2.0, 4.0])

    def test_exhausted_is_fail_or_unknown_with_the_last_error(self):
        RetryBuiltIn.results = [failed('1'), failed('2'), failed('3')]
        with self.assertRaises(AssertionError) as cm:
            self.retry()
        self.assertEqual(str(cm.exception), "'K' failed after 3 attempts. Last error: 3")
        self.assertEqual(self.sleeps, [1.0, 1.0])
        RetryBuiltIn.results = [failed('x'), failed('y')]
        with self.assertRaises(keywords.UnknownAssertionError) as cm:
            self.retry(attempts='2', on_exhausted='unknown')
        self.assertEqual(str(cm.exception), "'K' failed after 2 attempts. Last error: y")

    def test_single_attempt_raises_the_keywords_own_failure(self):
        error = failed('as it was')
        RetryBuiltIn.results = [error]
        with self.assertRaises(keywords.ExecutionFailed) as cm:
            self.retry(attempts='1')
        self.assertIs(cm.exception, error)

    def test_failures_that_must_not_be_retried(self):
        for flags in ({'skip': True}, {'test_timeout': True}, {'syntax': True}, {'exit': True}):
            RetryBuiltIn.results = [failed('stop', **flags), 'never']
            with self.subTest(flags), self.assertRaises(keywords.ExecutionFailed):
                self.retry()
            self.assertEqual(RetryBuiltIn.results, ['never'])
        # An enclosing keyword timeout is not this step's own: no retry either.
        RetryBuiltIn.results = [failed('outer', keyword_timeout=True), 'never']
        with self.assertRaises(keywords.ExecutionFailed):
            self.retry(timeout='1h')

    def test_invalid_policy_is_a_data_error(self):
        RetryBuiltIn.results = []
        for kwargs in ({'attempts': 'many'}, {'attempts': '0'}, {'interval': 'soon'},
                       {'backoff': 'x'}, {'on_exhausted': 'skip'}, {'timeout': 'later'}):
            with self.subTest(kwargs), self.assertRaises(DataError):
                self.retry(**kwargs)
        self.assertEqual(RetryBuiltIn.calls, [])


class FakeWorker:
    """A branch's thread: alive until its verdict is recorded, or until it is
    asked to stop (unless it is stuck); a stopped branch records that."""

    def __init__(self, name, stop_event, stuck=False):
        self.name = name
        self.stop_event = stop_event
        self.stuck = stuck
        self.done = False

    def is_alive(self):
        if self.done:
            return False
        if self.stop_event.is_set() and not self.stuck:
            keywords._BY_THREAD[self.name].record(self.name, keywords.STOPPED, 'stopped')
            return False
        return True

    def join(self, timeout=None):
        pass


class JoinContext:
    in_teardown = False

    def __init__(self):
        self.active_threads = {}

    def thread_stop_requested(self):
        return False


class TestParallelRegion(unittest.TestCase):

    def setUp(self):
        self._builtin, self._contexts = keywords.BuiltIn, keywords.EXECUTION_CONTEXTS
        self._slice, self._grace = keywords.JOIN_SLICE_S, keywords.STOP_GRACE_S
        keywords.BuiltIn = RetryBuiltIn
        self.context = JoinContext()
        keywords.EXECUTION_CONTEXTS = type('Contexts', (), {'current': self.context})
        keywords.JOIN_SLICE_S, keywords.STOP_GRACE_S = 0.001, 0.01
        keywords._REGIONS.clear()
        keywords._BY_THREAD.clear()
        keywords._RUNS.clear()
        RetryBuiltIn.calls = []

    def tearDown(self):
        keywords.BuiltIn, keywords.EXECUTION_CONTEXTS = self._builtin, self._contexts
        keywords.JOIN_SLICE_S, keywords.STOP_GRACE_S = self._slice, self._grace

    def start(self, timeout='NONE', stop_others='True', stuck=()):
        names = keywords.flow_parallel_start('r', timeout, stop_others, 'a', 'b')
        self.workers = {}
        for name in names:
            event = threading.Event()
            self.workers[name] = FakeWorker(name, event, stuck=name in stuck)
            self.context.active_threads[name] = {'worker': self.workers[name], 'stop_event': event}
        return names

    def end(self, name, status, message=''):
        keywords._BY_THREAD[name].record(name, status, message)
        self.workers[name].done = True

    def test_start_names_the_threads_uniquely_per_run(self):
        self.assertEqual(self.start(), ['flow.r.a', 'flow.r.b'])
        self.assertEqual(keywords.flow_parallel_start('r', 'NONE', 'True', 'a', 'b'),
                         ['flow.r.a.2', 'flow.r.b.2'])
        with self.assertRaises(DataError):
            keywords.flow_parallel_start('r', 'NONE', 'True', 'a')
        with self.assertRaises(DataError):
            keywords.flow_parallel_start('r', 'soon', 'True', 'a', 'b')

    def test_join_passes_when_every_branch_passed(self):
        a, b = self.start()
        self.end(a, 'PASS')
        self.end(b, 'PASS')
        self.assertIsNone(keywords.flow_join('r'))

    def test_failed_branch_stops_the_others_and_fails_the_join(self):
        a, b = self.start()
        self.end(b, 'FAIL', 'PSU tripped.')
        with self.assertRaises(AssertionError) as cm:
            keywords.flow_join('r')
        self.assertEqual(str(cm.exception), "Branch 'b' of 'r' failed: PSU tripped.")
        self.assertTrue(self.workers[a].stop_event.is_set(), 'the other branch was stopped')

    def test_without_stop_others_the_join_waits_for_the_rest(self):
        a, b = self.start(stop_others='False')
        self.end(b, 'FAIL', 'PSU tripped.')
        threading.Timer(0.02, lambda: self.end(a, 'UNKNOWN', 'Chamber never ready.')).start()
        with self.assertRaises(keywords.UnknownAssertionError) as cm:
            keywords.flow_join('r')
        self.assertEqual(str(cm.exception), "Branch 'a' of 'r' is unknown: Chamber never ready.\n"
                                            "Branch 'b' of 'r' failed: PSU tripped.")
        self.assertFalse(self.workers[a].stop_event.is_set())

    def test_timeout_names_the_running_branches_and_abandons_a_stuck_one(self):
        a, b = self.start(timeout='0.01s', stuck=('flow.r.a',))
        self.end(b, 'PASS')
        with self.assertRaises(AssertionError) as cm:
            keywords.flow_join('r')
        self.assertEqual(str(cm.exception),
                         "Region 'r' did not join within 10 milliseconds; still running: a.\n"
                         "Branch 'a' of 'r' did not stop within 10 milliseconds and was abandoned.")
        self.assertNotIn(a, self.context.active_threads)

    def test_branch_without_a_verdict_is_unknown(self):
        a, b = self.start()
        self.end(a, 'PASS')
        self.workers[b].done = True
        with self.assertRaises(keywords.UnknownAssertionError) as cm:
            keywords.flow_join('r')
        self.assertEqual(str(cm.exception), "Branch 'b' of 'r' ended without a verdict.")

    def test_join_needs_a_started_region(self):
        with self.assertRaises(DataError):
            keywords.flow_join('nowhere')

    def test_flow_branch_records_and_raises_a_collected_failure(self):
        a, b = self.start()
        RetryBuiltIn.results = [None]
        keywords.flow_branch(a, 'Flow Branch: r/a')
        self.assertEqual(RetryBuiltIn.calls, [('Flow Branch: r/a', ())])
        self.assertEqual(keywords._BY_THREAD[a].result(a), ('PASS', ''))
        RetryBuiltIn.results = [failed('PSU tripped.')]
        with self.assertRaises(keywords.ExecutionFailed) as cm:
            keywords.flow_branch(b, 'Flow Branch: r/b')
        self.assertEqual((cm.exception.message, cm.exception.status, cm.exception.collected),
                         ('PSU tripped.', 'FAIL', True))
        self.assertEqual(keywords._BY_THREAD[b].result(b), ('FAIL', 'PSU tripped.'))
        RetryBuiltIn.results = [failed('not ready', unknown=True)]
        with self.assertRaises(keywords.ExecutionFailed) as cm:
            keywords.flow_branch(b, 'Flow Branch: r/b')
        self.assertEqual(cm.exception.status, 'UNKNOWN')
        RetryBuiltIn.results = [failed(keywords.THREAD_STOP_MESSAGE)]
        with self.assertRaises(keywords.ExecutionFailed) as cm:
            keywords.flow_branch(b, 'Flow Branch: r/b')
        self.assertEqual(cm.exception.message, "Branch 'b' of 'r' was stopped.")
        self.assertEqual(keywords._BY_THREAD[b].result(b)[0], keywords.STOPPED)
        # A fatal error of the run is not a branch's verdict.
        RetryBuiltIn.results = [failed('exit', exit=True)]
        with self.assertRaises(keywords.ExecutionFailed) as cm:
            keywords.flow_branch(b, 'Flow Branch: r/b')
        self.assertFalse(getattr(cm.exception, 'collected', False))


if __name__ == '__main__':
    unittest.main()
