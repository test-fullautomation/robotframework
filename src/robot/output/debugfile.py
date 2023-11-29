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

from robot.errors import DataError
from robot.utils import get_timestamp, file_writer, seq2str2

from .logger import LOGGER
from .loggerhelper import IsLogged

#qth2hi
LOG_LEVEL_DEBUG_FILE = "INFO" # output caused by code in this file, depends on this trace level, compared against 'log_level'

def DebugFile(path, log_level):
    if not path:
        LOGGER.info('No debug file')
        return None
    try:
        outfile = file_writer(path, usage='debug')
    except DataError as err:
        LOGGER.error(err.message)
        return None
    else:
        LOGGER.info('Debug file: %s' % path)
        return _DebugFileWriter(outfile, log_level)


class _DebugFileWriter:
    _separators = {'SUITE': '=', 'TEST': '-', 'KEYWORD': '~'}

    def __init__(self, outfile, log_level):
        self._indent = 0
        self._kw_level = 0
        self._separator_written_last = False
        self._outfile = outfile

        #qth2hi
        #previously hard coded level 'DEBUG' replaced by actual level 'log_level'
        self._is_logged = IsLogged(log_level)


    def get_level_from_kw_args(self, args=None): # qth2hi
        # args expected to be a 'kw.args' tuple
        supported_levels = ('ERROR', 'WARN', 'USER', 'INFO', 'DEBUG', 'TRACE') # not using LEVELS from loggerhelper.py here, because of more states inside there. A more strict separation is desired here.
        identified_level = 'INFO' # the everywhere used default level 'INFO' is used as default here also
        if args is None:
            return identified_level
        for arg in args:
            if arg in supported_levels:
                identified_level = arg
                break
        return identified_level


    def start_suite(self, suite):
        if self._is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator('SUITE')
            self._start('SUITE', suite.longname)
            self._separator('SUITE')

    def end_suite(self, suite):
        if self._is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator('SUITE')
            self._end('SUITE', suite.longname, suite.elapsedtime)
            self._separator('SUITE')
            if self._indent == 0:
                LOGGER.output_file('Debug', self._outfile.name)
                self.close()

    def start_test(self, test):
        if self._is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator('TEST')
            self._start('TEST', test.name)
            self._separator('TEST')

    def end_test(self, test):
        if self._is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator('TEST')
            self._end('TEST', test.name, test.elapsedtime)
            self._separator('TEST')

    def start_keyword(self, kw):

        log_kw_start = True

        #qth2hi
        if kw.name == 'BuiltIn.Log':
            msg_level = self.get_level_from_kw_args(kw.args)
            if self._is_logged(msg_level):
                log_kw_start = True
            else:
                # suppress the logging of the start of a BuiltIn.Log keyword in case of no message is logged
                # because of a trace level mismatch (to avoid useless content in debug log file)
                log_kw_start = False

        if log_kw_start is True:
            if self._kw_level == 0:
                self._separator('KEYWORD')
            self._start(kw.type, kw.name, kw.args)
            self._kw_level += 1

    def end_keyword(self, kw):

        log_kw_end = True

        #qth2hi
        if kw.name == 'BuiltIn.Log':
            msg_level = self.get_level_from_kw_args(kw.args)
            if self._is_logged(msg_level):
                log_kw_end = True
            else:
                # suppress the logging of the end of a BuiltIn.Log keyword in case of no message is logged
                # because of a trace level mismatch (to avoid useless content in debug log file)
                log_kw_end = False

        if log_kw_end is True:
            self._end(kw.type, kw.name, kw.elapsedtime)
            self._kw_level -= 1

    def log_message(self, msg):
        if self._is_logged(msg.level):
            self._write(msg.message, level=msg.level, timestamp=msg.timestamp)

    def close(self):
        if not self._outfile.closed:
            self._outfile.close()

    def _start(self, type_, name, args=''):
        args = ' ' + seq2str2(args)
        if self._is_logged(LOG_LEVEL_DEBUG_FILE):
            self._write('+%s START %s: %s%s' % ('-'*self._indent, type_, name, args))
            self._indent += 1

    def _end(self, type_, name, elapsed):
        if self._is_logged(LOG_LEVEL_DEBUG_FILE):
            self._indent -= 1
            self._write('+%s END %s: %s (%s)' % ('-'*self._indent, type_, name, elapsed))

    def _separator(self, type_):
        self._write(self._separators[type_] * 78, separator=True)

    def _write(self, text, separator=False, level='INFO', timestamp=None):
        if separator and self._separator_written_last:
            return
        if not separator:
            text = '%s - %s - %s' % (timestamp or get_timestamp(), level, text)
        self._outfile.write(text.rstrip() + '\n')
        self._outfile.flush()
        self._separator_written_last = separator
