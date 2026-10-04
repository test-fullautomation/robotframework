import copy
import glob
import json
import os
import tempfile
import unittest

from robot.flow import FlowError, load_flow
from robot.flow.__main__ import main, schema_text
from robot.flow.schema import EDGE_LABELS, NODE_KINDS, json_schema

try:
    import jsonschema
except ImportError:
    jsonschema = None


FLOW_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'src', 'robot', 'flow')
SHIPPED = os.path.join(FLOW_DIR, 'flow.schema.json')
TESTDATA = os.path.join(os.path.dirname(__file__), '..', '..', 'atest', 'testdata', 'flow')

VALID = {
    'flow': {'name': 'Lab Cycle', 'version': 1},
    'imports': {'libraries': ['checks.py', ['Remote', '127.0.0.1:8270']]},
    'variables': {'MODE': 'EMC', 'VOLTS': 13.5},
    'nodes': [
        {'id': 'start', 'kind': 'start'},
        {'id': 'setup', 'kind': 'phase', 'role': 'setup'},
        {'id': 'ready', 'kind': 'gate', 'keyword': 'Bench Ready', 'timeout': '30s',
         'interval': 1, 'on_timeout': 'fail'},
        {'id': 'test', 'kind': 'phase', 'role': 'test', 'name': 'Cycles'},
        {'id': 'loop', 'kind': 'loop', 'max_loops': 3, 'every': '1s'},
        {'id': 'measure', 'kind': 'keyword', 'keyword': 'Measure', 'assign': '${I}'},
        {'id': 'emc', 'kind': 'decision', 'condition': "$MODE == 'EMC'"},
        {'id': 'burst', 'kind': 'keyword', 'keyword': 'Burst', 'args': ['${I}', 2, True]},
        {'id': 'pause', 'kind': 'sleep', 'duration': '1s'},
        {'id': 'end', 'kind': 'end'},
    ],
    'edges': [
        ['start', 'setup'], ['setup', 'ready'], ['ready', 'test'], ['test', 'loop'],
        {'from': 'loop', 'to': 'measure', 'label': 'body'}, ['measure', 'emc'],
        {'from': 'emc', 'to': 'burst', 'label': 'yes'},
        {'from': 'emc', 'to': 'pause', 'label': 'no'}, ['burst', 'pause'],
        {'from': 'pause', 'to': 'loop', 'label': 'next'},
        {'from': 'loop', 'to': 'end', 'label': 'done'},
    ],
}


def node(data, id):
    return next(n for n in data['nodes'] if n['id'] == id)


def mutated(change):
    data = copy.deepcopy(VALID)
    change(data)
    return data


# Broken files both the schema and the runtime validator must reject.
BROKEN = {
    'unbounded loop': lambda d: (node(d, 'loop').pop('max_loops'), node(d, 'loop').pop('every')),
    'gate without timeout': lambda d: node(d, 'ready').pop('timeout'),
    'unknown kind': lambda d: d['nodes'].append({'id': 'x', 'kind': 'subroutine'}),
    'test phase without name': lambda d: node(d, 'test').pop('name'),
    'unknown edge label': lambda d: d['edges'].append({'from': 'start', 'to': 'setup',
                                                        'label': 'maybe'}),
    'max_loops as a variable': lambda d: node(d, 'loop').update({'max_loops': '${N}'}),
    'object as argument': lambda d: node(d, 'burst').update({'args': [{'v': 1}]}),
    'on_timeout typo': lambda d: node(d, 'ready').update({'on_timeout': 'skip'}),
    'missing flow name': lambda d: d['flow'].pop('name'),
    'wrong version': lambda d: d['flow'].update({'version': 2}),
    'keyword without name': lambda d: node(d, 'measure').pop('keyword'),
    'decision without condition': lambda d: node(d, 'emc').pop('condition'),
    'empty nodes': lambda d: d.update({'nodes': []}),
}


class TestSchemaShape(unittest.TestCase):

    def test_shipped_file_matches_generated_schema(self):
        with open(SHIPPED, encoding='UTF-8') as file:
            self.assertEqual(file.read(), schema_text(),
                             'Regenerate: python -m robot.flow schema -o '
                             'src/robot/flow/flow.schema.json')

    def test_every_node_kind_is_defined(self):
        schema = json_schema()
        self.assertEqual(sorted(schema['definitions']), sorted(NODE_KINDS))

    def test_every_edge_label_is_allowed(self):
        edge = json_schema()['properties']['edges']['items']['oneOf'][1]
        self.assertEqual(edge['properties']['label']['enum'], list(EDGE_LABELS))

    def test_cli_writes_the_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'flow.schema.json')
            self.assertEqual(main(['schema', '--output', path]), 0)
            with open(path, encoding='UTF-8') as file:
                self.assertEqual(json.load(file), json_schema())


@unittest.skipIf(jsonschema is None, 'jsonschema is not installed')
class TestSchemaAgreesWithValidator(unittest.TestCase):

    def setUp(self):
        self.validator = jsonschema.Draft7Validator(json_schema())

    def errors(self, data):
        return list(self.validator.iter_errors(data))

    def test_schema_is_valid_draft7(self):
        jsonschema.Draft7Validator.check_schema(json_schema())

    def test_valid_flow_is_accepted_by_both(self):
        self.assertEqual(self.errors(VALID), [])
        load_flow(VALID)

    def test_broken_flows_are_rejected_by_both(self):
        for label, change in BROKEN.items():
            with self.subTest(label):
                data = mutated(change)
                self.assertTrue(self.errors(data), f'schema accepted: {label}')
                self.assertRaises(FlowError, load_flow, data)

    def test_unknown_attribute_is_reported_by_the_schema_only(self):
        # A typo the runner ignores; the editor should still flag it.
        data = mutated(lambda d: node(d, 'loop').update({'max_loop': 3}))
        self.assertTrue(self.errors(data))
        load_flow(data)

    def test_schema_key_is_allowed_in_flow_files(self):
        data = mutated(lambda d: d.update({'$schema': './flow.schema.json'}))
        self.assertEqual(self.errors(data), [])
        load_flow(data)

    def test_test_data_shapes_agree(self):
        # Structural rules (re-joining branches, reachability) are the
        # validator's alone, so only shape errors must agree.
        structural = {'invalid_body_no_next', 'invalid_no_join', 'invalid_unreachable',
                      'invalid_keyword'}
        for path in glob.glob(os.path.join(TESTDATA, '*.flow.json')):
            name = os.path.basename(path)[:-len('.flow.json')]
            with self.subTest(name):
                with open(path, encoding='UTF-8') as file:
                    data = json.load(file)
                if name.startswith('invalid_') and name not in structural:
                    self.assertTrue(self.errors(data))
                    self.assertRaises(FlowError, load_flow, data)
                else:
                    self.assertEqual(self.errors(data), [])


if __name__ == '__main__':
    unittest.main()
