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

``validate`` checks the file's shape and structure and prints the phases.
``render`` prints the equivalent ``.robot`` text of the suite that would run.
Running a flow is done with ``robot --parser robot.flow <file.flow.json>``.
"""

import argparse
import sys

from robot.errors import DataError

from . import build_flow_suite, load_flow, render_robot, structure


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m robot.flow',
                                     description=__doc__.split('\n\n')[1])
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate', help='check shape and structure')
    validate.add_argument('file')
    render = commands.add_parser('render', help='print the equivalent .robot text')
    render.add_argument('file')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            _validate(args.file)
        else:
            sys.stdout.write(render_robot(build_flow_suite(args.file)))
    except DataError as err:
        sys.stderr.write(f'{args.file}: {err}\n')
        return 1
    return 0


def _validate(path):
    flow = structure(load_flow(path))
    phases = []
    if flow.setup:
        phases.append('setup')
    phases.extend(f"test '{phase.name}'" for phase in flow.tests)
    if flow.teardown:
        phases.append('teardown')
    print(f"{path}: OK. Flow '{flow.name}' with {', '.join(phases)}.")


if __name__ == '__main__':
    sys.exit(main())
