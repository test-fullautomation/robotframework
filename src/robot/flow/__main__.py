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

"""Command line tools for flow files.

    python -m robot.flow validate <file.flow.json>
    python -m robot.flow render   <file.flow.json>
    python -m robot.flow schema   [--output flow.schema.json]
    python -m robot.flow control  <signal store> pause|resume|stop|status [--rig NAME]

``validate`` checks the file's shape and structure and prints the phases.
``render`` prints the equivalent ``.robot`` text of the suite that would run.
``schema`` prints the JSON Schema of flow files, for editor support.
``control`` pauses, resumes or stops the flows that share the signal store
(the file in ``ROBOT_FLOW_SIGNALS``), or one of them with ``--rig``; ``status``
shows what they are doing. A stopped flow writes a checkpoint and continues
when it is run again.
Running a flow is done with ``robot --parser robot.flow <file.flow.json>``.
"""

import argparse
import json
import sys
import time

from robot.errors import DataError

from . import build_flow_suite, load_flow, render_robot, structure
from .schema import json_schema


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m robot.flow',
                                     description=__doc__.split('\n\n')[1])
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate', help='check shape and structure')
    validate.add_argument('file')
    render = commands.add_parser('render', help='print the equivalent .robot text')
    render.add_argument('file')
    schema = commands.add_parser('schema', help='print the JSON Schema of flow files')
    schema.add_argument('--output', '-o', help='write it to this file instead')
    control = commands.add_parser('control', help='pause, resume or stop running flows')
    control.add_argument('store', help="the run's signal store (ROBOT_FLOW_SIGNALS)")
    control.add_argument('action', choices=['pause', 'resume', 'stop', 'status'])
    control.add_argument('--rig', help='only the flow started with this FLOW_RIG / '
                                       'ROBOT_FLOW_RIG; default: all of them')
    args = parser.parse_args(argv)
    if args.command == 'schema':
        return _schema(args.output)
    if args.command == 'control':
        return _control(args.store, args.action, args.rig)
    try:
        if args.command == 'validate':
            _validate(args.file)
        else:
            sys.stdout.write(render_robot(build_flow_suite(args.file)))
    except DataError as err:
        sys.stderr.write(f'{args.file}: {err}\n')
        return 1
    return 0


def schema_text():
    """The JSON Schema as written to ``flow.schema.json``."""
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + '\n'


def _schema(output):
    if output:
        with open(output, 'w', encoding='UTF-8', newline='\n') as file:
            file.write(schema_text())
    else:
        sys.stdout.write(schema_text())
    return 0


def _control(store, action, rig=None):
    from .control import CONTROL_SIGNAL, STATE_SIGNAL
    from .signals import FlowSignals

    backend = FlowSignals(store=store)._store
    name = f'{CONTROL_SIGNAL}.{rig}' if rig else CONTROL_SIGNAL
    if action != 'status':
        backend.set(name, action)
        print(f"{action} -> {'rig ' + rig if rig else 'all flows'} of '{store}'.")
        return 0
    entries = backend._read() if hasattr(backend, '_read') else {}
    commands = {key: entry for key, entry in entries.items()
                if key == CONTROL_SIGNAL or key.startswith(CONTROL_SIGNAL + '.')}
    states = {key: entry for key, entry in entries.items()
              if key.startswith(STATE_SIGNAL + '.')}
    if not commands and not states:
        print(f"No flow has used '{store}' yet.")
        return 0
    for key, entry in sorted(commands.items()):
        target = key[len(CONTROL_SIGNAL) + 1:] or 'all flows'
        print(f"last command for {target}: {entry.get('value')} ({_age(entry)} ago)")
    for key, entry in sorted(states.items()):
        state = entry.get('value') or {}
        where = ''
        if isinstance(state, dict):
            if state.get('phase'):
                where = f", phase '{state['phase']}'"
            if state.get('loop'):
                where += f", loop '{state['loop']}' iteration {state.get('iteration', 0) + 1}"
            state = state.get('state')
        print(f"{key[len(STATE_SIGNAL) + 1:]}: {state}{where} ({_age(entry)} ago)")
    return 0


def _age(entry):
    from robot.utils import secs_to_timestr
    return secs_to_timestr(max(0, round(time.time() - entry.get('time', time.time()))))


def _validate(path):
    flow = structure(load_flow(path))
    phases = []
    if flow.setup:
        phases.append('setup')
    phases.extend(f"test '{phase.name}'" for phase in flow.tests)
    if flow.teardown:
        phases.append('teardown')
    # Building the suite also loads and checks every sub-flow file.
    suite = build_flow_suite(path)
    subflows = [kw.name for kw in suite.resource.keywords if kw.name.startswith('Flow: ')]
    calls = f", calling sub-flow(s) {', '.join(n[6:] for n in subflows)}" if subflows else ''
    print(f"{path}: OK. Flow '{flow.name}' with {', '.join(phases)}{calls}.")


if __name__ == '__main__':
    sys.exit(main())
