import json
import os
import shutil
import tempfile
import unittest

from robot.flow import FlowError, build_flow_suite, load_flow, render_robot
from robot.flow.schema import json_schema

try:
    import jsonschema
except ImportError:
    jsonschema = None


START = {'id': 'start', 'kind': 'start'}
END = {'id': 'end', 'kind': 'end'}


def flow(name, nodes, edges, **extra):
    data = {'flow': {'name': name}, 'nodes': nodes, 'edges': edges}
    data.update(extra)
    return data


def chain(*ids):
    return [[a, b] for a, b in zip(ids, ids[1:])]


def call(id, file, **args):
    node = {'id': id, 'kind': 'flow', 'file': file}
    if args:
        node['args'] = args
    return node


def kw(id, keyword, *args):
    return {'id': id, 'kind': 'keyword', 'keyword': keyword, 'args': list(args)}


POWER_ON = flow('Power On',
                [START, kw('on', 'Log', 'on at ${VOLTS} V'), END],
                chain('start', 'on', 'end'),
                variables={'VOLTS': 12}, imports={'libraries': ['../lib/bench.py']})


class SubflowTestCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.write('sub/power_on.flow.json', POWER_ON)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, rel, data):
        path = os.path.join(self.dir, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='UTF-8') as file:
            json.dump(data, file)
        return path

    def main(self, nodes, edges, **extra):
        return self.write('main.flow.json', flow('Main', nodes, edges, **extra))

    def keyword(self, suite, name):
        return next(kw for kw in suite.resource.keywords if kw.name == name)


class TestSubflowBuild(SubflowTestCase):

    def test_subflow_becomes_one_keyword_with_parameters(self):
        path = self.main([START, call('a', 'sub/power_on.flow.json', VOLTS='${V}'),
                          call('b', 'sub/power_on.flow.json'), END],
                         chain('start', 'a', 'b', 'end'), variables={'V': 13.5})
        suite = build_flow_suite(path)
        body = suite.tests[0].body
        self.assertEqual([item.name for item in body],
                         ['Flow Phase'] + ['Flow: Power On'] * 2)
        self.assertEqual(list(body[1].args), ['VOLTS=${V}'])
        self.assertEqual(list(body[2].args), [])
        subflows = [kw for kw in suite.resource.keywords if kw.name == 'Flow: Power On']
        self.assertEqual(len(subflows), 1, 'one keyword per sub-flow file')
        self.assertEqual(list(subflows[0].args), ['${VOLTS}=12'])

    def test_subflow_imports_are_added_relative_to_the_caller(self):
        path = self.main([START, call('a', 'sub/power_on.flow.json'), END],
                         chain('start', 'a', 'end'),
                         imports={'libraries': ['lib/bench.py']})
        names = [imp.name for imp in build_flow_suite(path).resource.imports]
        # sub/../lib/bench.py and lib/bench.py are the same file: imported once.
        self.assertEqual(names, ['robot.flow.keywords', 'lib/bench.py'])

    def test_nested_subflows_resolve_from_their_own_folder(self):
        self.write('sub/deep/inner.flow.json',
                   flow('Inner', [START, kw('x', 'No Operation'), END], chain('start', 'x', 'end')))
        self.write('sub/outer.flow.json',
                   flow('Outer', [START, call('in', 'deep/inner.flow.json'), END],
                        chain('start', 'in', 'end')))
        path = self.main([START, call('o', 'sub/outer.flow.json'), END], chain('start', 'o', 'end'))
        suite = build_flow_suite(path)
        self.assertEqual(self.keyword(suite, 'Flow: Outer').body[0].name, 'Flow: Inner')
        self.assertIn('Flow: Inner', render_robot(suite))

    def test_render_shows_the_subflow_keyword(self):
        path = self.main([START, call('a', 'sub/power_on.flow.json', VOLTS=9), END],
                         chain('start', 'a', 'end'))
        text = render_robot(build_flow_suite(path))
        self.assertIn('Flow: Power On    VOLTS=9', text)
        self.assertIn('[Arguments]    ${VOLTS}=12', text)


class TestSubflowErrors(SubflowTestCase):

    def assertBuildError(self, path, *fragments):
        with self.assertRaises(FlowError) as ctx:
            build_flow_suite(path)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_missing_file(self):
        path = self.main([START, call('a', 'sub/nope.flow.json'), END], chain('start', 'a', 'end'))
        self.assertBuildError(path, "Node 'a'", "'sub/nope.flow.json' not found")

    def test_unknown_parameter(self):
        path = self.main([START, call('a', 'sub/power_on.flow.json', VOLT=1), END],
                         chain('start', 'a', 'end'))
        self.assertBuildError(path, "no parameter(s) VOLT", 'parameters are: VOLTS')

    def test_cycle(self):
        self.write('sub/ping.flow.json', flow('Ping', [START, call('p', 'pong.flow.json'), END],
                                              chain('start', 'p', 'end')))
        self.write('sub/pong.flow.json', flow('Pong', [START, call('p', 'ping.flow.json'), END],
                                              chain('start', 'p', 'end')))
        path = self.main([START, call('a', 'sub/ping.flow.json'), END], chain('start', 'a', 'end'))
        self.assertBuildError(path, 'cycle', 'ping.flow.json -> pong.flow.json -> ping.flow.json')

    def test_subflow_with_phases(self):
        self.write('sub/phased.flow.json',
                   flow('Phased', [START, {'id': 't', 'kind': 'phase', 'role': 'test', 'name': 'T'},
                                   kw('x', 'No Operation'), END], chain('start', 't', 'x', 'end')))
        path = self.main([START, call('a', 'sub/phased.flow.json'), END], chain('start', 'a', 'end'))
        self.assertBuildError(path, "Sub-flow 'sub/phased.flow.json'", 'cannot have phases')

    def test_invalid_subflow_names_its_file(self):
        self.write('sub/broken.flow.json', flow('Broken', [START, END], []))
        path = self.main([START, call('a', 'sub/broken.flow.json'), END], chain('start', 'a', 'end'))
        self.assertBuildError(path, "Sub-flow 'sub/broken.flow.json'")

    def test_two_subflows_with_the_same_name(self):
        self.write('other/power_on.flow.json', POWER_ON)
        path = self.main([START, call('a', 'sub/power_on.flow.json'),
                          call('b', 'other/power_on.flow.json'), END],
                         chain('start', 'a', 'b', 'end'))
        self.assertBuildError(path, "same name 'Power On'")

    def test_assign_is_rejected(self):
        node = call('a', 'sub/power_on.flow.json')
        node['assign'] = '${X}'
        self.assertRaises(FlowError, load_flow,
                          flow('Main', [START, node, END], chain('start', 'a', 'end')))

    def test_args_must_be_an_object(self):
        node = call('a', 'sub/power_on.flow.json')
        node['args'] = ['12']
        self.assertRaises(FlowError, load_flow,
                          flow('Main', [START, node, END], chain('start', 'a', 'end')))

    def test_file_is_required(self):
        self.assertRaises(FlowError, load_flow,
                          flow('Main', [START, {'id': 'a', 'kind': 'flow'}, END],
                               chain('start', 'a', 'end')))


@unittest.skipIf(jsonschema is None, 'jsonschema is not installed')
class TestSubflowSchema(unittest.TestCase):

    def errors(self, node):
        data = flow('Main', [START, node, END], chain('start', node['id'], 'end'))
        return list(jsonschema.Draft7Validator(json_schema()).iter_errors(data))

    def test_valid_call(self):
        self.assertEqual(self.errors(call('a', 'sub/x.flow.json', VOLTS='${V}', N=3)), [])

    def test_missing_file_and_list_args(self):
        self.assertTrue(self.errors({'id': 'a', 'kind': 'flow'}))
        self.assertTrue(self.errors({'id': 'a', 'kind': 'flow', 'file': 'x', 'args': ['1']}))


if __name__ == '__main__':
    unittest.main()
