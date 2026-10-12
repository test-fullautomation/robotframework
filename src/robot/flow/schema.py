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
import re
from pathlib import Path

from robot.errors import DataError
from robot.utils import timestr_to_secs


def is_string(item):
    # robot.utils.is_string is deprecated in RF 7.
    return isinstance(item, str)


FLOW_VERSION = 1

# One '${variable}' as the whole value: a bound or a time sized per run.
VARIABLE_PATTERN = r'^\s*\$\{[^{}$]+\}\s*$'
VARIABLE_RE = re.compile(VARIABLE_PATTERN)

START, END, PHASE, KEYWORD, GATE, SLEEP, DECISION, LOOP, TRY, FLOW, PARALLEL = (
    'start', 'end', 'phase', 'keyword', 'gate', 'sleep', 'decision', 'loop', 'try', 'flow',
    'parallel'
)
NODE_KINDS = (START, END, PHASE, KEYWORD, GATE, SLEEP, DECISION, LOOP, TRY, FLOW, PARALLEL)
ACTION_KINDS = (KEYWORD, GATE, SLEEP, FLOW)
CONTAINER_KINDS = (LOOP, TRY)

SETUP, TEST, TEARDOWN = 'setup', 'test', 'teardown'
PHASE_ROLES = (SETUP, TEST, TEARDOWN)

THEN, BODY, NEXT, ON_FAILURE, CONTINUE, ABORT, DONE, YES, NO, BRANCH, JOIN = (
    '', 'body', 'next', 'on_failure', 'continue', 'abort', 'done', 'yes', 'no', 'branch',
    'join'
)
EDGE_LABELS = (THEN, BODY, NEXT, ON_FAILURE, CONTINUE, ABORT, DONE, YES, NO, BRANCH, JOIN)

ON_TIMEOUT_UNKNOWN, ON_TIMEOUT_FAIL = 'unknown', 'fail'
DEFAULT_GATE_INTERVAL = '2s'
# A keyword node's retry policy: attempts, the wait between them growing by
# 'backoff', and the verdict when the last attempt fails too.
DEFAULT_RETRY_INTERVAL = '1s'
RETRY_KEYS = ('max_attempts', 'interval', 'backoff', 'on_exhausted')


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
        self.retry = attrs.pop('retry', None)
        self.duration = attrs.pop('duration', None)
        self.condition = attrs.pop('condition', None)
        self.max_loops = attrs.pop('max_loops', None)
        self.max_seconds = attrs.pop('max_seconds', None)
        self.every = attrs.pop('every', None)
        self.file = attrs.pop('file', None)
        self.stop_others = attrs.pop('stop_others', None)
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
                 variables, nodes, edges, checkpoint=None, checkpoint_every=None):
        self.name = name
        # None: the default file; a string: that file; False: no checkpoint.
        self.checkpoint = checkpoint
        self.checkpoint_every = checkpoint_every
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
        except OSError as err:
            raise FlowError(f"Reading flow file '{path}' failed: {err}")
        except ValueError as err:
            raise FlowError(f"Invalid JSON in '{path}': {err}")
    return validate(data)


def validate(data):
    if not isinstance(data, dict):
        raise FlowError('Flow file must contain a JSON object at the top level.')
    name, version, checkpoint, every = _validate_flow_section(data.get('flow'))
    libraries, resources, variable_files = _validate_imports(data.get('imports', {}))
    variables = _validate_variables(data.get('variables', {}))
    nodes = _validate_nodes(data.get('nodes'))
    edges = _validate_edges(data.get('edges'), nodes)
    return FlowData(name, version, libraries, resources, variable_files,
                    variables, nodes, edges, checkpoint, every)


def _validate_flow_section(flow):
    if not isinstance(flow, dict) or not is_string(flow.get('name')) \
            or not flow['name'].strip():
        raise FlowError("'flow' section must be an object with a non-empty 'name'.")
    version = flow.get('version', FLOW_VERSION)
    if version != FLOW_VERSION:
        raise FlowError(f"Unsupported flow version {version!r}; "
                        f"this Robot Framework supports version {FLOW_VERSION}.")
    checkpoint = flow.get('checkpoint')
    if checkpoint is True:
        checkpoint = None
    if checkpoint not in (None, False) \
            and not (is_string(checkpoint) and checkpoint.strip()):
        raise FlowError("'flow.checkpoint' must be a file path, or false for a "
                        "flow without a checkpoint.")
    every = flow.get('checkpoint_every')
    if every is not None and (isinstance(every, bool) or not isinstance(every, int)
                              or every < 1):
        raise FlowError("'flow.checkpoint_every' must be a positive integer.")
    return flow['name'].strip(), version, checkpoint, every


def _validate_imports(imports):
    if not isinstance(imports, dict):
        raise FlowError("'imports' must be an object.")
    for section in ('libraries', 'resources', 'variables'):
        if not isinstance(imports.get(section, []), list):
            raise FlowError(f"'imports.{section}' must be a list.")
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
        KEYWORD: _validate_keyword_node, GATE: _validate_gate, SLEEP: _validate_sleep,
        DECISION: _validate_decision, LOOP: _validate_loop, TRY: _no_attributes,
        FLOW: _validate_flow_call, PARALLEL: _validate_parallel,
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


def _validate_keyword_node(node):
    """A keyword node: the call, plus its optional retry policy and timeout."""
    _validate_keyword(node)
    node.timeout = _validate_time(node, 'timeout')
    node.retry = _validate_retry(node)


def _validate_retry(node):
    """``retry`` as a dict with every key, or ``None`` without a policy.

    A number (or a ``${variable}``) is short for ``{"max_attempts": n}``.
    """
    retry = node.retry
    if retry is None:
        return None
    if not isinstance(retry, dict):
        retry = {'max_attempts': retry}
    unknown = [key for key in retry if key not in RETRY_KEYS]
    if unknown:
        raise FlowError(f"'retry' has unknown key(s) {', '.join(unknown)}; the keys "
                        f"are {', '.join(RETRY_KEYS)}.", node.id)
    attempts = retry.get('max_attempts')
    if attempts is None:
        raise FlowError("'retry' needs 'max_attempts'.", node.id)
    if not is_variable(attempts):
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise FlowError("'retry.max_attempts' must be a positive integer or a "
                            "'${variable}'.", node.id)
    backoff = retry.get('backoff', 1)
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or backoff < 1:
        raise FlowError("'retry.backoff' must be a number of at least 1 (the factor "
                        "the interval grows by after each attempt).", node.id)
    on_exhausted = retry.get('on_exhausted') or ON_TIMEOUT_FAIL
    if on_exhausted not in (ON_TIMEOUT_UNKNOWN, ON_TIMEOUT_FAIL):
        raise FlowError(f"'retry.on_exhausted' must be '{ON_TIMEOUT_FAIL}' or "
                        f"'{ON_TIMEOUT_UNKNOWN}', got {on_exhausted!r}.", node.id)
    interval = _check_time(node, 'retry.interval', retry.get('interval'),
                           default=DEFAULT_RETRY_INTERVAL)
    return {'max_attempts': attempts.strip() if is_variable(attempts) else attempts,
            'interval': interval, 'backoff': backoff, 'on_exhausted': on_exhausted}


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
    if node.retry is not None:
        raise FlowError("A gate polls until it passes already; 'retry' belongs to "
                        "keyword nodes.", node.id)
    node.timeout = _validate_time(node, 'timeout', required=True)
    node.interval = _validate_time(node, 'interval', default=DEFAULT_GATE_INTERVAL)
    node.on_timeout = (node.on_timeout or ON_TIMEOUT_UNKNOWN)
    if node.on_timeout not in (ON_TIMEOUT_UNKNOWN, ON_TIMEOUT_FAIL):
        raise FlowError(f"'on_timeout' must be '{ON_TIMEOUT_UNKNOWN}' or "
                        f"'{ON_TIMEOUT_FAIL}', got {node.on_timeout!r}.", node.id)


def _validate_sleep(node):
    node.duration = _validate_time(node, 'duration', required=True)


def _validate_flow_call(node):
    """A sub-flow call: 'file' (relative to the calling flow file) and named 'args'."""
    if not is_string(node.file) or not node.file.strip():
        raise FlowError("'file' is required: the sub-flow file to call.", node.id)
    node.file = node.file.strip()
    if node.assign is not None:
        raise FlowError("A sub-flow returns nothing; 'assign' is not supported.", node.id)
    args = node.args if node.args is not None else {}
    if not isinstance(args, dict):
        raise FlowError("'args' of a sub-flow must be an object mapping its parameter "
                        "names to values.", node.id)
    result = {}
    for name, value in args.items():
        if not is_string(name) or not name.strip('${} '):
            raise FlowError(f'Invalid parameter name {name!r}.', node.id)
        if isinstance(value, (dict, list)):
            raise FlowError(f"Argument '{name}' must be a scalar value.", node.id)
        result[name.strip().strip('${}')] = _stringify(value)
    node.args = result
    node.assign = []


def _validate_decision(node):
    if not is_string(node.condition) or not node.condition.strip():
        raise FlowError("'condition' is required.", node.id)
    node.condition = node.condition.strip()


def is_variable(value):
    """True for a value that is one ``${variable}`` reference, resolved when run."""
    return is_string(value) and VARIABLE_RE.match(value.strip()) is not None


def _validate_loop(node):
    if node.max_loops is not None and not is_variable(node.max_loops):
        if isinstance(node.max_loops, bool) or not isinstance(node.max_loops, int) \
                or node.max_loops < 1:
            raise FlowError("'max_loops' must be a positive integer or a "
                            "'${variable}'.", node.id)
    node.max_seconds = _validate_time(node, 'max_seconds')
    node.every = _validate_time(node, 'every')
    if node.max_loops is None and node.max_seconds is None:
        raise FlowError("A loop needs 'max_loops' and/or 'max_seconds'; "
                        "unbounded loops are not allowed.", node.id)


def _validate_parallel(node):
    """A parallel region: branches run at the same time and join before the
    plan goes on. ``timeout`` bounds the whole region; ``stop_others`` says
    whether a failed branch stops the others (the default) or lets them end."""
    node.timeout = _validate_time(node, 'timeout')
    if node.stop_others is None:
        node.stop_others = True
    elif not isinstance(node.stop_others, bool):
        raise FlowError("'stop_others' must be true or false.", node.id)


def _validate_time(node, attr, required=False, default=None):
    return _check_time(node, attr, getattr(node, attr), required, default)


def _check_time(node, attr, value, required=False, default=None):
    if value is None:
        if required:
            raise FlowError(f"'{attr}' is required.", node.id)
        return default
    value = _stringify(value)
    if is_variable(value):
        return value.strip()          # checked when the step runs
    try:
        seconds = timestr_to_secs(value)
    except ValueError:
        raise FlowError(f"'{attr}' must be a valid time string, got {value!r}.",
                        node.id)
    if seconds < 0:
        raise FlowError(f"'{attr}' must not be negative, got {value!r}.", node.id)
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


# ------------------------------------------------------------------ JSON Schema

def json_schema():
    """The JSON Schema (draft-07) of flow files, for editors.

    Built from the constants above so it cannot drift from :func:`validate`.
    It is stricter in one way: unknown attributes are reported, so a typo such
    as ``max_loop`` shows up in the editor (the runner itself ignores unknown
    attributes). Rules about the graph's *structure* -- branches re-join,
    loop bodies return, everything is reachable -- are not expressible in a
    schema; ``python -m robot.flow validate`` checks them.
    """
    scalar = {'type': ['string', 'number', 'boolean', 'null']}
    time = {'type': ['string', 'number'],
            'description': "A time: '10s', '1 min 30 s', '1:30', '2h' or seconds as a number; "
                           "or a '${variable}' holding one."}
    named = {'oneOf': [
        {'type': 'string', 'minLength': 1},
        {'type': 'array', 'minItems': 1,
         'items': [{'type': 'string', 'minLength': 1}], 'additionalItems': scalar},
    ]}
    common = {
        'id': {'type': 'string', 'pattern': r'\S',
               'description': 'Unique id of the node; edges refer to it.'},
        'kind': {'enum': list(NODE_KINDS), 'description': 'What the node is.'},
        'label': {'type': 'string', 'description': 'Text shown instead of the id.'},
    }
    keyword = {
        'keyword': {'type': 'string', 'pattern': r'\S',
                    'description': 'Keyword to call, e.g. a Python function '
                                   "'power_on' as 'Power On'."},
        'args': {'type': 'array', 'items': scalar,
                 'description': "Arguments; '${var}' substitution applies."},
        'assign': {'oneOf': [{'type': 'string', 'pattern': r'\S'},
                             {'type': 'array', 'items': {'type': 'string', 'pattern': r'\S'}}],
                   'description': "Variable(s) for the return value, e.g. '${VERSION}'."},
    }

    attempts = {'anyOf': [{'type': 'integer', 'minimum': 1},
                          {'type': 'string', 'pattern': VARIABLE_PATTERN}],
                'description': "How many times the keyword may run, or a '${variable}' "
                               'holding the number.'}
    retry = {
        'type': 'object', 'required': ['max_attempts'], 'additionalProperties': False,
        'properties': {
            'max_attempts': attempts,
            'interval': dict(time, description=f'Wait before the next attempt; default '
                                               f'{DEFAULT_RETRY_INTERVAL}.'),
            'backoff': {'type': 'number', 'minimum': 1,
                        'description': 'Factor the wait grows by after each attempt; '
                                       'default 1 (a constant wait).'},
            'on_exhausted': {'enum': [ON_TIMEOUT_FAIL, ON_TIMEOUT_UNKNOWN],
                             'description': "Verdict when the last attempt fails too: 'fail' "
                                            "(the default: the keyword's own failure) or "
                                            "'unknown' (the bench did not cooperate)."},
        },
    }

    def kind(name, description, required=(), extra=None, **more):
        properties = dict(common, **(extra or {}))
        definition = {'description': description, 'type': 'object',
                      'required': ['id', 'kind', *required],
                      'properties': properties, 'additionalProperties': False}
        definition.update(more)
        return name, definition

    kinds = dict([
        kind(START, 'Where the flow begins; exactly one.'),
        kind(END, 'Where the flow ends; at least one.'),
        kind(PHASE, 'A lane: setup becomes the suite setup, each test phase a test '
                    'case, teardown the suite teardown (always runs).',
             required=('role',),
             extra={'role': {'enum': list(PHASE_ROLES)},
                    'name': {'type': 'string', 'pattern': r'\S',
                             'description': 'Test case name; required for a test phase.'}},
             **{'if': {'properties': {'role': {'const': TEST}}},
                'then': {'required': ['name']}}),
        kind(KEYWORD, 'Call a keyword.', required=('keyword',),
             extra=dict(keyword,
                        retry={'oneOf': [attempts, retry],
                               'description': 'Run the keyword again when it fails: a '
                                              'number of attempts, or a policy object.'},
                        timeout=dict(time, description='Time limit of one attempt; an '
                                                       'attempt that exceeds it fails '
                                                       '(and is retried under a retry '
                                                       'policy).'))),
        kind(GATE, "Wait until the keyword passes, at most 'timeout'.",
             required=('keyword', 'timeout'),
             extra=dict(keyword,
                        timeout=dict(time, description='Give up after this long.'),
                        interval=dict(time, description=f'Time between attempts; '
                                                        f'default {DEFAULT_GATE_INTERVAL}.'),
                        on_timeout={'enum': [ON_TIMEOUT_UNKNOWN, ON_TIMEOUT_FAIL],
                                    'description': "Verdict on timeout: 'unknown' (the "
                                                   "default: nothing was tested) or 'fail'."})),
        kind(SLEEP, 'Wait a fixed time.', required=('duration',),
             extra={'duration': time}),
        kind(DECISION, "Branch: edges labelled 'yes' and 'no', which re-join later.",
             required=('condition',),
             extra={'condition': {'type': 'string', 'pattern': r'\S',
                                  'description': "Python expression in '$var' syntax, "
                                                 "e.g. \"$MODE == 'EMC'\"."}}),
        kind(LOOP, "Bounded repetition: 'body' in, 'next' back, 'done' out, optional "
                   "'on_failure'.",
             extra={'max_loops': {'anyOf': [{'type': 'integer', 'minimum': 1},
                                            {'type': 'string', 'pattern': VARIABLE_PATTERN}],
                                  'description': "Fixed number, or a '${variable}' "
                                                 "holding one (set in 'variables' or "
                                                 "with --variable)."},
                    'max_seconds': dict(time, description='Time limit of the loop.'),
                    'every': dict(time, description='Minimum time per round.')},
             anyOf=[{'required': ['max_loops']}, {'required': ['max_seconds']}]),
        kind(TRY, "Failure routing without a loop: 'body', 'on_failure', 'done'."),
        kind(PARALLEL, "Branches that run at the same time: two or more 'branch' edges "
                       "to their first nodes, a 'join' edge back from the last node of "
                       "each, 'done' out, optional 'on_failure'.",
             extra={'timeout': dict(time, description='The branches must all have joined '
                                                      'within this time (on the flow '
                                                      'clock); none by default.'),
                    'stop_others': {'type': 'boolean',
                                    'description': 'When a branch fails, stop the other '
                                                   'branches at their next step (true, '
                                                   'the default) or let them run to '
                                                   'their end (false).'}}),
        kind(FLOW, 'Call another flow file (a sub-flow) as one step.', required=('file',),
             extra={'file': {'type': 'string', 'pattern': r'\S',
                             'description': 'The sub-flow file, relative to this file. '
                                            'It has no phases; its variables are its '
                                            'parameters.'},
                    'args': {'type': 'object', 'additionalProperties': scalar,
                             'description': "Values for the sub-flow's parameters, "
                                            'by name: {"VOLTS": "${VOLTS}"}.'}}),
    ])
    node = {
        'type': 'object', 'required': ['id', 'kind'],
        'properties': {'kind': common['kind']},
        'allOf': [{'if': {'properties': {'kind': {'const': name}}, 'required': ['kind']},
                   'then': {'$ref': f'#/definitions/{name}'}} for name in kinds],
    }
    edge = {'oneOf': [
        {'type': 'array', 'minItems': 2, 'maxItems': 2,
         'items': {'type': 'string'}, 'description': '[from, to]: plain "then".'},
        {'type': 'object', 'required': ['from', 'to'], 'additionalProperties': False,
         'properties': {'from': {'type': 'string'}, 'to': {'type': 'string'},
                        'label': {'enum': list(EDGE_LABELS),
                                  'description': 'Needed only where a node has more '
                                                 "than one way out. 'branch' (several "
                                                 "per parallel node) and 'join' belong "
                                                 'to parallel regions.'}}},
    ]}
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        '$id': 'https://github.com/test-fullautomation/robotframework/flow.schema.json',
        'title': 'Robot Framework flow file',
        'description': 'A test plan as nodes and edges, run with '
                       "'robot --parser robot.flow'.",
        'type': 'object', 'required': ['flow', 'nodes', 'edges'],
        'additionalProperties': False,
        'properties': {
            '$schema': {'type': 'string'},
            'flow': {'type': 'object', 'required': ['name'], 'additionalProperties': False,
                     'properties': {
                         'name': {'type': 'string', 'pattern': r'\S'},
                         'version': {'const': FLOW_VERSION},
                         'checkpoint': {
                             'type': ['string', 'boolean'],
                             'description': 'File that records how far the flow got, '
                                            'so a stopped or crashed run can continue. '
                                            'Default: <flow file name>.checkpoint.json '
                                            'in the output directory; false for none.'},
                         'checkpoint_every': {
                             'type': 'integer', 'minimum': 1,
                             'description': 'Write the checkpoint every this many loop '
                                            'iterations instead of at most once a '
                                            'second.'}}},
            'imports': {'type': 'object', 'additionalProperties': False,
                        'properties': {
                            'libraries': {'type': 'array', 'items': named,
                                          'description': 'Python files or modules, '
                                                         'optionally [name, args...].'},
                            'resources': {'type': 'array',
                                          'items': {'type': 'string', 'minLength': 1}},
                            'variables': {'type': 'array', 'items': named,
                                          'description': 'Variable files.'}}},
            'variables': {'type': 'object', 'additionalProperties': scalar,
                          'description': 'Default values; override with --variable.'},
            'nodes': {'type': 'array', 'minItems': 1, 'items': node},
            'edges': {'type': 'array', 'items': edge},
        },
        'definitions': kinds,
    }
