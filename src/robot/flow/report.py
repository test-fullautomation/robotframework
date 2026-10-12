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

"""The flow report: the plan as it was drawn, with what a run did on each node.

``log.html`` is the record of what ran, in order: a loop of a thousand
cycles is a thousand blocks. The flow report is the other view of the same
run. It draws the flow file, one box per node however often the node ran,
with the counts, the time and the last error of every node, the counts on
the edges, where the run stopped and what a restart would do. Every box
links into ``log.html``.

The report needs no help from the run. The builder is deterministic, so the
suite is built again from the flow file and walked next to the result in
``output.xml``: the n-th item of a body is the same node in both. This also
means the flow file must still be the one the run used.

::

    robot --parser robot.flow --flowreport flow.html plan.flow.json
    python -m robot.flow report results/output.xml [--output flow.html]
"""

import json
import os
import re
import re
from datetime import datetime
from html import escape
from pathlib import Path

from robot.api import ExecutionResult
from robot.errors import DataError

from . import build_flow_suite
from .builder import FLOW_LIBRARY, GATE_KEYWORD
from .control import checkpoint_name, fingerprint, read_checkpoint
from .graph import (Decision, FlowStep, GateStep, KeywordStep, Loop, SleepStep, Try,
                    structure)
from .schema import PHASE, load_flow


FLOW_SUFFIX = '.flow.json'
MESSAGE = 'MESSAGE'
NOT_RUN = 'NOT RUN'


# ------------------------------------------------------------------ public

def generate_report(output, outfile=None, log='log.html', flow=None, checkpoint=None):
    """Write the flow report of ``output`` (an ``output.xml``).

    ``log`` is the path of ``log.html`` as the report links to it, relative to
    the report. ``flow`` names the flow file when the output does not (its
    suite source); ``checkpoint`` the checkpoint file to read what is left.
    Returns ``(path, number of flows found)``.
    """
    data = collect(output, flow, checkpoint)
    path = outfile or os.path.join(os.path.dirname(os.path.abspath(output)), 'flow.html')
    with open(path, 'w', encoding='UTF-8', newline='\n') as file:
        file.write(render(data, log))
    return path, len(data['flows'])


def collect(output, flow=None, checkpoint=None):
    """The report's data: one entry per flow suite in ``output``."""
    result = ExecutionResult(output)
    flows = []
    for suite in _suites(result.suite):
        source = _source(suite, flow)
        if not source:
            continue
        flows.append(_FlowRun(suite, source, output, checkpoint).data())
    if not flows:
        raise DataError(f"No flow suite in '{output}': the suite source is not a "
                        f"'{FLOW_SUFFIX}' file. Use --flow to name the flow file.")
    return {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'output': os.path.abspath(output),
        'flows': flows,
    }


# ----------------------------------------------------------------- helpers

def _suites(suite):
    if suite.tests:
        yield suite
    for child in suite.suites:
        yield from _suites(child)


def _source(suite, flow):
    source = str(suite.source or '')
    if flow and (not source or os.path.abspath(source) == os.path.abspath(flow)
                 or not source.lower().endswith(FLOW_SUFFIX)):
        return flow if os.path.isfile(flow) else None
    if source.lower().endswith(FLOW_SUFFIX) and os.path.isfile(source):
        return source
    return None


def _seconds(item):
    elapsed = getattr(item, 'elapsed_time', None)
    if elapsed is not None:
        return elapsed.total_seconds()
    return (getattr(item, 'elapsedtime', 0) or 0) / 1000.0


def _error(item):
    """The failure text of a failed item.

    The status message, or the last FAIL/UNKNOWN message logged in the body
    when keywords do not carry a status message (Robot Framework 6).
    """
    if item.message:
        return item.message
    for child in reversed(list(getattr(item, 'body', []) or [])):
        if getattr(child, 'type', None) == MESSAGE:
            if child.level in ('FAIL', 'UNKNOWN', 'ERROR'):
                return child.message
        else:
            found = _error(child)
            if found:
                return found
    return ''


def _items(body):
    """Body items that are not messages."""
    return [item for item in body if getattr(item, 'type', None) != MESSAGE]


def _numbered(parent_id, body):
    """``(item, id)`` for the items of a result body as log.html numbers them.

    Messages take no number. IF and TRY roots are flattened: their branches
    take the positions, the root itself has no id of its own.
    """
    out = []
    k = 0
    for item in _items(body):
        if item.type in ('IF/ELSE ROOT', 'TRY/EXCEPT ROOT'):
            branches = []
            for branch in _items(item.body):
                k += 1
                branches.append((branch, f'{parent_id}-k{k}'))
            out.append((item, None, branches))
        else:
            k += 1
            out.append((item, f'{parent_id}-k{k}', None))
    return out


class _Stats:
    """What a run did on one node."""

    def __init__(self, key):
        self.key = key
        self.runs = 0
        self.status = {}            # status -> count
        self.elapsed = 0.0
        self.max = 0.0
        self.max_id = None
        self.first_fail = None
        self.last_fail = None
        self.last = None
        self.last_error = ''
        self.edges = {}             # edge label -> count
        self.iterations = []        # loops: [{status, id, elapsed}]
        self.attempts = []          # gates: attempts per run
        self.limit = None           # loops: the iteration limit the run used
        self.roles = {}             # role -> count of items run

    def run(self, item, item_id):
        status = item.status
        if status == NOT_RUN:
            return              # a branch not taken: the node was never entered
        self.runs += 1
        self.status[status] = self.status.get(status, 0) + 1
        seconds = _seconds(item)
        self.elapsed += seconds
        if seconds >= self.max:
            self.max, self.max_id = seconds, item_id
        if item_id:
            self.last = item_id
        if status in ('FAIL', 'UNKNOWN'):
            if item_id:
                self.first_fail = self.first_fail or item_id
                self.last_fail = item_id
            self.last_error = _error(item) or self.last_error

    def edge(self, label, count=1):
        self.edges[label] = self.edges.get(label, 0) + count

    def to_dict(self):
        return {
            'runs': self.runs, 'status': self.status,
            'elapsed': round(self.elapsed, 3), 'max': round(self.max, 3), 'max_id': self.max_id,
            'first_fail': self.first_fail, 'last_fail': self.last_fail, 'last': self.last,
            'last_error': self.last_error, 'edges': self.edges,
            'iterations': self.iterations, 'attempts': self.attempts, 'roles': self.roles,
            'limit': self.limit,
        }


class _FlowRun:
    """Walks a result suite next to the suite built again from its flow file."""

    def __init__(self, suite, source, output, checkpoint):
        self.suite = suite
        self.source = source
        self.output = output
        self.checkpoint = checkpoint
        self.origins = {}
        self.built = build_flow_suite(source, origins=self.origins)
        self.keywords = {kw.name: kw for kw in self.built.resource.keywords}
        self.stats = {}

    def data(self):
        suite = self.suite
        lanes = []
        setup = self._setup_or_teardown(suite.setup, 'Flow Setup', 'setup')
        if setup:
            lanes.append(setup)
        built_tests = list(self.built.tests)
        for index, test in enumerate(suite.tests):
            lane = {'role': 'test', 'name': test.name, 'status': test.status,
                    'message': test.message, 'elapsed': round(_seconds(test), 3),
                    'id': test.id, 'stopped': _stopped(test)}
            if index < len(built_tests):
                self._walk(built_tests[index].body, test.body, test.id)
            lanes.append(lane)
        teardown = self._setup_or_teardown(suite.teardown, 'Flow Teardown', 'teardown')
        if teardown:
            lanes.append(teardown)
        flow = structure(load_flow(self.source))
        return {
            'name': flow.name,
            'source': os.path.abspath(self.source),
            'suite': {'name': suite.name, 'id': suite.id, 'status': suite.status,
                      'message': suite.message, 'elapsed': round(_seconds(suite), 3),
                      'tests': {'passed': suite.statistics.passed,
                                'failed': suite.statistics.failed,
                                'unknown': getattr(suite.statistics, 'unknown', 0),
                                'skipped': suite.statistics.skipped}},
            'lanes': lanes,
            'plan': self._plan(flow),
            'stats': {key: stats.to_dict() for key, stats in self.stats.items()},
            'checkpoint': self._checkpoint(),
        }

    # ----------------------------------------------------------- matching

    def _setup_or_teardown(self, item, keyword, role):
        if not item or keyword not in self.keywords:
            return None
        self._walk(self.keywords[keyword].body, item.body, f'{self.suite.id}-k1' if role == 'setup' else f'{self.suite.id}-k2')
        return {'role': role, 'name': item.name, 'status': item.status, 'message': item.message,
                'elapsed': round(_seconds(item), 3), 'id': None, 'stopped': None}

    def _stat(self, key):
        if key not in self.stats:
            self.stats[key] = _Stats(key)
        return self.stats[key]

    def _origin(self, built):
        return self.origins.get(id(built))

    def _walk(self, built_body, result_body, parent_id):
        built_items = list(built_body)
        result_items = _numbered(parent_id, result_body)
        for built, (item, item_id, branches) in zip(built_items, result_items):
            origin = self._origin(built)
            btype = getattr(built, 'type', None)
            rtype = getattr(item, 'type', None)
            if btype == 'KEYWORD' and rtype in ('KEYWORD', 'SETUP', 'TEARDOWN'):
                self._keyword(built, item, item_id, origin)
            elif btype == 'WHILE' and rtype == 'WHILE':
                self._loop(built, item, item_id, origin)
            elif btype == 'IF/ELSE ROOT' and rtype == 'IF/ELSE ROOT':
                self._decision(built, item, branches, origin)
            elif btype == 'TRY/EXCEPT ROOT' and rtype == 'TRY/EXCEPT ROOT':
                self._guarded(built, item, branches, origin)
            else:
                return          # the shapes differ: the flow file is not the run's

    def _keyword(self, built, item, item_id, origin):
        if origin:
            key, role = origin
            stats = self._stat(key)
            stats.roles[role] = stats.roles.get(role, 0) + 1
            if role == 'node':
                stats.run(item, item_id)
                if built.name == GATE_KEYWORD:
                    stats.attempts.append(len(_items(item.body)))
            elif role == 'abort':
                stats.edge('abort')
            elif role == 'loop-start':
                stats.limit = _assigned_limit(item)
        if built.name in self.keywords:
            self._walk(self.keywords[built.name].body, item.body, item_id)

    def _loop(self, built, item, item_id, origin):
        stats = self._stat(origin[0]) if origin else None
        iterations = [(it, it_id) for it, it_id, _ in _numbered(item_id, item.body)
                      if it.type == 'ITERATION']
        if stats:
            stats.run(item, item_id)
            stats.edge('body', len(iterations))
            stats.edge('next', sum(1 for it, _ in iterations if it.status == 'PASS'))
            stats.edge('done', 1 if item.status == 'PASS' else 0)
            for it, it_id in iterations:
                stats.iterations.append({'status': it.status, 'id': it_id,
                                         'elapsed': round(_seconds(it), 3)})
        for it, it_id in iterations:
            self._walk(built.body, it.body, it_id)

    def _decision(self, built, item, branches, origin):
        stats = self._stat(origin[0]) if origin else None
        if stats:
            stats.run(item, branches[0][1] if branches else None)
        for b_branch, (r_branch, branch_id) in zip(built.body, branches or []):
            if stats and r_branch.status != NOT_RUN:
                stats.edge('yes' if r_branch.type == 'IF' else 'no')
            self._walk(b_branch.body, r_branch.body, branch_id)

    def _guarded(self, built, item, branches, origin):
        stats = self._stat(origin[0]) if origin else None
        if stats and origin[1] == 'node':      # a try node; a loop's guard belongs to the loop
            stats.run(item, branches[0][1] if branches else None)
            stats.edge('done', 1 if item.status == 'PASS' else 0)
        for b_branch, (r_branch, branch_id) in zip(built.body, branches or []):
            if stats and r_branch.type == 'EXCEPT' and r_branch.status != NOT_RUN:
                stats.edge('on_failure')
                if r_branch.status == 'PASS':
                    stats.edge('continue')
            self._walk(b_branch.body, r_branch.body, branch_id)

    # --------------------------------------------------------------- plan

    def _plan(self, flow):
        base = Path(self.source).resolve().parent
        lanes = []
        if flow.setup:
            lanes.append({'role': 'setup', 'name': 'Setup', 'steps': self._steps(flow.setup.steps, '', base)})
        for phase in flow.tests:
            lanes.append({'role': 'test', 'name': phase.name, 'key': f'phase:{phase.name}',
                          'steps': self._steps(phase.steps, '', base)})
        if flow.teardown:
            lanes.append({'role': 'teardown', 'name': 'Teardown', 'steps': self._steps(flow.teardown.steps, '', base)})
        return lanes

    def _steps(self, steps, prefix, base):
        out = []
        for step in steps:
            node = step.node
            entry = {'key': prefix + step.id, 'id': step.id, 'label': node.label or step.id}
            if isinstance(step, GateStep):
                entry.update(kind='gate', detail=' '.join([step.keyword] + list(step.args)),
                             timeout=step.timeout, interval=step.interval, on_timeout=step.on_timeout)
            elif isinstance(step, KeywordStep):
                entry.update(kind='keyword', detail=' '.join([step.keyword] + list(step.args)))
            elif isinstance(step, SleepStep):
                entry.update(kind='sleep', detail=step.duration)
            elif isinstance(step, FlowStep):
                entry.update(kind='flow', detail=step.file)
                entry['steps'] = self._subflow(step, base)
            elif isinstance(step, Decision):
                entry.update(kind='decision', detail=step.condition,
                             yes=self._steps(step.yes, prefix, base), no=self._steps(step.no, prefix, base))
            elif isinstance(step, (Loop, Try)):
                entry.update(kind='loop' if isinstance(step, Loop) else 'try',
                             body=self._steps(step.body, prefix, base),
                             recovery=self._steps(step.recovery, prefix, base) if step.recovery is not None else None,
                             then=step.then)
                if isinstance(step, Loop):
                    entry.update(max_loops=step.max_loops, max_seconds=step.max_seconds, every=step.every)
            out.append(entry)
        return out

    def _subflow(self, step, base):
        path = (base / step.file).resolve()
        try:
            data = load_flow(path)
            inner = structure(data)
        except DataError:
            return []
        return self._steps(inner.tests[0].steps, f'{data.name}::', path.parent)

    # --------------------------------------------------------- checkpoint

    def _checkpoint(self):
        path = self.checkpoint or self._checkpoint_from_stop()
        if not path:
            stem = os.path.basename(self.source)
            for extension in ('.json', '.flow'):
                if stem.lower().endswith(extension):
                    stem = stem[:-len(extension)]
            path = os.path.join(os.path.dirname(os.path.abspath(self.output)), checkpoint_name(stem))
        data, _ = read_checkpoint(path)
        if not data:
            return None
        return {'path': os.path.abspath(path), 'matches': data.get('fingerprint') == fingerprint(self.source),
                'position': data.get('position'), 'remaining': data.get('remaining'),
                'completed': [p.get('name') for p in data.get('completed_phases') or []],
                'saved': data.get('saved')}


    def _checkpoint_from_stop(self):
        # A stop names the checkpoint it wrote: 'resumable from <path>'.
        for test in self.suite.tests:
            message = test.message or ''
            if message.startswith('Stopped by') and '; resumable from ' in message:
                return message.split('; resumable from ', 1)[1].rstrip('.')
        return None


def _assigned_limit(item):
    """The limit `Flow Loop` returned: logged as '${flow_limit_x} = 3'."""
    for child in getattr(item, 'body', []) or []:
        text = getattr(child, 'message', '') if getattr(child, 'type', None) == MESSAGE else ''
        match = re.match(r'\$\{flow_limit_\w+\} = (\d+)$', text or '')
        if match:
            return int(match.group(1))
    return None


def _stopped(test):
    message = test.message or ''
    return message.split(';', 1)[0] if message.startswith('Stopped by') else None


# ------------------------------------------------------------------ render

def render(data, log='log.html'):
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    title = ', '.join(flow['name'] for flow in data['flows'])
    return (TEMPLATE
            .replace('%TITLE%', escape(title))
            .replace('%LOG%', escape(log or ''))
            .replace('%DATA%', payload))


def report_cli(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog='python -m robot.flow report',
                                     description='The flow report of a run: the plan with what '
                                                 'the run did on each node.')
    parser.add_argument('output', help='output.xml of the run')
    parser.add_argument('-o', '--output-file', dest='outfile', help='the report; default flow.html next to output.xml')
    parser.add_argument('--log', default='log.html', help='log.html the report links to, relative to the report')
    parser.add_argument('--flow', help='the flow file, when the output does not name it')
    parser.add_argument('--checkpoint', help='checkpoint file of a stopped run; default: next to output.xml')
    args = parser.parse_args(argv)
    try:
        path, count = generate_report(args.output, args.outfile, args.log, args.flow, args.checkpoint)
    except DataError as err:
        sys.stderr.write(f'{err}\n')
        return 1
    print(f'Flow report: {path} ({count} flow{"s" if count != 1 else ""})')
    return 0


TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flow report: %TITLE%</title>
<style>
:root {
  --bg: #eef1f2; --panel: #ffffff; --band: #f6f8f8; --box: #fbfcfc; --line: #cdd5d8;
  --fg: #16242b; --muted: #5b6b73; --accent: #0b5d7a;
  --pass: #1f7a4d; --fail: #b3261e; --unknown: #0080fe; --idle: #8a979d; --skip: #a8650c;
  --pass-soft: #dff1e7; --fail-soft: #f8dedb; --unknown-soft: #dcecff; --idle-soft: #e4e9eb; --skip-soft: #f6e6cc;
  --mono: ui-monospace, Consolas, "Liberation Mono", monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10181c; --panel: #18242a; --band: #141e23; --box: #1d2b32; --line: #2e3f47;
    --fg: #e3ecef; --muted: #93a4ac; --accent: #5cc0e0;
    --pass: #5fce94; --fail: #ff8a80; --unknown: #8cc4ff; --idle: #7d8c93; --skip: #f0b25a;
    --pass-soft: #173a2a; --fail-soft: #4a1f1c; --unknown-soft: #1c3252; --idle-soft: #24323a; --skip-soft: #46330f;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, "Segoe UI", sans-serif; }
.page { max-width: 1480px; margin: 0 auto; padding: 20px 20px 48px; display: flex; flex-direction: column; gap: 18px; }
h1, h2, h3 { margin: 0; line-height: 1.2; }
h1 { font-size: 26px; } h2 { font-size: 18px; } h3 { font-size: 15px; }
p { margin: 0; }
code, .mono { font-family: var(--mono); font-size: 0.92em; }
a { color: var(--accent); }
.eyebrow { font: 500 11px var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.head { display: flex; flex-wrap: wrap; gap: 12px 32px; align-items: flex-end; justify-content: space-between; }
.head .what { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.sub { color: var(--muted); max-width: 80ch; overflow-wrap: anywhere; }
.verdict { display: inline-flex; align-items: center; gap: 8px; font: 600 12px var(--mono); letter-spacing: 0.06em; padding: 4px 10px; border-radius: 4px; }
.v-PASS { background: var(--pass-soft); color: var(--pass); }
.v-FAIL { background: var(--fail-soft); color: var(--fail); }
.v-UNKNOWN { background: var(--unknown-soft); color: var(--unknown); }
.v-SKIP { background: var(--skip-soft); color: var(--skip); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.fact { background: var(--panel); padding: 10px 14px; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.fact b { font-size: 19px; font-variant-numeric: tabular-nums; }
.fact span { color: var(--muted); font-size: 12.5px; }
.work { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 18px; align-items: start; }
@media (max-width: 980px) { .work { grid-template-columns: minmax(0, 1fr); } }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; min-width: 0; }
.card > header { padding: 10px 16px; border-bottom: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; justify-content: space-between; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12.5px; color: var(--muted); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.scroll { overflow-x: auto; }
svg.plan { display: block; width: 100%; height: auto; }  /* scales to the card; min-width (set per plan) keeps it readable, then it scrolls */
.lane { fill: var(--band); stroke: var(--line); }
.lane-title { font: 600 15px system-ui, sans-serif; fill: var(--fg); }
.lane-role, .kind { font: 500 10px var(--mono); letter-spacing: 0.1em; fill: var(--muted); }
.lane-note, .small { font: 400 11.5px system-ui, sans-serif; fill: var(--muted); }
.edge { fill: none; stroke: var(--muted); stroke-width: 1.5; }
.edge.cold { stroke: var(--idle); stroke-dasharray: 4 4; opacity: 0.8; }
.edge.fail { stroke: var(--fail); }
.edge.rec { stroke: var(--skip); }
.arrow { fill: var(--muted); } .arrow.cold { fill: var(--idle); } .arrow.fail { fill: var(--fail); } .arrow.rec { fill: var(--skip); }
.edge-label { font: 500 11px var(--mono); fill: var(--fg); paint-order: stroke; stroke: var(--band); stroke-width: 5px; stroke-linejoin: round; }
.edge-label.cold { fill: var(--idle); }
.box-in .edge-label { stroke: var(--box); }
.node { cursor: pointer; outline: none; }
.node .box { fill: var(--panel); stroke: var(--line); stroke-width: 1.4; }
.node .container { fill: var(--box); stroke: var(--line); stroke-width: 1.4; }
.node.s-PASS .box, .node.s-PASS .container { stroke: var(--pass); }
.node.s-FAIL .box, .node.s-FAIL .container { stroke: var(--fail); stroke-width: 2; }
.node.s-UNKNOWN .box, .node.s-UNKNOWN .container { stroke: var(--unknown); stroke-width: 2; }
.node.s-SKIP .box { stroke: var(--skip); }
.node.s-NONE .box, .node.s-NONE .container { stroke: var(--idle); stroke-dasharray: 5 4; }
.node:hover > .box { fill: var(--band); }
.node .halo { stroke: none; fill: none; }
.node.sel > .halo, .node:focus-visible > .halo { stroke: var(--accent); stroke-width: 3; fill: none; }
.label { font: 600 13.5px system-ui, sans-serif; fill: var(--fg); }
.chip rect { rx: 3px; } .chip text { font: 600 11px var(--mono); }
.chip.pass rect { fill: var(--pass-soft); } .chip.pass text { fill: var(--pass); }
.chip.fail rect { fill: var(--fail-soft); } .chip.fail text { fill: var(--fail); }
.chip.idle rect { fill: var(--idle-soft); } .chip.idle text { fill: var(--muted); }
.chip.unknown rect { fill: var(--unknown-soft); } .chip.unknown text { fill: var(--unknown); }
.chip.skip rect { fill: var(--skip-soft); } .chip.skip text { fill: var(--skip); }
.track { fill: var(--idle-soft); } .fill-a { fill: var(--accent); }
.toggle { cursor: pointer; outline: none; }
.toggle rect { fill: var(--panel); stroke: var(--line); stroke-width: 1.2; }
.toggle text { font: 600 13px var(--mono); fill: var(--accent); }
.toggle:hover rect, .toggle:focus-visible rect { stroke: var(--accent); }
.tools { display: flex; gap: 6px; }
.btn { font: 500 12px system-ui, sans-serif; color: var(--accent); background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 3px 9px; cursor: pointer; }
.btn:hover { border-color: var(--accent); }
.join { fill: var(--muted); } .join.cold { fill: var(--idle); } .join.rec { fill: var(--skip); }
.detail { position: sticky; top: 12px; }
.detail .body { padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 12px; }
.detail h2 { font-size: 20px; overflow-wrap: anywhere; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 5px 14px; font-size: 13.5px; margin: 0; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; font-variant-numeric: tabular-nums; min-width: 0; overflow-wrap: anywhere; }
.counts { display: flex; flex-wrap: wrap; gap: 8px; }
.count { border-radius: 4px; padding: 5px 10px; font: 600 12.5px var(--mono); }
.count.pass { background: var(--pass-soft); color: var(--pass); }
.count.fail { background: var(--fail-soft); color: var(--fail); }
.count.unknown { background: var(--unknown-soft); color: var(--unknown); }
.count.skip { background: var(--skip-soft); color: var(--skip); }
.count.idle { background: var(--idle-soft); color: var(--muted); }
.err { border: 1px solid var(--fail); background: var(--fail-soft); border-radius: 4px; padding: 8px 10px; font: 12.5px/1.45 var(--mono); overflow-wrap: anywhere; max-height: 12em; overflow: auto; }
.jumps { display: flex; flex-direction: column; gap: 5px; font-size: 13.5px; }
.jump { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.hint { color: var(--muted); font-size: 12.5px; }
.strip { display: grid; grid-template-columns: repeat(auto-fill, 16px); gap: 3px; }
.strip a { display: block; height: 16px; border-radius: 2px; background: var(--pass); opacity: 0.85; }
.strip a.FAIL { background: var(--fail); opacity: 1; }
.strip a.UNKNOWN { background: var(--unknown); opacity: 1; }
.strip a.SKIP, .strip a.NOT { background: var(--idle); }
.foot { color: var(--muted); font-size: 12.5px; }
</style>
</head>
<body>
<div class="page" id="page"></div>
<script>
var DATA = %DATA%;
var LOG = "%LOG%";
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var NW = 236, NH = 52, GAP = 30, PAD = 14, COL = 48, LANE_W = 190, LANE_PAD = 16, DH = 64;

  function el(name, attrs, parent, text) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function secs(s) {
    if (s == null) return '';
    if (s < 1) return Math.round(s * 1000) + ' ms';
    if (s < 60) return (Math.round(s * 10) / 10) + ' s';
    var m = Math.floor(s / 60), sec = Math.round(s % 60);
    if (m < 60) return m + ' min ' + (sec < 10 ? '0' : '') + sec + ' s';
    var hh = Math.floor(m / 60); m = m % 60;
    return hh + ' h ' + (m < 10 ? '0' : '') + m + ' min';
  }
  function statusOf(st) {
    if (!st || !st.runs) return 'NONE';
    if (st.status.UNKNOWN) return 'UNKNOWN';
    if (st.status.FAIL) return 'FAIL';
    if (st.status.PASS) return 'PASS';
    if (st.status.SKIP) return 'SKIP';
    return 'NONE';
  }
  function logLink(id) { return LOG && id ? LOG + '#' + id : null; }
  function truncate(text, n) { return text && text.length > n ? text.slice(0, n - 1) + '…' : text; }

  // ---- layout: every step measures itself; containers hold their children
  function measureSeq(steps) {
    var w = NW, hh = 0;
    steps.forEach(function (s, i) { var m = measure(s); w = Math.max(w, m.w); hh += m.h + (i ? GAP : 0); });
    return { w: w, h: hh };
  }
  function measure(step) {
    if (step._m) return step._m;
    var m;
    if (step.kind === 'decision') {
      var y = measureSeq(step.yes), n = measureSeq(step.no);
      m = { w: y.w + n.w + COL, h: DH + GAP + Math.max(y.h, n.h) + GAP, yes: y, no: n };
    } else if (step.kind === 'loop' || step.kind === 'try') {
      var b = measureSeq(step.body), r = step.recovery ? measureSeq(step.recovery) : null;
      // A loop's head holds a bar per bound (cycles, time); one bar when it has none.
      var head = step.kind === 'loop' ? 82 + 13 * Math.max(0, (step.max_loops ? 1 : 0) + (step.max_seconds ? 1 : 0) - 1) : 44;
      m = { w: b.w + (r ? COL + r.w : 0) + 2 * PAD, h: head + Math.max(b.h, r ? r.h : 0) + 34 + PAD, body: b, rec: r, head: head };
    } else if (step.kind === 'flow' && step._collapsed) {
      m = { w: NW, h: NH, body: null };
    } else if (step.kind === 'flow') {
      var sb = measureSeq(step.steps);
      m = { w: Math.max(NW, sb.w + 2 * PAD), h: 44 + sb.h + PAD, body: sb };
    } else {
      m = { w: NW, h: NH };
    }
    step._m = m;
    return m;
  }

  // ---- drawing
  var svg, stats;
  function runs(step) { var st = stats[step.key]; return st ? st.runs : 0; }
  function edge(points, cls, label, labelAt, join) {
    var d = points.map(function (p, i) { return (i ? 'L' : 'M') + p[0] + ' ' + p[1]; }).join(' ');
    el('path', { 'class': 'edge ' + (cls || ''), d: d }, svg);
    var a = points[points.length - 2], b = points[points.length - 1];
    if (join) {
      // The edge flows into another one: a dot where they meet, the other edge's arrowhead serves both.
      el('circle', { 'class': 'join ' + (cls || ''), cx: b[0], cy: b[1], r: 3.5 }, svg);
    } else {
      var ang = Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
      el('path', { 'class': 'arrow ' + (cls || ''), d: 'M0 0 L-7 -3.5 L-7 3.5 Z', transform: 'translate(' + b[0] + ' ' + b[1] + ') rotate(' + ang + ')' }, svg);
    }
    if (label) el('text', { 'class': 'edge-label ' + (cls === 'cold' ? 'cold' : ''), x: labelAt[0], y: labelAt[1] }, svg, label);
  }
  function chip(g, cls, text, right, y) {
    var w = 12 + text.length * 6.8;
    var c = el('g', { 'class': 'chip ' + cls }, g);
    el('rect', { x: right - w, y: y, width: w, height: 17 }, c);
    el('text', { x: right - w / 2, y: y + 12.5, 'text-anchor': 'middle' }, c, text);
    return right - w - 5;
  }
  function chips(g, st, right, y) {
    if (!st || !st.runs) return chip(g, 'idle', 'not run', right, y);
    var s = st.status;
    if (s.UNKNOWN) right = chip(g, 'unknown', s.UNKNOWN + ' unknown', right, y);
    if (s.FAIL) right = chip(g, 'fail', s.FAIL + ' failed', right, y);
    if (s.SKIP) right = chip(g, 'skip', s.SKIP + ' skipped', right, y);
    if (s.PASS) right = chip(g, 'pass', s.PASS + (st.runs > 1 ? ' passed' : ' ok'), right, y);
    return right;
  }
  function group(step, x, y, w, hh, shape) {
    var st = stats[step.key];
    var g = el('g', { 'class': 'node s-' + statusOf(st), tabindex: 0, role: 'button', 'data-key': step.key,
                      'aria-label': step.label + ', ' + step.kind }, svg);
    el('rect', { 'class': 'halo', x: x - 4, y: y - 4, width: w + 8, height: hh + 8, rx: 9 }, g);
    el('rect', { 'class': shape, x: x, y: y, width: w, height: hh, rx: 6 }, g);
    g.addEventListener('click', function (ev) { ev.stopPropagation(); select(step.key); });
    g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); select(step.key); } });
    return g;
  }
  function drawSeq(steps, x, y, w) {
    var cy = y;
    steps.forEach(function (s, i) {
      var m = measure(s), sx = x + (w - m.w) / 2;
      if (i) {
        var n = runs(s), cls = n ? '' : 'cold';
        edge([[x + w / 2, cy - GAP], [x + w / 2, cy]], cls, '×' + n, [x + w / 2 + 8, cy - 8]);
      }
      draw(s, sx, cy, m.w);
      cy += m.h + GAP;
    });
  }
  function draw(step, x, y, w) {
    var m = measure(step), st = stats[step.key];
    if (step.kind === 'decision') {
      var cx = x + w / 2;
      var g = el('g', { 'class': 'node s-' + statusOf(st), tabindex: 0, role: 'button', 'data-key': step.key }, svg);
      el('polygon', { 'class': 'halo', points: [cx - 126, y + DH / 2, cx, y - 4, cx + 126, y + DH / 2, cx, y + DH + 4].join(' ') }, g);
      el('polygon', { 'class': 'box', points: [cx - 120, y + DH / 2, cx, y, cx + 120, y + DH / 2, cx, y + DH].join(' ') }, g);
      el('text', { 'class': 'kind', x: cx, y: y + DH / 2 - 8, 'text-anchor': 'middle' }, g, 'DECISION');
      el('text', { 'class': 'label', x: cx, y: y + DH / 2 + 10, 'text-anchor': 'middle' }, g, truncate(step.label, 26));
      g.addEventListener('click', function (ev) { ev.stopPropagation(); select(step.key); });
      g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); select(step.key); } });
      var yx = x, nx = x + m.yes.w + COL, top = y + DH + GAP, bottom = y + m.h;
      var e = (st && st.edges) || {};
      var yc = e.yes || 0, nc = e.no || 0;
      edge([[cx - 60, y + DH / 2 + 15], [yx + m.yes.w / 2, y + DH / 2 + 15], [yx + m.yes.w / 2, top]], yc ? '' : 'cold', 'yes ×' + yc, [yx + m.yes.w / 2 + 8, top - 8]);
      edge([[cx + 60, y + DH / 2 + 15], [nx + m.no.w / 2, y + DH / 2 + 15], [nx + m.no.w / 2, top]], nc ? '' : 'cold', 'no ×' + nc, [nx + m.no.w / 2 + 8, top - 8]);
      drawSeq(step.yes, yx, top, m.yes.w);
      drawSeq(step.no, nx, top, m.no.w);
      var joinY = bottom;
      el('path', { 'class': 'edge ' + (yc ? '' : 'cold'), d: 'M' + (yx + m.yes.w / 2) + ' ' + (top + m.yes.h) + ' V' + (joinY - 12) + ' H' + cx }, svg);
      el('path', { 'class': 'edge ' + (nc ? '' : 'cold'), d: 'M' + (nx + m.no.w / 2) + ' ' + (top + m.no.h) + ' V' + (joinY - 12) + ' H' + cx }, svg);
      el('circle', { 'class': 'join', cx: cx, cy: joinY - 12, r: 3.5 }, svg);
      el('path', { 'class': 'edge', d: 'M' + cx + ' ' + (joinY - 12) + ' V' + joinY }, svg);
      return;
    }
    if (step.kind === 'loop' || step.kind === 'try') {
      var g2 = group(step, x, y, w, m.h, 'container');
      el('text', { 'class': 'kind', x: x + 12, y: y + 17 }, g2, step.kind.toUpperCase());
      el('text', { 'class': 'label', x: x + 12, y: y + 35 }, g2, truncate(step.label, 30));
      chips(g2, st, x + w - 8, y + 8);
      if (step.kind === 'loop') {
        var it = st ? st.iterations.length : 0, done = st ? (st.edges.next || 0) : 0;
        var bars = [];
        var limit = (st && st.limit) || (isFinite(+step.max_loops) ? +step.max_loops : null);
        if (step.max_loops) bars.push([limit ? Math.min(1, done / limit) : 0, done + ' / ' + (limit || step.max_loops) + ' cycles']);
        var bound = step.max_seconds ? seconds(step.max_seconds) : NaN;
        if (step.max_seconds) bars.push([st && isFinite(bound) ? Math.min(1, st.elapsed / bound) : 0, secs(st ? st.elapsed : 0) + ' / ' + step.max_seconds]);
        if (!bars.length) bars.push([0, it + ' cycles']);
        bars.forEach(function (bar, i) {
          var by = y + 46 + i * 13;
          el('rect', { 'class': 'track', x: x + 12, y: by, width: 90, height: 5, rx: 2 }, g2);
          el('rect', { 'class': 'fill-a', x: x + 12, y: by, width: Math.max(bar[0] > 0 ? 4 : 0, 90 * bar[0]), height: 5, rx: 2 }, g2);
          el('text', { 'class': 'small', x: x + 110, y: by + 6 }, g2, bar[1]);
        });
      }
      var bx = x + PAD, by0 = y + m.head, e2 = (st && st.edges) || {};
      var bodyIn = step.kind === 'loop' ? e2.body || 0 : runs(step);
      edge([[bx + m.body.w / 2, by0 - 20], [bx + m.body.w / 2, by0]], bodyIn ? '' : 'cold', (step.kind === 'loop' ? 'body ' : '') + '×' + bodyIn, [bx + m.body.w / 2 + 8, by0 - 8]);
      drawSeq(step.body, bx, by0, m.body.w);
      var bodyEnd = by0 + m.body.h, foot = y + m.h - PAD - 10;
      if (step.kind === 'loop') {
        var nc2 = e2.next || 0;
        // Down to the foot, up the left side and into the body arrow: one arrowhead for both.
        edge([[bx + m.body.w / 2, bodyEnd], [bx + m.body.w / 2, foot], [bx - 2, foot], [bx - 2, by0 - 20], [bx + m.body.w / 2, by0 - 20]], nc2 ? '' : 'cold', 'next ×' + nc2, [bx + 8, foot - 6], true);
      } else {
        edge([[bx + m.body.w / 2, bodyEnd], [bx + m.body.w / 2, foot]], runs(step) ? '' : 'cold', 'done ×' + (e2.done || 0), [bx + m.body.w / 2 + 8, foot - 4]);
      }
      if (m.rec) {
        var rx = bx + m.body.w + COL, f = e2.on_failure || 0;
        // Out of the body's side, up the gap between the columns and down into the recovery box from above.
        var gx = bx + m.body.w + COL / 2;
        // It leaves from the first step of the body, which may be narrower than the body column.
        var f0 = measure(step.body[0]), fx = bx + (m.body.w - f0.w) / 2 + f0.w, fy = by0 + (step.body[0].kind === 'decision' ? DH / 2 : 24);
        edge([[fx, fy], [gx, fy], [gx, by0 - 16], [rx + m.rec.w / 2, by0 - 16], [rx + m.rec.w / 2, by0]], f ? 'fail' : 'cold', 'on failure ×' + f, [gx + 6, by0 - 20]);
        drawSeq(step.recovery, rx, by0, m.rec.w);
        var thenCount = step.then === 'abort' ? (e2.abort || 0) : (e2.continue || 0), rcx = rx + m.rec.w / 2;
        if (step.then === 'abort') {
          // The error is raised again after the recovery: the path leaves through the bottom edge.
          edge([[rcx, by0 + m.rec.h], [rcx, y + m.h]], thenCount ? 'fail' : 'cold', 'abort ×' + thenCount, [rcx + 8, foot - 8]);
        } else {
          // Back into the plan: joins the line at the foot (a loop's next, a try's done).
          edge([[rcx, by0 + m.rec.h], [rcx, foot], [bx + m.body.w / 2, foot]], thenCount ? 'rec' : 'cold', 'continue ×' + thenCount, [rcx + 8, foot - 8], true);
        }
      }
      if (step.kind === 'loop') {
        var dn = e2.done || 0;
        el('text', { 'class': 'edge-label ' + (dn ? '' : 'cold'), x: x + w - 8, y: y + m.h - 6, 'text-anchor': 'end' }, svg, 'done ×' + dn);
      }
      return;
    }
    if (step.kind === 'flow') {
      var g3 = group(step, x, y, w, m.h, 'container'), open = !step._collapsed;
      var tg = el('g', { 'class': 'toggle', role: 'button', tabindex: 0,
                         'aria-label': (open ? 'Collapse ' : 'Expand ') + step.label, 'aria-expanded': String(open) }, g3);
      el('rect', { x: x + 8, y: y + 8, width: 16, height: 16, rx: 4 }, tg);
      el('text', { x: x + 16, y: y + 20, 'text-anchor': 'middle' }, tg, open ? '\u2212' : '+');
      tg.addEventListener('click', function (ev) { ev.stopPropagation(); toggle(step); });
      tg.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ev.stopPropagation(); toggle(step); } });
      el('text', { 'class': 'kind', x: x + 32, y: y + 17 }, g3, 'SUB-FLOW' + (open ? '' : ' \u00b7 ' + countSteps(step.steps) + (countSteps(step.steps) === 1 ? ' step' : ' steps')));
      el('text', { 'class': 'label', x: x + 32, y: y + 35 }, g3, truncate(step.label, 26));
      chips(g3, st, x + w - 8, y + 8);
      if (open) drawSeq(step.steps, x + (w - m.body.w) / 2, y + 44, m.body.w);
      return;
    }
    var g4 = group(step, x, y, w, NH, 'box');
    el('text', { 'class': 'kind', x: x + 12, y: y + 17 }, g4, step.kind.toUpperCase());
    el('text', { 'class': 'label', x: x + 12, y: y + 36 }, g4, truncate(step.label, 28));
    chips(g4, st, x + w - 8, y + 8);
  }
  function seconds(text) {
    var m = /^\s*([\d.]+)\s*([a-z]*)/i.exec(String(text));
    if (!m) return 0;
    var n = parseFloat(m[1]), u = m[2].toLowerCase();
    var f = { ms: 0.001, millisecond: 0.001, milliseconds: 0.001, s: 1, sec: 1, second: 1, seconds: 1, m: 60, min: 60, minute: 60, minutes: 60, h: 3600, hour: 3600, hours: 3600, d: 86400, day: 86400, days: 86400 };
    return isFinite(n) ? n * (f[u] || 1) : NaN;
  }

  // ---- a flow: summary, lanes, detail
  var selected = null, detail, flowData;
  function renderFlow(flow, page) {
    flowData = flow; stats = flow.stats;
    var s = flow.suite, head = h('div', 'head');
    var what = h('div', 'what');
    what.appendChild(h('span', 'eyebrow', 'Flow report · ' + flow.source.split(/[\\/]/).pop()));
    what.appendChild(h('h1', null, flow.name));
    var stopped = flow.lanes.map(function (l) { return l.stopped; }).filter(Boolean)[0];
    what.appendChild(h('p', 'sub', stopped ? stopped + '. ' : '' + 'The plan as it was drawn, with what this run did on each node. Select a node for its record.'));
    head.appendChild(what);
    head.appendChild(h('span', 'verdict v-' + s.status, s.status + (stopped ? ' · stopped' : '')));
    page.appendChild(head);

    var facts = h('div', 'facts');
    function fact(b, t) { var f = h('div', 'fact'); f.appendChild(h('b', null, b)); f.appendChild(h('span', null, t)); facts.appendChild(f); }
    var loops = []; (function find(steps) { steps.forEach(function (st) { if (st.kind === 'loop') loops.push(st); ['body', 'recovery', 'yes', 'no', 'steps'].forEach(function (k) { if (st[k]) find(st[k]); }); }); })(flow.lanes.length ? [].concat.apply([], flow.plan.map(function (l) { return l.steps; })) : []);
    loops.forEach(function (loop) {
      var st = stats[loop.key]; if (!st) return;
      var lim = st.limit || (isFinite(+loop.max_loops) ? +loop.max_loops : loop.max_loops);
      fact((st.edges.next || 0) + (lim ? ' of ' + lim : ''), 'cycles of ' + loop.label + ' completed');
      var failed = st.iterations.filter(function (i) { return i.status !== 'PASS'; }).length;
      if (failed) fact(String(failed), 'cycles of ' + loop.label + ' did not pass');
    });
    fact(secs(s.elapsed), 'run time');
    fact(s.tests.passed + ' · ' + s.tests.failed + ' · ' + s.tests.unknown, 'phases passed · failed · unknown');
    if (flow.checkpoint && flow.checkpoint.matches) {
      var r = flow.checkpoint.remaining || {};
      var left = [];
      if (r.max_loops != null) left.push(r.max_loops + ' cycles');
      if (r.max_seconds != null) left.push(secs(r.max_seconds));
      fact(left.join(', ') || 'yes', 'left for a restart from the checkpoint');
    }
    page.appendChild(facts);

    var work = h('div', 'work'), card = h('section', 'card'), hd = h('header');
    hd.appendChild(h('h2', null, 'The plan and this run'));
    var legend = h('div', 'legend');
    [['pass', 'passed'], ['fail', 'failed'], ['skip', 'recovery path'], ['unknown', 'unknown'], ['idle', 'not reached']].forEach(function (p) {
      var sp = h('span'); var i = h('i'); i.style.background = 'var(--' + p[0] + ')'; sp.appendChild(i); sp.appendChild(document.createTextNode(p[1])); legend.appendChild(sp);
    });
    hd.appendChild(legend);
    if (subflows().length) {
      var tools = h('div', 'tools');
      [['Expand all', false], ['Collapse all', true]].forEach(function (p) {
        var b = h('button', 'btn', p[0]); b.type = 'button';
        b.addEventListener('click', function () { setAll(p[1]); });
        tools.appendChild(b);
      });
      hd.appendChild(tools);
    }
    card.appendChild(hd);
    var scroll = h('div', 'scroll');
    svg = el('svg', { 'class': 'plan', role: 'group', 'aria-label': 'Flow diagram with run results' });
    scroll.appendChild(svg); card.appendChild(scroll); work.appendChild(card);

    detail = h('aside', 'card detail'); detail.setAttribute('aria-live', 'polite');
    var dh = h('header'); dh.appendChild(h('span', 'eyebrow', 'node')); dh.appendChild(h('span', 'mono', '')); detail.appendChild(dh);
    detail.appendChild(h('div', 'body')); work.appendChild(detail);
    page.appendChild(work);

    // Sub-flows start folded, except those with a failure or an unknown inside.
    subflows().forEach(function (s) { s._collapsed = !troubled(s.steps); });
    drawPlan();

    var first = Object.keys(stats).filter(function (k) { return stats[k].status.FAIL || stats[k].status.UNKNOWN; })[0]
             || Object.keys(stats).filter(function (k) { return k.indexOf('phase:') !== 0 && stats[k].runs; })[0];
    if (first) select(first);
    else detail.querySelector('.body').appendChild(h('p', 'hint', 'No node of this plan ran.'));
  }

  // ---- the plan: lanes and their steps (drawn again when a sub-flow is toggled)
  function drawPlan() {
    var flow = flowData;
    svg.textContent = '';
    var contentW = 0, lanes = flow.plan.map(function (lane) { var m = measureSeq(lane.steps); contentW = Math.max(contentW, m.w); return m; });
    var y = 8, laneInfo = {};
    flow.lanes.forEach(function (l) { laneInfo[l.role === 'test' ? l.name : l.role] = l; });
    var width = LANE_W + contentW + 2 * LANE_PAD + 16;
    flow.plan.forEach(function (lane, i) {
      var m = lanes[i], hh = Math.max(96, m.h + 2 * LANE_PAD + 8);
      var info = laneInfo[lane.role === 'test' ? lane.name : lane.role] || {};
      el('rect', { 'class': 'lane', x: 8, y: y, width: width - 16, height: hh, rx: 6 }, svg);
      el('text', { 'class': 'lane-role', x: 24, y: y + 24 }, svg, lane.role.toUpperCase());
      var title = el('text', { 'class': 'lane-title', x: 24, y: y + 44 }, svg, truncate(lane.name, 20));
      if (lane.key) { title.style.cursor = 'pointer'; title.addEventListener('click', function () { select(lane.key, info); }); }
      var note = info.status ? [info.status.toLowerCase() + (info.elapsed != null ? ' · ' + secs(info.elapsed) : '')] : ['not run'];
      if (info.stopped) note.push(truncate(info.stopped, 28));
      note.forEach(function (line, j) {
        var t = el('text', { 'class': 'lane-note', x: 24, y: y + 64 + j * 15 }, svg, line);
        if (j === 0 && info.status) t.setAttribute('style', 'fill: var(--' + info.status.toLowerCase().replace(' ', '') + '); font-weight: 600');
      });
      drawSeq(lane.steps, LANE_W + LANE_PAD, y + LANE_PAD + 4, contentW);
      y += hh + 10;
    });
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + (y + 2));
    // Fit the card: scale down to 60% of the natural size at most, scroll beyond that; never scale up.
    svg.style.maxWidth = width + 'px';
    svg.style.minWidth = Math.round(width * 0.6) + 'px';
    svg.setAttribute('width', width); svg.setAttribute('height', y + 2);
  }
  function allSteps() { return [].concat.apply([], flowData.plan.map(function (l) { return l.steps; })); }
  function walkSteps(steps, fn) { steps.forEach(function (s) { fn(s); ['body', 'recovery', 'yes', 'no', 'steps'].forEach(function (k) { if (s[k]) walkSteps(s[k], fn); }); }); }
  function subflows() { var out = []; walkSteps(allSteps(), function (s) { if (s.kind === 'flow') out.push(s); }); return out; }
  function countSteps(steps) { var n = 0; walkSteps(steps, function (s) { if (s.kind !== 'flow') n++; }); return n; }
  function troubled(steps) { var t = false; walkSteps(steps, function (s) { var st = stats[s.key]; if (st && (st.status.FAIL || st.status.UNKNOWN)) t = true; }); return t; }
  function toggle(step, collapsed) { step._collapsed = collapsed === undefined ? !step._collapsed : collapsed; redraw(); }
  function setAll(collapsed) { subflows().forEach(function (s) { s._collapsed = collapsed; }); redraw(); }
  function redraw() {
    walkSteps(allSteps(), function (s) { delete s._m; });
    drawPlan();
    if (selected) svg.querySelectorAll('.node').forEach(function (g) { g.classList.toggle('sel', g.getAttribute('data-key') === selected); });
  }
  function findStep(key) {
    var found = null;
    (function walk(steps) { steps.forEach(function (st) { if (st.key === key) found = st; ['body', 'recovery', 'yes', 'no', 'steps'].forEach(function (k) { if (st[k]) walk(st[k]); }); }); })([].concat.apply([], flowData.plan.map(function (l) { return l.steps; })));
    return found;
  }
  function select(key, lane) {
    selected = key;
    svg.querySelectorAll('.node').forEach(function (g) { g.classList.toggle('sel', g.getAttribute('data-key') === key); });
    var st = stats[key], step = findStep(key), body = detail.querySelector('.body');
    detail.querySelector('header .eyebrow').textContent = step ? step.kind : (lane ? lane.role : 'node');
    detail.querySelector('header .mono').textContent = step ? step.id : key.replace('phase:', '');
    body.textContent = '';
    body.appendChild(h('h2', null, step ? step.label : (lane ? lane.name : key)));
    if (step && step.detail) body.appendChild(h('p', 'mono hint', step.detail));
    var counts = h('div', 'counts');
    if (!st || !st.runs) counts.appendChild(h('span', 'count idle', 'not run'));
    else {
      if (st.status.PASS) counts.appendChild(h('span', 'count pass', st.status.PASS + ' passed'));
      if (st.status.FAIL) counts.appendChild(h('span', 'count fail', st.status.FAIL + ' failed'));
      if (st.status.UNKNOWN) counts.appendChild(h('span', 'count unknown', st.status.UNKNOWN + ' unknown'));
      if (st.status.SKIP) counts.appendChild(h('span', 'count skip', st.status.SKIP + ' skipped'));
    }
    body.appendChild(counts);
    var kv = h('dl', 'kv');
    function row(k, v) { if (v == null || v === '') return; kv.appendChild(h('dt', null, k)); kv.appendChild(h('dd', null, v)); }
    if (lane) { row('Status', lane.status); row('Time', secs(lane.elapsed)); if (lane.message) row('Message', truncate(lane.message, 300)); }
    if (st && st.runs) {
      row('Total time', secs(st.elapsed));
      if (st.runs > 1) { row('Average', secs(st.elapsed / st.runs)); row('Slowest', secs(st.max)); }
      if (st.attempts.length) {
        var maxA = Math.max.apply(null, st.attempts), avgA = st.attempts.reduce(function (a, b) { return a + b; }, 0) / st.attempts.length;
        row('Attempts', st.attempts.length > 1 ? 'up to ' + maxA + ', on average ' + (Math.round(avgA * 10) / 10) : String(maxA));
      }
      if (step && step.kind === 'loop') {
        row('Cycles', st.iterations.length + ' started, ' + (st.edges.next || 0) + ' completed');
        if (step.max_loops) row('Limit', st && st.limit && String(st.limit) !== String(step.max_loops) ? st.limit + ' cycles (' + step.max_loops + ')' : step.max_loops + ' cycles');
        if (step.max_seconds) row('Deadline', step.max_seconds);
        if (step.every) row('Every', step.every);
        if (st.edges.on_failure) row('Recovered', (st.edges.continue || 0) + ' of ' + st.edges.on_failure + ' failures');
      }
      if (step && step.kind === 'try') row('Recovered', (st.edges.continue || 0) + ' of ' + (st.edges.on_failure || 0) + ' failures');
      if (step && step.kind === 'decision') row('Branches', 'yes ' + (st.edges.yes || 0) + ', no ' + (st.edges.no || 0));
    }
    if (step && step.kind === 'gate') { row('Timeout', step.timeout + ', poll every ' + step.interval); row('On timeout', step.on_timeout); }
    if (kv.children.length) body.appendChild(kv);
    if (st && st.last_error) { body.appendChild(h('span', 'eyebrow', 'Last error')); body.appendChild(h('div', 'err', st.last_error)); }
    if (st && st.iterations.length) {
      body.appendChild(h('span', 'eyebrow', 'Cycles, one cell each'));
      var strip = h('div', 'strip');
      st.iterations.forEach(function (it, i) {
        var a = h('a', it.status.split(' ')[0]); a.title = 'Cycle ' + (i + 1) + ': ' + it.status.toLowerCase() + ', ' + secs(it.elapsed);
        var link = logLink(it.id); if (link) a.href = link;
        strip.appendChild(a);
      });
      body.appendChild(strip);
    }
    if (st && LOG) {
      var jumps = h('div', 'jumps'), any = false;
      function jump(text, id) { if (!id) return; any = true; var line = h('div', 'jump'); var a = h('a', null, text); a.href = logLink(id); line.appendChild(a); line.appendChild(h('code', 'hint', '#' + id)); jumps.appendChild(line); }
      jump('First failure', st.first_fail);
      if (st.last_fail && st.last_fail !== st.first_fail) jump('Last failure', st.last_fail);
      if (st.max_id && st.runs > 1) jump('Slowest run', st.max_id);
      jump(st.runs > 1 ? 'Last run' : 'In the log', st.last);
      if (any) { body.appendChild(h('span', 'eyebrow', 'Open in log.html')); body.appendChild(jumps); }
    }
    if (flowData.checkpoint && step && step.kind === 'loop' && flowData.checkpoint.position && flowData.checkpoint.position.loop === step.id) {
      var r = flowData.checkpoint.remaining || {};
      body.appendChild(h('p', 'hint', 'A restart continues this loop with ' + [r.max_loops != null ? r.max_loops + ' cycles' : '', r.max_seconds != null ? secs(r.max_seconds) : ''].filter(Boolean).join(' and ') + ' left (checkpoint saved ' + flowData.checkpoint.saved + ').'));
    }
  }

  var page = document.getElementById('page');
  DATA.flows.forEach(function (flow) { renderFlow(flow, page); });
  var foot = h('p', 'foot', 'Generated ' + DATA.generated + ' from ' + DATA.output + (LOG ? '. Links open ' + LOG + ' at the keyword.' : '.'));
  page.appendChild(foot);
})();
</script>
</body>
</html>
'''
