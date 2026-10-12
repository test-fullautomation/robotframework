"""The tests (a flow's phases) of an output.xml, for the extension's Runs view.

    python run_summary.py <output.xml>   -> JSON on stdout

Streams the file with iterparse and keeps only the test elements, so the
output of a long run (hundreds of megabytes) is read in bounded memory.
Needs no Robot Framework: any interpreter will do. A file without an end
(the run is on, or died) gives what was read so far, with "partial": true.
"""
import json
import sys
import xml.etree.ElementTree as ET

# Robot 7 writes start/elapsed on <status>, Robot 6 starttime/endtime.
CONTAINERS = {'kw', 'for', 'iter', 'while', 'if', 'branch', 'try', 'group', 'thread',
              'return', 'break', 'continue', 'error', 'var', 'msg', 'doc', 'arg', 'tag',
              'meta', 'tags', 'args'}


def _times(status):
    if status is None:
        return None, None
    start = status.get('start') or status.get('starttime') or None
    if start == 'N/A':
        start = None
    if status.get('elapsed') is not None:
        try:
            return start, float(status.get('elapsed'))
        except ValueError:
            return start, None
    end = status.get('endtime')
    if start and end and end != 'N/A':
        try:
            from datetime import datetime
            fmt = '%Y%m%d %H:%M:%S.%f'
            return start, (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds()
        except ValueError:
            pass
    return start, None


def summarize(path):
    tests = []
    suite = None
    depth = 0
    partial = False
    total = None
    in_statistics = False   # <statistics> has <suite> elements of its own
    try:
        for event, elem in ET.iterparse(path, events=('start', 'end')):
            tag = elem.tag
            if event == 'start':
                if tag == 'statistics':
                    in_statistics = True
                elif tag == 'suite' and not in_statistics:
                    if depth == 0:
                        suite = {'name': elem.get('name', ''), 'source': elem.get('source', ''),
                                 'status': None, 'start': None, 'elapsed': None}
                    depth += 1
                continue
            if in_statistics and tag != 'total':
                if tag == 'statistics':
                    in_statistics = False
                    elem.clear()
                continue
            if tag == 'test':
                status = elem.find('status')
                start, elapsed = _times(status)
                tests.append({
                    'id': elem.get('id', ''),
                    'name': elem.get('name', ''),
                    'status': status.get('status', '') if status is not None else '',
                    'start': start,
                    'elapsed': elapsed,
                    'message': (status.text or '').strip() if status is not None else '',
                })
                elem.clear()
            elif tag == 'suite':
                depth -= 1
                if depth == 0 and suite is not None:
                    status = elem.find('status')
                    if status is not None:
                        suite['status'] = status.get('status')
                        suite['start'], suite['elapsed'] = _times(status)
                elem.clear()
            elif tag == 'total':
                stat = elem.find('stat')
                if stat is not None:
                    total = {k: int(stat.get(k, 0) or 0) for k in ('pass', 'fail', 'skip', 'unknown')}
                elem.clear()
            elif tag in CONTAINERS:
                elem.clear()
    except ET.ParseError:
        partial = True
    except OSError as e:
        return {'ok': False, 'error': str(e)}
    if suite is None:
        return {'ok': False, 'error': 'Not an output.xml: no suite in it.', 'partial': partial}
    if total is None:
        partial = True
    return {'ok': True, 'suite': suite, 'tests': tests, 'total': total, 'partial': partial}


def main(argv):
    if len(argv) != 2:
        print(json.dumps({'ok': False, 'error': 'usage: run_summary.py <output.xml>'}))
        return 2
    sys.stdout.write(json.dumps(summarize(argv[1])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
