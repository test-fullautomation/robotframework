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

"""Shape validation of flow files (schema version 1).

A flow file is a JSON document with ``nodes`` and ``edges``. This module only
checks that the document has the right *shape*: known node kinds with their
required attributes, well-formed edges and known edge labels. Whether the
graph is *structured* (branches re-join, loop bodies return, everything is
reachable) is the job of :mod:`robot.flow.graph`.
"""

import json
from pathlib import Path

from robot.errors import DataError
from robot.utils import is_string, timestr_to_secs


FLOW_VERSION = 1

START, END, PHASE, KEYWORD, GATE, SLEEP, DECISION, LOOP, TRY = (
    'start', 'end', 'phase', 'keyword', 'gate', 'sleep', 'decision', 'loop', 'try'
)
NODE_KINDS = (START, END, PHASE, KEYWORD, GATE, SLEEP, DECISION, LOOP, TRY)
ACTION_KINDS = (KEYWORD, GATE, SLEEP)
CONTAINER_KINDS = (LOOP, TRY)

SETUP, TEST, TEARDOWN = 'setup', 'test', 'teardown'
PHASE_ROLES = (SETUP, TEST, TEARDOWN)

THEN, BODY, NEXT, ON_FAILURE, CONTINUE, ABORT, DONE, YES, NO = (
    '', 'body', 'next', 'on_failure', 'continue', 'abort', 'done', 'yes', 'no'
)
EDGE_LABELS = (THEN, BODY, NEXT, ON_FAILURE, CONTINUE, ABORT, DONE, YES, NO)

ON_TIMEOUT_UNKNOWN, ON_TIMEOUT_FAIL = 'unknown', 'fail'
DEFAULT_GATE_INTERVAL = '2s'


class FlowError(DataError):
    """Raised when a flow file is invalid. The message names the offending node."""

    def __init__(self, message, node=None):
        if node is not None:
            message = f"Node '{node}': {message}"
        super().__init__(message)


class Node:
    """A validated node. Attributes not relevant for the kind are ``None``."""

    def __init__(self, id, kind, **attrs):
        self.id = id
        self.kind = kind
        self.label = attrs.pop('label', None) or id
        self.role = attrs.pop('role', None)
        self.name = attrs.pop('name', None)
        self.keyword = attrs.pop('keyword', None)
        self.args = attrs.pop('args', None)
        self.assign = attrs.pop('assign', None)
        self.timeout = attrs.pop('timeout', None)
        self.interval = attrs.pop('interval', None)
        self.on_timeout = attrs.pop('on_timeout', None)
        self.duration = attrs.pop('duration', None)
        self.condition = attrs.pop('condition', None)
        self.max_loops = attrs.pop('max_loops', None)
        self.max_seconds = attrs.pop('max_seconds', None)
        self.every = attrs.pop('every', None)
        self.extra = attrs

    def __repr__(self):
        return f'Node({self.id!r}, {self.kind!r})'


class Edge:

    def __init__(self, source, target, label=THEN):
        self.source = source
        self.target = target
        self.label = label

    def __repr__(self):
        return f'Edge({self.source!r} -> {self.target!r}, {self.label!r})'


class FlowData:
    """The validated content of a flow file."""

    def __init__(self, name, version, libraries, resources, variable_files,
                 variables, nodes, edges):
        self.name = name
        self.version = version
        self.libraries = libraries          # list of (name, args)
        self.resources = resources          # list of paths
        self.variable_files = variable_files  # list of (name, args)
        self.variables = variables          # dict name -> value
        self.nodes = nodes                  # dict id -> Node, in file order
        self.edges = edges                  # list of Edge


def load_flow(source):
    """Read and validate a flow file. ``source`` is a path or an already parsed dict."""
    if isinstance(source, dict):
        data = source
    else:
        path = Path(source)
        try:
            with open(path, encoding='UTF-8') as file:
                data = json.load(file)
        except ValueError as err:
            raise FlowError(f"Invalid JSON in '{path}': {err}")
    return validate(data)


def validate(data):
    if not isinstance(data, dict):
        raise FlowError('Flow file must contain a JSON object at the top level.')
    name, version = _validate_flow_section(data.get('flow'))
    libraries, resources, variable_files = _validate_imports(data.get('imports', {}))
    variables = _validate_variables(data.get('variables', {}))
    nodes = _validate_nodes(data.get('nodes'))
    edges = _validate_edges(data.get('edges'), nodes)
    return FlowData(name, version, libraries, resources, variable_files,
                    variables, nodes, edges)


def _validate_flow_section(flow):
    if not isinstance(flow, dict) or not is_string(flow.get('name')) \
            or not flow['name'].strip():
        raise FlowError("'flow' section must be an object with a non-empty 'name'.")
    version = flow.get('version', FLOW_VERSION)
    if version != FLOW_VERSION:
        raise FlowError(f"Unsupported flow version {version!r}; "
                        f"this Robot Framework supports version {FLOW_VERSION}.")
    return flow['name'].strip(), version


def _validate_imports(imports):
    if not isinstance(imports, dict):
        raise FlowError("'imports' must be an object.")
    libraries = [_name_and_args(item, 'libraries') for item in imports.get('libraries', [])]
    resources = imports.get('resources', [])
    if not all(is_string(res) for res in resources):
        raise FlowError("'imports.resources' must be a list of paths.")
    variable_files = [_name_and_args(item, 'variables')
                      for item in imports.get('variables', [])]
    return libraries, list(resources), variable_files


def _name_and_args(item, section):
    if is_string(item):
        return item, []
    if isinstance(item, list) and item and is_string(item[0]):
        return item[0], [_stringify(arg) for arg in item[1:]]
    raise FlowError(f"'imports.{section}' entries must be a name or a list "
                    f"starting with a name, got {item!r}.")


def _validate_variables(variables):
    if not isinstance(variables, dict):
        raise FlowError("'variables' must be an object mapping names to values.")
    result = {}
    for name, value in variables.items():
        if not is_string(name) or not name.strip():
            raise FlowError(f"Invalid variable name {name!r}.")
        if isinstance(value, (dict, list)):
            raise FlowError(f"Variable '{name}' must be a scalar value.")
        result[name.strip().strip('${}')] = _stringify(value)
    return result


def _validate_nodes(nodes):
    if not isinstance(nodes, list) or not nodes:
        raise FlowError("'nodes' must be a non-empty list.")
    result = {}
    for raw in nodes:
        node = _validate_node(raw)
        if node.id in result:
            raise FlowError('Duplicate node id.', node.id)
        result[node.id] = node
    return result


def _validate_node(raw):
    if not isinstance(raw, dict):
        raise FlowError(f'Each node must be an object, got {raw!r}.')
    id = raw.get('id')
    if not is_string(id) or not id.strip():
        raise FlowError(f"Node without a valid 'id': {raw!r}.")
    kind = raw.get('kind')
    if kind not in NODE_KINDS:
        raise FlowError(f"Unknown kind {kind!r}. Valid kinds are "
                        f"{', '.join(NODE_KINDS)}.", id)
    attrs = {key: value for key, value in raw.items() if key not in ('id', 'kind')}
    node = Node(id, kind, **attrs)
    validator = {
        START: _no_attributes, END: _no_attributes, PHASE: _validate_phase,
        KEYWORD: _validate_keyword, GATE: _validate_gate, SLEEP: _validate_sleep,
        DECISION: _validate_decision, LOOP: _validate_loop, TRY: _no_attributes,
    }[kind]
    validator(node)
    return node


def _no_attributes(node):
    pass


def _validate_phase(node):
    if node.role not in PHASE_ROLES:
        raise FlowError(f"Phase 'role' must be one of {', '.join(PHASE_ROLES)}, "
                        f"got {node.role!r}.", node.id)
    if node.role == TEST:
        if not is_string(node.name) or not node.name.strip():
            raise FlowError("A test phase needs a non-empty 'name'.", node.id)
        node.name = node.name.strip()


def _validate_keyword(node):
    if not is_string(node.keyword) or not node.keyword.strip():
        raise FlowError("'keyword' is required.", node.id)
    node.keyword = node.keyword.strip()
    node.args = _validate_args(node)
    node.assign = _validate_assign(node)


def _validate_assign(node):
    assign = node.assign
    if assign is None:
        return []
    if is_string(assign):
        assign = [assign]
    if not isinstance(assign, list) or not all(is_string(a) and a.strip() for a in assign):
        raise FlowError("'assign' must be a variable name or a list of them.", node.id)
    return [a.strip() for a in assign]


def _validate_args(node):
    args = node.args if node.args is not None else []
    if not isinstance(args, list):
        raise FlowError("'args' must be a list.", node.id)
    for arg in args:
        if isinstance(arg, (dict, list)):
            raise FlowError(f"Arguments must be scalar values, got {arg!r}.", node.id)
    return [_stringify(arg) for arg in args]


def _validate_gate(node):
    _validate_keyword(node)
    node.timeout = _validate_time(node, 'timeout', required=True)
    node.interval = _validate_time(node, 'interval', default=DEFAULT_GATE_INTERVAL)
    node.on_timeout = (node.on_timeout or ON_TIMEOUT_UNKNOWN)
    if node.on_timeout not in (ON_TIMEOUT_UNKNOWN, ON_TIMEOUT_FAIL):
        raise FlowError(f"'on_timeout' must be '{ON_TIMEOUT_UNKNOWN}' or "
                        f"'{ON_TIMEOUT_FAIL}', got {node.on_timeout!r}.", node.id)


def _validate_sleep(node):
    node.duration = _validate_time(node, 'duration', required=True)


def _validate_decision(node):
    if not is_string(node.condition) or not node.condition.strip():
        raise FlowError("'condition' is required.", node.id)
    node.condition = node.condition.strip()


def _validate_loop(node):
    if node.max_loops is not None:
        if isinstance(node.max_loops, bool) or not isinstance(node.max_loops, int) \
                or node.max_loops < 1:
            raise FlowError("'max_loops' must be a positive integer.", node.id)
    node.max_seconds = _validate_time(node, 'max_seconds')
    node.every = _validate_time(node, 'every')
    if node.max_loops is None and node.max_seconds is None:
        raise FlowError("A loop needs 'max_loops' and/or 'max_seconds'; "
                        "unbounded loops are not allowed.", node.id)


def _validate_time(node, attr, required=False, default=None):
    value = getattr(node, attr)
    if value is None:
        if required:
            raise FlowError(f"'{attr}' is required.", node.id)
        return default
    value = _stringify(value)
    try:
        timestr_to_secs(value)
    except ValueError:
        raise FlowError(f"'{attr}' must be a valid time string, got {value!r}.",
                        node.id)
    return value


def _validate_edges(edges, nodes):
    if not isinstance(edges, list):
        raise FlowError("'edges' must be a list.")
    result = []
    for raw in edges:
        if isinstance(raw, list) and len(raw) == 2:
            source, target, label = raw[0], raw[1], THEN
        elif isinstance(raw, dict):
            source, target = raw.get('from'), raw.get('to')
            label = raw.get('label', THEN) or THEN
        else:
            raise FlowError(f'Each edge must be a [from, to] pair or an object with '
                            f"'from', 'to' and optional 'label', got {raw!r}.")
        for end in (source, target):
            if end not in nodes:
                raise FlowError(f"Edge {source!r} -> {target!r} refers to unknown "
                                f"node {end!r}.")
        if label not in EDGE_LABELS:
            raise FlowError(f"Unknown edge label {label!r} on {source!r} -> "
                            f"{target!r}. Valid labels are "
                            f"{', '.join(repr(l) for l in EDGE_LABELS[1:])} "
                            f"or none.")
        result.append(Edge(source, target, label))
    return result


def _stringify(value):
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if value is None:
        return ''
    return str(value)
