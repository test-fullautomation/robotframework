#  cuongnht add thread
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

"""Generate a per-thread timeline view from a (merged) Robot Framework output.xml.

log.html shows each THREAD as one collapsible block, which hides the real
interleaving between threads. This module complements it: one horizontal lane
per thread (plus MainThread), keywords drawn as bars on a common time axis,
so the true parallel execution is visible. Bars link to the corresponding
element in log.html (same directory by default).

Usage as a command line tool::

    python -m robot.timeline path/to/output.xml [-o timeline.html] [--log log.html]

Usage programmatically::

    from robot.timeline import generate_timeline

    generate_timeline('output.xml')                       # -> timeline.html
    generate_timeline('output.xml', 'custom.html')

The generated HTML is fully self-contained.
"""

import argparse
import os
from datetime import datetime
from html import escape
from xml.etree import ElementTree as ET

TS_FORMAT = '%Y%m%d %H:%M:%S.%f'
BODY_TAGS = ('kw', 'for', 'while', 'if', 'try', 'error', 'return', 'break',
             'continue')
STATUS_COLORS = {'PASS': '#4caf50', 'FAIL': '#e53935', 'SKIP': '#fbc02d',
                 'NOT RUN': '#9e9e9e', 'UNKNOWN': '#7e57c2'}


def parse_ts(value):
    try:
        return datetime.strptime(value, TS_FORMAT)
    except (TypeError, ValueError):
        return None


def get_span(elem):
    """Return (start, end, status) from the element's <status> child."""
    status = elem.find('status')
    if status is None:
        return None
    start = parse_ts(status.get('starttime'))
    end = parse_ts(status.get('endtime'))
    if start is None or end is None:
        return None
    return start, end, status.get('status', 'PASS')


def label_for(elem):
    if elem.tag == 'kw':
        lib = elem.get('library')
        name = elem.get('name', '')
        return f'{lib}.{name}' if lib else name
    return elem.tag.upper()


def iter_body(parent, parent_id):
    """Yield ``(element, log_html_id)`` for body items of ``parent``.

    Mirrors how log.html numbers elements (testdata.js): non-message body
    items are ``k1..kn`` in document order, IF/TRY roots are flattened so
    their branches take the positions, and messages consume no numbers.
    """
    k = 0
    for child in parent:
        if child.tag in ('if', 'try'):
            for branch in child:
                if branch.tag == 'branch':
                    k += 1
                    yield branch, f'{parent_id}-k{k}'
        elif child.tag in BODY_TAGS or child.tag in ('thread', 'iter', 'branch'):
            k += 1
            yield child, f'{parent_id}-k{k}'


def collect_test(test_elem):
    """Return {lane_name: {'span': bar|None, 'bars': [bar, ...]}}.

    Each bar carries the log.html element id so the timeline can link to it.
    """
    lanes = {'MainThread': {'span': None, 'bars': []}}

    def make_bar(elem, elem_id, label=None):
        span = get_span(elem)
        if not span:
            return None
        start, end, status = span
        return {'label': label or label_for(elem), 'start': start, 'end': end,
                'status': status, 'id': elem_id}

    def process(parent, parent_id, lane, draw):
        for child, child_id in iter_body(parent, parent_id):
            if child.tag == 'thread':
                name = child.get('name', 'THREAD')
                lanes.setdefault(name, {'span': None, 'bars': []})
                span_bar = make_bar(child, child_id, f'THREAD {name}')
                if span_bar and lanes[name]['span'] is None:
                    lanes[name]['span'] = span_bar
                # Thread children are drawn on the thread's own lane.
                process(child, child_id, name, draw=True)
            else:
                if draw:
                    bar = make_bar(child, child_id)
                    if bar:
                        lanes[lane]['bars'].append(bar)
                # Recurse without drawing nested keywords (keeps the picture
                # readable) but keep numbering so nested THREADs get correct
                # ids and their own lanes.
                process(child, child_id, lane, draw=False)

    process(test_elem, test_elem.get('id', 't'), 'MainThread', draw=True)
    return {name: data for name, data in lanes.items()
            if data['bars'] or data['span']}


def build_tests(root):
    tests = []
    for test in root.iter('test'):
        lanes = collect_test(test)
        if not lanes:
            continue
        all_bars = [b for lane in lanes.values()
                    for b in lane['bars'] + ([lane['span']] if lane['span'] else [])]
        t0 = min(b['start'] for b in all_bars)
        t1 = max(b['end'] for b in all_bars)
        # Suite-scoped THREADs may keep running after the test has ended;
        # remember the test's own end time so it can be marked on the chart.
        status = test.find('status')
        test_end = parse_ts(status.get('endtime')) if status is not None else None
        tests.append({'name': test.get('name', ''), 'lanes': lanes,
                      't0': t0, 't1': t1, 'test_end': test_end})
    return tests


def render(tests, source, log_file='log.html'):
    out = [HTML_HEAD.replace('%SOURCE%', escape(source))]
    for test in tests:
        t0, t1 = test['t0'], test['t1']
        total = max((t1 - t0).total_seconds(), 0.001)

        def pos(dt):
            return 100.0 * (dt - t0).total_seconds() / total

        out.append(f'<section><h2>{escape(test["name"])}</h2>')
        out.append(f'<div class="meta">{t0.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}'
                   f' &rarr; {t1.strftime("%H:%M:%S.%f")[:-3]}'
                   f' &nbsp;({total:.3f} s)</div>')
        out.append('<div class="chart">')
        test_end = test.get('test_end')
        if test_end and (t1 - test_end).total_seconds() > 0.05:
            # Threads keep running after the test ended: mark the boundary.
            out.append(f'<div class="testend" style="left:calc(142px + '
                       f'(100% - 154px) * {pos(test_end) / 100.0:.4f})" '
                       f'title="test ended {test_end.strftime("%H:%M:%S.%f")[:-3]}'
                       f' — threads to the right kept running"></div>')
        for lane_name, lane in test['lanes'].items():
            out.append('<div class="row">')
            out.append(f'<div class="lane">{escape(lane_name)}</div>')
            out.append('<div class="track">')
            span = lane['span']
            if span:
                left, width = pos(span['start']), max(pos(span['end']) - pos(span['start']), 0.2)
                href = f'{log_file}#{span["id"]}' if log_file else None
                link = f' href="{escape(href)}" target="rflog"' if href else ''
                out.append(f'<a class="span" style="left:{left:.2f}%;width:{width:.2f}%"'
                           f' title="{escape(span["label"])} [{span["status"]}]'
                           f' &ndash; click to open in log.html"{link}></a>')
            for bar in lane['bars']:
                left = pos(bar['start'])
                width = max(pos(bar['end']) - left, 0.4)
                color = STATUS_COLORS.get(bar['status'], '#9e9e9e')
                dur = (bar['end'] - bar['start']).total_seconds()
                tip = (f'{bar["label"]}  [{bar["status"]}]\n'
                       f'{bar["start"].strftime("%H:%M:%S.%f")[:-3]} - '
                       f'{bar["end"].strftime("%H:%M:%S.%f")[:-3]}  ({dur:.3f} s)'
                       '\nclick to open in log.html')
                text = escape(bar['label']) if width > 7 else ''
                href = f'{log_file}#{bar["id"]}' if log_file else None
                link = f' href="{escape(href)}" target="rflog"' if href else ''
                out.append(f'<a class="bar" style="left:{left:.2f}%;width:{width:.2f}%;'
                           f'background:{color}" title="{escape(tip)}"{link}>{text}</a>')
            out.append('</div></div>')
        # time axis
        out.append('<div class="row"><div class="lane"></div><div class="axis">')
        step = nice_step(total)
        t = 0.0
        while t <= total + 1e-9:
            out.append(f'<div class="tick" style="left:{100.0 * t / total:.2f}%">'
                       f'+{t:g}s</div>')
            t += step
        out.append('</div></div>')
        out.append('</div></section>')
    out.append(HTML_FOOT)
    return '\n'.join(out)


def nice_step(total):
    for step in (0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800,
                 3600, 7200, 14400):
        if total / step <= 10:
            return step
    return total / 10


HTML_HEAD = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Thread Timeline</title>
<style>
 body {font-family: 'Segoe UI', Helvetica, Arial, sans-serif; margin: 20px;
       background: #fafafa; color: #222;}
 h1 {font-size: 20px;} h2 {font-size: 16px; margin: 24px 0 2px;}
 .meta {color: #777; font-size: 12px; margin-bottom: 8px;}
 .chart {border: 1px solid #ddd; background: #fff; padding: 8px 12px 22px;
         border-radius: 6px;}
 .row {display: flex; align-items: center; min-height: 26px; position: relative;}
 .lane {flex: 0 0 130px; font-size: 12px; font-weight: 600; color: #444;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;}
 .track {position: relative; flex: 1; height: 22px; margin: 2px 0;
         background: #f4f4f4; border-radius: 3px;}
 .span {position: absolute; top: 0; bottom: 0; background: #90caf9;
        opacity: .35; border-radius: 3px; display: block;}
 .bar {position: absolute; top: 2px; bottom: 2px; border-radius: 3px;
       color: #fff; font-size: 10px; line-height: 18px; padding: 0 4px;
       overflow: hidden; white-space: nowrap; box-sizing: border-box;
       cursor: pointer; display: block; text-decoration: none;}
 .bar:hover {filter: brightness(1.15); outline: 1px solid #333;}
 .span:hover {opacity: .55;}
 .axis {position: relative; flex: 1; height: 16px;}
 .chart {position: relative;}
 .testend {position: absolute; top: 8px; bottom: 22px; width: 0;
           border-left: 2px dashed #e53935; z-index: 2;}
 .testend::after {content: 'test end'; position: absolute; top: -6px; left: 4px;
                  font-size: 10px; color: #e53935; white-space: nowrap;}
 .tick {position: absolute; top: 0; font-size: 10px; color: #999;
        border-left: 1px solid #ccc; padding-left: 3px; height: 14px;}
 .legend {font-size: 11px; color: #555; margin: 6px 0 0;}
 .legend span {display: inline-block; width: 10px; height: 10px;
               border-radius: 2px; margin: 0 4px 0 12px; vertical-align: -1px;}
</style></head><body>
<h1>Thread Timeline &mdash; %SOURCE%</h1>
<div class="legend">Status:
 <span style="background:#4caf50"></span>PASS
 <span style="background:#e53935"></span>FAIL
 <span style="background:#fbc02d"></span>SKIP
 <span style="background:#9e9e9e"></span>NOT RUN
 <span style="background:#7e57c2"></span>UNKNOWN
 <span style="background:#90caf9;opacity:.5"></span>thread lifetime
</div>"""

HTML_FOOT = """<script>
// Append a changing '~<n>' suffix on every click so the hash always differs
// and log.html (which strips the suffix) re-navigates even when the same bar
// is clicked twice. Plain href stays clean for middle-click / open-in-new-tab.
(function () {
    var n = 0;
    document.addEventListener('click', function (e) {
        var a = e.target.closest('a.bar, a.span');
        if (!a || !a.hash) return;
        e.preventDefault();
        window.open(a.href + '~' + (++n), 'rflog');
    });
})();
</script>
</body></html>"""


def generate_timeline(output, outfile=None, log='log.html'):
    """Generate a timeline HTML file from an output.xml file.

    :param output: Path to a (merged) output.xml file.
    :param outfile: Target HTML file. Defaults to ``timeline.html`` next to
        the input.
    :param log: Log file the bars link to, relative to the timeline. Use
        an empty value to disable linking.
    :return: Tuple ``(path of the generated file, number of tests)``.
    """
    root = ET.parse(output).getroot()
    tests = build_tests(root)
    outfile = outfile or os.path.join(os.path.dirname(os.path.abspath(output)),
                                      'timeline.html')
    with open(outfile, 'w', encoding='utf-8') as f:
        f.write(render(tests, os.path.basename(output), log))
    return outfile, len(tests)


def timeline_cli(argv=None):
    parser = argparse.ArgumentParser(prog='python -m robot.timeline',
                                     description=__doc__.splitlines()[0])
    parser.add_argument('output', help='Path to (merged) output.xml')
    parser.add_argument('-o', '--outfile', default=None,
                        help='Target HTML file (default: timeline.html next '
                             'to the input)')
    parser.add_argument('--log', default='log.html',
                        help='Log file the bars link to, relative to the '
                             'timeline (default: log.html). Use --log= to '
                             'disable linking.')
    args = parser.parse_args(argv)
    outfile, count = generate_timeline(args.output, args.outfile, args.log)
    print(f'Timeline: {outfile}  ({count} test(s))')


if __name__ == '__main__':
    timeline_cli()
