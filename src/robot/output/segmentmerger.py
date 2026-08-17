#  cuongnht add segmented output
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

"""Merge segmented output XML files back into one output.xml.

With ``--segmentoutput <interval>`` the main output.xml is periodically
sealed into well-formed segments named ``<base>_part_NNN.xml`` while writing
continues in a fresh file with the same open element structure. Elements
that were open at a segment boundary are replayed in the next segment with
a ``continued="true"`` marker.

This module joins the segments back together: for each segment, children of
``continued`` elements are appended to their counterpart in the previous
segment (the last child of the corresponding parent, since it was the open
one when the segment was sealed).

Merging happens automatically at the end of a run. After a crash, run it
manually to recover everything written so far - the possibly truncated live
output.xml is repaired by closing its dangling elements::

    python -m robot.output.segmentmerger output.xml
    rebot output.xml
"""

import glob
import os
import re
from xml.etree import ElementTree as ET

_TAG = re.compile(r'<(/?)([A-Za-z][\w.-]*)(?:"[^"]*"|\'[^\']*\'|[^>])*?(/?)>')


def merge_segments(output_path, part_paths=None, remove=True, logger=None):
    """Merges ``<base>_part_NNN.xml`` segments and ``output_path`` into one.

    :param output_path: The live/final output.xml (last segment). Receives
        the merged result.
    :param part_paths: Sealed segment files in order. Auto-discovered from
        the file name pattern if not given.
    :param remove: Remove the sealed segment files after a successful merge.
    :param logger: Object with ``info``/``warn``/``error`` methods. Defaults
        to the global robot LOGGER.
    :return: Number of segment files merged into the output.
    """
    if logger is None:
        from .logger import LOGGER as logger
    if part_paths is None:
        part_paths = _discover_parts(output_path)
    if not part_paths:
        return 0
    trees = []
    for path in list(part_paths) + [output_path]:
        root = _parse(path, logger)
        if root is None and path == output_path:
            logger.warn(f"Ignoring unrecoverable live output '{path}'.")
            continue
        if root is not None:
            trees.append(root)
    if not trees:
        logger.error('No parseable output segments found.')
        return 0
    merged = trees[0]
    for root in trees[1:]:
        _merge_children(merged, root)
    ET.ElementTree(merged).write(output_path, encoding='UTF-8',
                                 xml_declaration=True)
    logger.info(f'Merged {len(part_paths)} output segment(s) into '
                f'{output_path}.')
    if remove:
        for path in part_paths:
            try:
                os.remove(path)
            except OSError as err:
                logger.warn(f"Removing merged segment '{path}' failed: {err}")
    return len(part_paths)


def _discover_parts(output_path):
    base, ext = os.path.splitext(output_path)
    return sorted(glob.glob(f'{glob.escape(base)}_part_*{ext}'))


def _parse(path, logger):
    if not os.path.isfile(path):
        logger.warn(f"Output segment '{path}' does not exist.")
        return None
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        pass
    # Probably the live file of a crashed run: repair by dropping a possible
    # incomplete trailing tag and closing all dangling elements.
    try:
        with open(path, encoding='UTF-8', errors='replace') as f:
            text = f.read()
        root = ET.fromstring(_recover(text))
        logger.warn(f"Output segment '{path}' was truncated and has been "
                    f"recovered.")
        return root
    except ET.ParseError as err:
        logger.warn(f"Cannot parse output segment '{path}': {err}")
        return None


def _recover(text):
    stack = []
    end_of_last_tag = 0
    for match in _TAG.finditer(text):
        closing, tag, self_closing = match.groups()
        end_of_last_tag = match.end()
        if self_closing:
            continue
        if not closing:
            stack.append(tag)
        elif stack and stack[-1] == tag:
            stack.pop()
    text = text[:end_of_last_tag]
    return text + ''.join(f'</{tag}>' for tag in reversed(stack))


def _merge_children(previous, current):
    """Appends children of ``current`` into ``previous`` (same document
    position), joining the ``continued`` element chain recursively."""
    children = list(current)
    if children and children[0].get('continued') == 'true':
        cont = children.pop(0)
        del cont.attrib['continued']
        if len(previous) and previous[-1].tag == cont.tag:
            # The element that was open when the previous segment was sealed
            # is by construction its parent's last child.
            _merge_children(previous[-1], cont)
        else:
            previous.append(cont)
    for child in children:
        previous.append(child)


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog='python -m robot.output.segmentmerger',
        description='Merge segmented output XML files back into one.')
    parser.add_argument('output', help='Path to the live/final output.xml')
    parser.add_argument('--keep', action='store_true',
                        help='Keep the sealed segment files after merging.')

    class _StdoutLogger:
        def info(self, msg): print(msg)
        def warn(self, msg): print(f'[ WARN ] {msg}')
        def error(self, msg): print(f'[ ERROR ] {msg}')

    ns = parser.parse_args(args)
    merged = merge_segments(ns.output, remove=not ns.keep,
                            logger=_StdoutLogger())
    print(f'{merged} segment(s) merged.')


if __name__ == '__main__':
    main()
