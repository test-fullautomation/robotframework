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

"""Rendering a built suite back as ``.robot`` text.

The text is produced from the very :class:`~robot.running.TestSuite` that
would run, so what a reviewer reads is what executes. It is meant for
review and debugging; the run itself never writes it to disk.
"""

from robot.model import BodyItem
from robot.running import For, If, Keyword, Try, While
from robot.running.model import Import


SEPARATOR = '    '


def render_robot(suite):
    """Return ``suite`` as Robot Framework plain-text data."""
    lines = []
    _settings(lines, suite)
    _variables(lines, suite)
    _tests(lines, suite)
    _keywords(lines, suite)
    return '\n'.join(lines).rstrip('\n') + '\n'


def _settings(lines, suite):
    resource = suite.resource
    rows = []
    for imp in resource.imports:
        if imp.type == Import.LIBRARY:
            rows.append(('Library', imp.name, *imp.args))
        elif imp.type == Import.RESOURCE:
            rows.append(('Resource', imp.name))
        else:
            rows.append(('Variables', imp.name, *imp.args))
    if suite.has_setup:
        rows.append(('Suite Setup', suite.setup.name, *suite.setup.args))
    if suite.has_teardown:
        rows.append(('Suite Teardown', suite.teardown.name, *suite.teardown.args))
    if rows:
        lines.append('*** Settings ***')
        width = max(len(row[0]) for row in rows)
        for row in rows:
            lines.append(row[0].ljust(width) + SEPARATOR + SEPARATOR.join(row[1:]))
        lines.append('')


def _variables(lines, suite):
    variables = list(suite.resource.variables)
    if not variables:
        return
    lines.append('*** Variables ***')
    width = max(len(var.name) for var in variables)
    for var in variables:
        lines.append(var.name.ljust(width) + SEPARATOR + SEPARATOR.join(var.value))
    lines.append('')


def _tests(lines, suite):
    if not suite.tests:
        return
    lines.append('*** Test Cases ***')
    for test in suite.tests:
        lines.append(test.name)
        if test.timeout:
            _row(lines, 1, '[Timeout]', test.timeout)
        _body(lines, test.body, 1)
        lines.append('')


def _keywords(lines, suite):
    keywords = list(suite.resource.keywords)
    if not keywords:
        return
    lines.append('*** Keywords ***')
    for keyword in keywords:
        lines.append(keyword.name)
        if keyword.args:
            _row(lines, 1, '[Arguments]', *keyword.args)
        _body(lines, keyword.body, 1)
        lines.append('')


def _body(lines, body, indent):
    for item in body:
        if isinstance(item, Keyword):
            assign = [a if a.endswith('=') else a + '=' for a in item.assign]
            _row(lines, indent, *assign, item.name, *item.args)
        elif isinstance(item, If):
            for branch in item.body:
                if branch.type == BodyItem.IF:
                    _row(lines, indent, 'IF', branch.condition)
                elif branch.type == BodyItem.ELSE_IF:
                    _row(lines, indent, 'ELSE IF', branch.condition)
                else:
                    _row(lines, indent, 'ELSE')
                _body(lines, branch.body, indent + 1)
            _row(lines, indent, 'END')
        elif isinstance(item, Try):
            for branch in item.body:
                cells = [branch.type]
                if branch.type == BodyItem.EXCEPT:
                    cells.extend(branch.patterns)
                    if branch.pattern_type:
                        cells.append(f'type={branch.pattern_type}')
                    if branch.variable:
                        cells.extend(['AS', branch.variable])
                _row(lines, indent, *cells)
                _body(lines, branch.body, indent + 1)
            _row(lines, indent, 'END')
        elif isinstance(item, While):
            cells = ['WHILE', item.condition or 'True']
            if item.limit:
                cells.append(f'limit={item.limit}')
            if item.on_limit:
                cells.append(f'on_limit={item.on_limit}')
            if item.on_limit_message:
                cells.append(f'on_limit_message={item.on_limit_message}')
            _row(lines, indent, *cells)
            _body(lines, item.body, indent + 1)
            _row(lines, indent, 'END')
        elif isinstance(item, For):
            _row(lines, indent, 'FOR', *item.variables, item.flavor, *item.values)
            _body(lines, item.body, indent + 1)
            _row(lines, indent, 'END')
        else:
            _row(lines, indent, item.type, *getattr(item, 'values', ()))


def _row(lines, indent, *cells):
    lines.append(SEPARATOR * indent + SEPARATOR.join(cells))
