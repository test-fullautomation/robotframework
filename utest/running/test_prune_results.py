import json
import os
import tempfile
import unittest
from io import StringIO

from robot.api import ExecutionResult
from robot.flow import build_flow_suite
from robot.model import BodyItem
from robot.running import TestSuite


class Sizes:
    """Listener (v3): how many iterations each loop of a test still holds at its end."""
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self):
        self.kept = []

    def end_test(self, data, result):
        for item in result.body:
            if item.type in (BodyItem.WHILE, BodyItem.FOR):
                self.kept.append(sum(1 for i in item.body if i.type == BodyItem.ITERATION))


def run(suite, **kwargs):
    config = dict(output=None, log=None, report=None, stdout=StringIO(), stderr=StringIO())
    config.update(kwargs)
    return suite.run(**config).suite


def loops(n):
    suite = TestSuite(name='Loops')
    test = suite.tests.create(name='Test')
    while_ = test.body.create_while(condition='True', limit=str(n), on_limit='pass')
    while_.body.create_keyword('No Operation')
    for_ = test.body.create_for(assign=['${i}'], flavor='IN RANGE', values=[str(n)])
    for_.body.create_keyword('No Operation')
    return suite


def iterations(item):
    return [i for i in item.body if i.type == BodyItem.ITERATION]


class TestPruneResults(unittest.TestCase):

    def test_kept_by_default(self):
        sizes = Sizes()
        run(loops(20), listener=sizes)
        while_kept, for_kept = sizes.kept
        self.assertGreaterEqual(while_kept, 20)
        self.assertEqual(for_kept, 20)

    def test_pruned_on_request_and_output_complete(self):
        sizes = Sizes()
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, 'output.xml')
            run(loops(20), pruneresults=True, output=output, listener=sizes)
            self.assertEqual(sizes.kept, [1, 1], 'only the last iteration of each loop stays')
            saved = ExecutionResult(output).suite.tests[0]
            self.assertEqual(len([i for i in iterations(saved.body[0]) if i.status == 'PASS']), 20)
            self.assertEqual(len(iterations(saved.body[1])), 20)
            self.assertEqual((saved.status, saved.body[0].status, saved.body[1].status),
                             ('PASS', 'PASS', 'PASS'))

    def test_flow_loops_are_pruned_by_default(self):
        flow = {'flow': {'name': 'Prune', 'version': 1},
                'nodes': [{'id': 'start', 'kind': 'start'},
                          {'id': 'loop', 'kind': 'loop', 'max_loops': 15},
                          {'id': 'w', 'kind': 'keyword', 'keyword': 'No Operation'},
                          {'id': 'end', 'kind': 'end'}],
                'edges': [['start', 'loop'], {'from': 'loop', 'to': 'w', 'label': 'body'},
                          {'from': 'w', 'to': 'loop', 'label': 'next'},
                          {'from': 'loop', 'to': 'end', 'label': 'done'}]}
        sizes = Sizes()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'prune.flow.json')
            with open(path, 'w', encoding='UTF-8') as file:
                json.dump(flow, file)
            run(build_flow_suite(path), listener=sizes)
        self.assertEqual(sizes.kept, [1])


if __name__ == '__main__':
    unittest.main()
