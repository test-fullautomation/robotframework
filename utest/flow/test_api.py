import json
import os
import tempfile
import unittest

from robot.flow import FlowError, build_suite, load_flow, render_robot, structure
from robot.flow.api import Flow


def edges(data):
    return {(e[0], e[1], '') if isinstance(e, list) else (e['from'], e['to'], e['label'])
            for e in data['edges']}


def rendered(data):
    return render_robot(build_suite(structure(load_flow(data))))


class TestSequences(unittest.TestCase):

    def test_plain_sequence_is_one_test_named_after_the_flow(self):
        with Flow('Plain') as f:
            f.keyword('a', 'Log', 'one')
            f.sleep('b', '1s')
        data = f.to_dict()
        self.assertEqual(edges(data), {('start', 'a', ''), ('a', 'b', ''), ('b', 'end', '')})
        self.assertEqual(structure(load_flow(data)).tests[0].name, 'Plain')

    def test_phases(self):
        with Flow('Phased', libraries=['checks.py'], variables={'V': 1}) as f:
            with f.setup():
                f.gate('ready', 'Ready', timeout='10s', interval='1s', on_timeout='fail')
            with f.test('First'):
                f.keyword('a', 'Log', 'x', assign='${A}')
            with f.test('Second'):
                f.flow('call', 'sub.flow.json', VOLTS='${V}')
            with f.teardown():
                f.keyword('z', 'Log', 'bye')
        data = f.to_dict()
        self.assertEqual(data['imports'], {'libraries': ['checks.py']})
        self.assertEqual(data['variables'], {'V': 1})
        self.assertIn(('setup', 'ready', ''), edges(data))
        self.assertIn(('a', 'second', ''), edges(data))
        self.assertIn(('call', 'teardown', ''), edges(data))
        flow = structure(load_flow(data))
        self.assertEqual([t.name for t in flow.tests], ['First', 'Second'])

    def test_duplicate_id(self):
        with self.assertRaises(FlowError):
            with Flow('Dup') as f:
                f.keyword('a', 'Log', 'x')
                f.keyword('a', 'Log', 'y')

    def test_save_writes_a_valid_flow_file(self):
        with Flow('Saved') as f:
            f.keyword('a', 'No Operation')
        with tempfile.TemporaryDirectory() as tmp:
            path = f.save(os.path.join(tmp, 'saved.flow.json'))
            with open(path, encoding='UTF-8') as file:
                self.assertEqual(json.load(file), f.to_dict())
            load_flow(path)


class TestDecisions(unittest.TestCase):

    def test_branches_rejoin_at_the_next_step(self):
        with Flow('Decide') as f:
            with f.decision('d', '$X') as d:
                with d.yes():
                    f.keyword('y', 'Log', 'yes')
                with d.no():
                    f.keyword('n1', 'Log', 'no')
                    f.keyword('n2', 'Log', 'no')
            f.keyword('after', 'Log', 'joined')
        e = edges(f.to_dict())
        self.assertTrue({('d', 'y', 'yes'), ('d', 'n1', 'no'), ('y', 'after', ''),
                         ('n2', 'after', '')} <= e)

    def test_an_empty_branch_goes_straight_to_the_join(self):
        with Flow('Half') as f:
            with f.decision('d', '$X') as d:
                with d.yes():
                    f.keyword('y', 'Log', 'yes')
            f.keyword('after', 'Log', 'joined')
        self.assertIn(('d', 'after', 'no'), edges(f.to_dict()))
        self.assertIn('ELSE', rendered(f.to_dict()))

    def test_an_empty_branch_cannot_end_a_loop_body(self):
        with self.assertRaises(FlowError) as ctx:
            with Flow('Bad') as f:
                with f.loop('l', max_loops=2):
                    with f.decision('d', '$X') as d:
                        with d.yes():
                            f.keyword('y', 'Log', 'yes')
        self.assertIn('empty decision branch', str(ctx.exception))


class TestContainers(unittest.TestCase):

    def test_loop_body_returns_with_next_and_leaves_with_done(self):
        with Flow('Loop') as f:
            with f.loop('l', max_loops=3, every='1s'):
                f.keyword('a', 'Log', 'x')
            f.keyword('after', 'Log', 'done')
        e = edges(f.to_dict())
        self.assertTrue({('l', 'a', 'body'), ('a', 'l', 'next'), ('l', 'after', 'done')} <= e)

    def test_recovery_continue(self):
        with Flow('Recover') as f:
            with f.loop('l', max_seconds='1h') as loop:
                f.keyword('a', 'Work')
                with loop.on_failure('continue'):
                    f.keyword('r', 'Recover')
        e = edges(f.to_dict())
        self.assertTrue({('a', 'l', 'next'), ('l', 'r', 'on_failure'), ('r', 'l', 'continue'),
                         ('l', 'end', 'done')} <= e)
        self.assertIn('EXCEPT', rendered(f.to_dict()))

    def test_recovery_abort_leads_to_the_node_after(self):
        with Flow('Abort') as f:
            with f.try_('t') as attempt:
                f.keyword('a', 'Flash')
                with attempt.on_failure('abort'):
                    f.keyword('r', 'Reset')
            f.keyword('after', 'Log', 'x')
        e = edges(f.to_dict())
        self.assertTrue({('t', 'a', 'body'), ('a', 't', 'next'), ('r', 'after', 'abort'),
                         ('t', 'after', 'done')} <= e)
        self.assertIn('Fail    ${flow_error}', rendered(f.to_dict()))

    def test_loop_as_last_node_of_a_loop_leaves_with_next(self):
        with Flow('Nested') as f:
            with f.loop('outer', max_loops=2):
                f.keyword('a', 'Log', 'x')
                with f.loop('inner', max_loops=2):
                    f.keyword('b', 'Log', 'y')
        e = edges(f.to_dict())
        self.assertTrue({('b', 'inner', 'next'), ('inner', 'outer', 'next'),
                         ('outer', 'end', 'done')} <= e)
        self.assertEqual(rendered(f.to_dict()).count('WHILE'), 2)

    def test_decision_as_last_node_of_a_loop(self):
        with Flow('Last') as f:
            with f.loop('l', max_loops=2):
                with f.decision('d', '$X') as d:
                    with d.yes():
                        f.keyword('y', 'Log', 'y')
                    with d.no():
                        f.keyword('n', 'Log', 'n')
        e = edges(f.to_dict())
        self.assertTrue({('y', 'l', 'next'), ('n', 'l', 'next')} <= e)

    def test_empty_body_and_recovery(self):
        with self.assertRaises(FlowError):
            with Flow('Empty') as f:
                with f.loop('l', max_loops=2):
                    pass
        with self.assertRaises(FlowError):
            with Flow('Empty') as f:
                with f.loop('l', max_loops=2) as loop:
                    f.keyword('a', 'Log', 'x')
                    with loop.on_failure():
                        pass


if __name__ == '__main__':
    unittest.main()
