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

"""Merge per-thread output XML files into the main output.xml.

Each ``THREAD`` block writes its body into ``output_<ThreadName>.xml`` while
the main ``output.xml`` only contains an empty ``<thread name="...">``
placeholder written when the thread was started. This module grafts the
thread files into their placeholders so that a single, complete ``output.xml``
remains. After merging, ``log.html`` shows each thread as one collapsible
block (like a FOR loop) at the position where the thread was started, with
the real timestamps of everything the thread executed.

Usage from code (done automatically in ``Output.close``)::

    merge_thread_outputs('output.xml', {'worker1': 'output_worker1.xml'})

Usage from the command line, e.g. before running ``rebot`` manually::

    python -m robot.output.threadmerger output.xml
"""

import glob
import os
import re
from xml.etree import ElementTree as ET

from .segmentmerger import merge_segments


def merge_thread_outputs(output_path, thread_files=None, remove=True, logger=None):
    """Graft per-thread output files into ``<thread>`` placeholders.

    :param output_path: Path to the main output.xml file.
    :param thread_files: Mapping ``{thread_name: path}``. If not given, files
        matching ``<base>_<name><ext>`` next to ``output_path`` whose root
        element is ``<thread>`` are discovered automatically.
    :param remove: Remove thread files that were merged successfully.
    :param logger: Object with ``info``/``warn``/``error`` methods. Defaults
        to the global robot LOGGER.
    :return: Number of placeholders that were filled.
    """
    if logger is None:
        from .logger import LOGGER as logger
    if thread_files is None:
        thread_files = _discover_thread_files(output_path)
    if not thread_files:
        return 0
    tree = ET.parse(output_path)
    placeholders = _find_placeholders(tree.getroot())
    merged = 0
    used_files = set()
    for name, elements in placeholders.items():
        path = thread_files.get(name)
        if not path or not os.path.isfile(path):
            logger.warn(f"No output file found for thread '{name}'.")
            continue
        if len(elements) > 1:
            # Same thread name used more than once: only the last run survives
            # on disk (earlier files were overwritten), so attach to the last
            # placeholder and leave the earlier ones as they are.
            logger.warn(f"Multiple THREAD blocks use name '{name}'. Thread log "
                        f"is attached only to the last occurrence.")
        # cuongnht add segmented output: long living threads may have been
        # rotated into segments; join them back before grafting.
        try:
            merge_segments(path, remove=remove, logger=logger)
        except Exception as err:
            logger.warn(f"Merging segments of thread output '{path}' "
                        f"failed: {err}")
        thread_root = _parse_thread_file(path, logger)
        if thread_root is None:
            continue
        _graft(elements[-1], thread_root)
        merged += 1
        used_files.add(path)
    if merged:
        tree.write(output_path, encoding='UTF-8', xml_declaration=True)
        logger.info(f'Merged {merged} thread output file(s) into {output_path}.')
    if remove:
        for path in used_files:
            try:
                os.remove(path)
            except OSError as err:
                logger.warn(f"Removing merged thread output '{path}' failed: {err}")
    return merged


def _discover_thread_files(output_path):
    base, ext = os.path.splitext(output_path)
    files = {}
    for path in glob.glob(f'{glob.escape(base)}_*{ext}'):
        # File name format is <base>_<thread name><ext>.
        name = path[len(base) + 1:len(path) - len(ext)]
        # cuongnht add segmented output: skip sealed segment files of the
        # main output ('part_NNN') and of threads ('<thread>_part_NNN').
        if re.fullmatch(r'(?:.+_)?part_\d+', name):
            continue
        try:
            for _, elem in ET.iterparse(path, events=('start',)):
                if elem.tag == 'thread':
                    files[elem.get('name') or name] = path
                break
        except ET.ParseError:
            # Possibly truncated (crashed/daemon thread); _parse_thread_file
            # attempts recovery later.
            files[name] = path
    return files


def _find_placeholders(root):
    """Return ``{thread_name: [placeholder elements in document order]}``.

    A placeholder is a ``<thread>`` element that has no body yet, i.e. only
    ``<status>``/``<doc>`` children. Already merged threads are left alone,
    which makes merging idempotent.
    """
    placeholders = {}
    for elem in root.iter('thread'):
        if all(child.tag in ('status', 'doc') for child in elem):
            placeholders.setdefault(elem.get('name'), []).append(elem)
    return placeholders


def _parse_thread_file(path, logger):
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        # The thread was probably still running when execution ended (daemon)
        # or the process crashed: the root element was never closed. Recover
        # what we have by closing it ourselves.
        try:
            with open(path, encoding='UTF-8') as f:
                text = f.read()
            return ET.fromstring(text + '</thread>')
        except ET.ParseError as err:
            logger.warn(f"Cannot parse thread output '{path}': {err}")
            return None


def _graft(placeholder, thread_root):
    """Move the body of ``thread_root`` into ``placeholder``.

    The fake always-PASS ``<status>`` written when the thread was started is
    replaced with the real one from the thread file (real start/end times and
    status). Resulting child order is: body items, doc, status.
    """
    doc = placeholder.find('doc')
    real_status = thread_root.find('status')
    if real_status is None:
        # cuongnht thread scope: the thread never wrote its final status --
        # it was abandoned at scope end or was still running when execution
        # finished. Report it as UNKNOWN instead of the provisional PASS.
        status = ET.Element('status', {'status': 'UNKNOWN',
                                       'starttime': 'N/A', 'endtime': 'N/A',
                                       'elapsedtime': '0'})
        status.text = 'Thread did not finish before its scope ended.'
    else:
        status = real_status
    for child in list(placeholder):
        placeholder.remove(child)
    for child in list(thread_root):
        if child.tag != 'status':
            placeholder.append(child)
    if doc is not None:
        placeholder.append(doc)
    if status is not None:
        if status.tail is None or not status.tail.strip():
            status.tail = '\n'
        placeholder.append(status)


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser(
        description='Merge per-thread output XML files into the main output.xml.')
    parser.add_argument('output', help='Path to the main output.xml')
    parser.add_argument('--keep', action='store_true',
                        help='Keep the per-thread files after merging.')

    class _StdoutLogger:
        def info(self, msg): print(msg)
        def warn(self, msg): print(f'[ WARN ] {msg}')
        def error(self, msg): print(f'[ ERROR ] {msg}')

    ns = parser.parse_args(args)
    merged = merge_thread_outputs(ns.output, remove=not ns.keep,
                                  logger=_StdoutLogger())
    print(f'{merged} thread(s) merged.')


if __name__ == '__main__':
    main()
