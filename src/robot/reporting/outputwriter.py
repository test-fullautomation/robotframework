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

from robot.output.xmllogger import XmlLogger


class OutputWriter(XmlLogger):

    # Serialization must preserve existing results: suppressing BuiltIn.Log
    # keyword elements here would orphan their <msg> children.
    suppress_log_keywords = False

    def __init__(self, output, rpa=False):
        XmlLogger.__init__(self, output, rpa=rpa, generator='Rebot')

    def start_message(self, msg):
        self._write_message(msg)

    # XmlLogger's thread methods implement the run-time split between the
    # main writer (a placeholder) and the worker thread's own file. When
    # serializing a result model there is no worker: the element is opened
    # here, the visitor writes the body, and end_thread closes it - the same
    # as for any other control structure.
    def start_thread(self, thread_):
        self._writer.start('thread', {'name': thread_.name,
                                      'daemon': str(thread_.daemon)})
        self._writer.element('doc', thread_.doc)

    def end_thread(self, thread_):
        self._write_status(thread_)
        self._writer.end('thread')

    def close(self):
        self._writer.end('robot')
        self._writer.close()

    def end_result(self, result):
        self.close()
