import io
import json
import os
import tempfile
import unittest

from robot import run
from robot.errors import DataError
from robot.flow.report import collect, generate_report, render


FLOW = {
    'flow': {'name': 'Report Flow', 'version': 1, 'checkpoint': False},
    'imports': {'libraries': ['report_lib.py']},
    'nodes': [
        {'id': 'start', 'kind': 'start'},
        {'id': 'setup', 'kind': 'phase', 'role': 'setup'},
        {'id': 'power', 'kind': 'keyword', 'keyword': 'Note', 'args': ['power on'], 'label': 'Power on'},
        {'id': 'cycle', 'kind': 'phase', 'role': 'test', 'name': 'Cycle'},
        {'id': 'ready', 'kind': 'gate', 'keyword': 'Ready After', 'args': [2], 'timeout': '5s',
         'interval': '0.01s', 'label': 'Bench ready'},
        {'id': 'loop', 'kind': 'loop', 'max_loops': 4, 'label': 'Cycles'},
        {'id': 'work', 'kind': 'keyword', 'keyword': 'Work', 'args': ['${FAIL_AT}'], 'label': 'Run cycle'},
        {'id': 'fix', 'kind': 'keyword', 'keyword': 'Note', 'args': ['recovered'], 'label': 'Recover'},
        {'id': 'which', 'kind': 'decision', 'condition': "$MODE == 'fast'", 'label': 'Fast mode?'},
        {'id': 'fast', 'kind': 'keyword', 'keyword': 'Note', 'args': ['fast']},
        {'id': 'slow', 'kind': 'keyword', 'keyword': 'Note', 'args': ['slow']},
        {'id': 'done', 'kind': 'keyword', 'keyword': 'Note', 'args': ['joined']},
        {'id': 'teardown', 'kind': 'phase', 'role': 'teardown'},
        {'id': 'release', 'kind': 'keyword', 'keyword': 'Note', 'args': ['released']},
        {'id': 'end', 'kind': 'end'},
    ],
    'edges': [
        ['start', 'setup'], ['setup', 'power'], ['power', 'cycle'], ['cycle', 'ready'], ['ready', 'loop'],
        {'from': 'loop', 'to': 'work', 'label': 'body'}, {'from': 'work', 'to': 'loop', 'label': 'next'},
        {'from': 'loop', 'to': 'fix', 'label': 'on_failure'}, {'from': 'fix', 'to': 'loop', 'label': 'continue'},
        {'from': 'loop', 'to': 'which', 'label': 'done'},
        {'from': 'which', 'to': 'fast', 'label': 'yes'}, {'from': 'which', 'to': 'slow', 'label': 'no'},
        ['fast', 'done'], ['slow', 'done'], ['done', 'teardown'], ['teardown', 'release'], ['release', 'end'],
    ],
}

LIBRARY = '''
COUNT = {'ready': 0, 'work': 0}

def note(text):
    print(text)

def ready_after(attempts):
    COUNT['ready'] += 1
    if COUNT['ready'] < int(attempts):
        raise AssertionError('not yet')

def work(fail_at):
    COUNT['work'] += 1
    if str(COUNT['work']) == str(fail_at):
        raise AssertionError('cycle %d broke' % COUNT['work'])
'''


class TestReport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.flow = os.path.join(cls.tmp.name, 'plan.flow.json')
        with open(cls.flow, 'w', encoding='UTF-8') as file:
            json.dump(FLOW, file)
        with open(os.path.join(cls.tmp.name, 'report_lib.py'), 'w', encoding='UTF-8') as file:
            file.write(LIBRARY)
        cls.out = os.path.join(cls.tmp.name, 'out')
        rc = run(cls.flow, parser='robot.flow', outputdir=cls.out, output='output.xml',
                 log=None, report=None, stdout=io.StringIO(),
                 variable=['FAIL_AT:2', 'MODE:slow'])
        cls.rc = rc
        cls.data = collect(os.path.join(cls.out, 'output.xml'))
        cls.stats = cls.data['flows'][0]['stats']

    def test_run_passed_and_one_flow_found(self):
        self.assertEqual(self.rc, 0)
        self.assertEqual(len(self.data['flows']), 1)
        flow = self.data['flows'][0]
        self.assertEqual(flow['name'], 'Report Flow')
        self.assertEqual([l['role'] for l in flow['lanes']], ['setup', 'test', 'teardown'])
        self.assertEqual([l['status'] for l in flow['lanes']], ['PASS'] * 3)

    def test_nodes_in_setup_and_teardown(self):
        self.assertEqual(self.stats['power']['runs'], 1)
        self.assertEqual(self.stats['release']['runs'], 1)
        self.assertTrue(self.stats['power']['last'].startswith('s1-k1-k'))

    def test_gate_attempts(self):
        gate = self.stats['ready']
        self.assertEqual((gate['runs'], gate['status'], gate['attempts']), (1, {'PASS': 1}, [2]))

    def test_loop_counts_and_edges(self):
        loop = self.stats['loop']
        self.assertEqual(loop['runs'], 1)
        self.assertEqual([i['status'] for i in loop['iterations']], ['PASS', 'PASS', 'PASS', 'PASS'])
        self.assertEqual(loop['edges'], {'body': 4, 'next': 4, 'done': 1, 'on_failure': 1, 'continue': 1})
        work = self.stats['work']
        self.assertEqual((work['runs'], work['status']), (4, {'PASS': 3, 'FAIL': 1}))
        self.assertEqual(work['last_error'], 'cycle 2 broke')
        self.assertEqual(work['first_fail'], work['last_fail'])
        self.assertTrue(work['first_fail'].endswith('-k2-k1-k1'), work['first_fail'])
        fix = self.stats['fix']
        self.assertEqual((fix['runs'], fix['status']), (1, {'PASS': 1}))

    def test_decision_edges(self):
        which = self.stats['which']
        self.assertEqual(which['edges'], {'no': 1})
        self.assertEqual(self.stats['slow']['runs'], 1)
        self.assertEqual(self.stats['fast']['runs'], 0, 'a branch not taken never ran')

    def test_plan_keeps_labels_and_structure(self):
        plan = self.data['flows'][0]['plan']
        self.assertEqual([l['name'] for l in plan], ['Setup', 'Cycle', 'Teardown'])
        steps = plan[1]['steps']
        self.assertEqual([s['kind'] for s in steps], ['gate', 'loop', 'decision', 'keyword'])
        self.assertEqual(steps[0]['label'], 'Bench ready')
        self.assertEqual([s['id'] for s in steps[1]['body']], ['work'])
        self.assertEqual([s['id'] for s in steps[1]['recovery']], ['fix'])
        self.assertEqual(([s['id'] for s in steps[2]['yes']], [s['id'] for s in steps[2]['no']]), (['fast'], ['slow']))

    def test_generate_report_writes_html_with_data(self):
        path, count = generate_report(os.path.join(self.out, 'output.xml'),
                                      os.path.join(self.out, 'flow.html'), 'log.html')
        self.assertEqual(count, 1)
        with open(path, encoding='UTF-8') as file:
            html = file.read()
        self.assertIn('Report Flow', html)
        self.assertIn('"Bench ready"', html)
        self.assertIn('var LOG = "log.html"', html)
        self.assertIn('cycle 2 broke', html)

    def test_render_escapes_script_end(self):
        data = dict(self.data)
        data['flows'][0]['stats']['work']['last_error'] = '</script><b>'
        html = render(data)
        self.assertNotIn('</script><b>', html)
        self.assertIn('<\\/script>', html)

    def test_not_a_flow_output(self):
        with self.assertRaises(DataError) as cm:
            collect(os.path.join(os.path.dirname(__file__), '..', 'result', 'golden.xml'))
        self.assertIn('No flow suite', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
