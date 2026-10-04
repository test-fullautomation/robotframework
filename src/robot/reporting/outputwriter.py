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

from robot.output.xmllogger import LegacyXmlLogger, XmlLogger


class _ThreadSerialization:
    # cuongnht add thread: XmlLogger's thread methods implement the run-time
    # split between the main writer (a placeholder) and the worker's own
    # file. When serializing a result model there is no worker: the element
    # is opened here, the visitor writes the body, and end_thread closes it,
    # the same as for any other control structure.

    def start_thread(self, thread):
        self._writer.start("thread", {"name": thread.name, "daemon": str(thread.daemon)})

    def end_thread(self, thread):
        self._writer.element("doc", thread.doc)
        self._write_status(thread)
        self._writer.end("thread")


class OutputWriter(_ThreadSerialization, XmlLogger):
    generator = "Rebot"

    def end_result(self, result):
        self.close()


class LegacyOutputWriter(_ThreadSerialization, LegacyXmlLogger):
    generator = "Rebot"

    def end_result(self, result):
        self.close()
