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

"""Flow runner: a flow file executed as a runtime-built suite.

A *flow file* (``*.flow.json``) describes a test plan as a small graph of
nodes and edges: setup, a bounded cycle loop, recovery, teardown. Robot
Framework parses it directly and builds a :class:`~robot.running.TestSuite`
in memory, so the drawing of the plan is the executable::

    robot --parser robot.flow --variable RIG:RIG_A flows/endurance_cycle.flow.json
    robot --parser robot.flow --dryrun flows/endurance_cycle.flow.json

Programmatic use::

    from robot.flow import build_flow_suite
    suite = build_flow_suite('flows/endurance_cycle.flow.json')
    suite.run(outputdir='out')

The pieces, in order: :mod:`~robot.flow.schema` validates the file's shape,
:mod:`~robot.flow.graph` folds the graph into phases and structured steps,
:mod:`~robot.flow.builder` emits the suite, :mod:`~robot.flow.keywords`
holds the generic ``Flow Gate`` keyword, and :mod:`~robot.flow.render`
prints the equivalent ``.robot`` text for review. :mod:`~robot.flow.signals`
is a library flows import to talk to each other across processes (``Set
Signal``, ``Signal Should Be``).
"""

from pathlib import Path

from .builder import build_suite
from .graph import Flow, structure
from .render import render_robot
from .schema import FLOW_VERSION, FlowError, load_flow


EXTENSION = 'flow.json'


def build_flow_suite(source, origins=None):
    """Build a runnable suite from the flow file at ``source``.

    ``origins``, when a dict is given, records which node each built item
    comes from (see :func:`robot.flow.builder.build_suite`).
    """
    path = Path(source)
    flow = structure(load_flow(path))
    return build_suite(flow, source=path, origins=origins)


class FlowParser:
    """Custom parser for ``--parser robot.flow`` or ``--parser robot.flow.FlowParser``."""

    EXTENSION = EXTENSION

    def parse(self, source, defaults=None):
        suite = build_flow_suite(source)
        if defaults:
            # Test setup, teardown, tags and timeout inherited from __init__
            # files apply to flow tests as to any other test.
            for test in suite.tests:
                defaults.set_to(test)
        return suite


def parse(source, defaults=None):
    """Module-level entry point so ``--parser robot.flow`` works as well."""
    return FlowParser().parse(source, defaults)
