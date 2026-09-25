import copy
import unittest

from robot.errors import DataError
from robot.flow import FlowError, build_suite, load_flow, render_robot, structure
from robot.flow import keywords
from robot.flow.graph import Decision, GateStep, KeywordStep, Loop, Try
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
    imports={'libraries': ['Bench', ['Signals', '127.0.0.1:50210']],
             'resources': ['bench.resource']},
    variables={'ARG': 'value', 'N': 3}
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

    def test_loop_must_be_bounded(self):
        self._error(flow([START, {'id': 'l', 'kind': 'loop'}, END], []),
                    "unbounded loops are not allowed")
        self._error(flow([START, {'id': 'l', 'kind': 'loop', 'max_loops': 0}, END], []),
                    "'max_loops' must be a positive integer")

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
                    "Edge labelled 'next' must lead to the enclosing loop or try node")

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
                                   ('LIBRARY', 'Signals', ['127.0.0.1:50210']),
                                   ('RESOURCE', 'bench.resource', [])])
        variables = [(v.name, v.value) for v in suite.resource.variables]
        self.assertEqual(variables, [('${ARG}', ('value',)), ('${N}', ('3',))])

    def test_keyword_with_assign(self):
        node = {'id': 'v', 'kind': 'keyword', 'keyword': 'Read Version', 'assign': '${V}'}
        data = flow([START, node, END], [['start', 'v'], ['v', 'end']])
        call = build(data).tests[0].body[0]
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
        loop = test.body[0]
        self.assertEqual(loop.type, BodyItem.WHILE)
        self.assertEqual((loop.condition, loop.limit, loop.on_limit), ('True', '3', 'pass'))
        try_, sleep = loop.body
        self.assertEqual(try_.type, BodyItem.TRY_EXCEPT_ROOT)
        main, recovery = try_.body
        self.assertEqual(main.type, BodyItem.TRY)
        self.assertEqual(main.body[0].name, 'Work')
        self.assertEqual(recovery.type, BodyItem.EXCEPT)
        self.assertEqual(recovery.variable, '${flow_error}')
        self.assertEqual([k.name for k in recovery.body], ['Recover'])
        self.assertEqual((sleep.name, list(sleep.args)), ('Sleep', ['1s']))

    def test_abort_reraises(self):
        data = copy.deepcopy(LOOP_FLOW)
        data['edges'][4] = {'from': 'recover', 'to': 'end', 'label': 'abort'}
        recovery = build(data).tests[0].body[0].body[0].body[1]
        self.assertEqual([(k.name, list(k.args)) for k in recovery.body],
                         [('Recover', []), ('Fail', ['${flow_error}'])])

    def test_deadline_loop(self):
        data = flow([START, {'id': 'my-loop', 'kind': 'loop', 'max_seconds': '2h'},
                     kw('w', 'W'), END],
                    [['start', 'my-loop'], {'from': 'my-loop', 'to': 'w', 'label': 'body'},
                     {'from': 'w', 'to': 'my-loop', 'label': 'next'},
                     {'from': 'my-loop', 'to': 'end', 'label': 'done'}])
        evaluate, loop = build(data).tests[0].body
        self.assertEqual(evaluate.name, 'Evaluate')
        self.assertEqual(list(evaluate.assign), ['${flow_deadline_my_loop}'])
        self.assertEqual(list(evaluate.args), ['time.time() + 7200.0'])
        self.assertEqual(loop.condition, 'time.time() < ${flow_deadline_my_loop}')
        self.assertEqual((loop.limit, loop.on_limit), ('NONE', None))

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
        outer_eval, outer = build(data).tests[0].body
        inner_eval, inner = outer.body
        self.assertEqual(list(outer_eval.assign), ['${flow_deadline_a_b}'])
        self.assertEqual(list(inner_eval.assign), ['${flow_deadline_a_b_2}'])
        self.assertEqual(outer.condition, 'time.time() < ${flow_deadline_a_b}')
        self.assertEqual(inner.condition, 'time.time() < ${flow_deadline_a_b_2}')

    def test_gate_with_assign(self):
        gate = {'id': 'g', 'kind': 'gate', 'keyword': 'Read Level', 'timeout': '1s',
                'assign': '${LEVEL}'}
        data = flow([START, gate, END], [['start', 'g'], ['g', 'end']])
        call = build(data).tests[0].body[0]
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
        if_ = build(data).tests[0].body[0]
        self.assertEqual(if_.type, BodyItem.IF_ELSE_ROOT)
        yes, no = if_.body
        self.assertEqual((yes.type, yes.condition), (BodyItem.IF, "$MODE == 'fast'"))
        self.assertEqual(yes.body[0].name, 'Yes')
        self.assertEqual(no.type, BodyItem.ELSE)
        self.assertEqual(no.body[0].name, 'No Operation')

    def test_render(self):
        text = render_robot(build(LOOP_FLOW))
        expected = '''\
*** Settings ***
Library    robot.flow.keywords

*** Test Cases ***
Test Flow
    WHILE    True    limit=3    on_limit=pass
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
        self._sleep = keywords.time.sleep
        keywords.time.sleep = lambda seconds: None

    def tearDown(self):
        keywords.BuiltIn = self._builtin
        keywords.time.sleep = self._sleep

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


if __name__ == '__main__':
    unittest.main()
