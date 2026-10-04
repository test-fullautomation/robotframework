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

import threading
from pathlib import Path

from robot.errors import DataError
from robot.utils import file_writer, seq2str2, ThreadSafeDict

from .logger import LOGGER
from .loggerapi import LoggerApi
from .loglevel import LEVELS, LogLevel
from .outputfile import level_from_log_keyword_args

# cuongnht log level shortening: output caused by code in this file (suite,
# test and keyword start/end lines, separators) depends on this level,
# compared against the actual log level.
LOG_LEVEL_DEBUG_FILE = "INFO"


def DebugFile(path, log_level=None):
    if not path:
        LOGGER.info("No debug file")
        return None
    try:
        outfile = file_writer(path, usage="debug")
    except DataError as err:
        LOGGER.error(err.message)
        return None
    else:
        LOGGER.info(f"Debug file: {path}")
        return _DebugFileWriter(outfile, log_level)


class _DebugFileWriter(LoggerApi):
    _separators = {"SUITE": "=", "TEST": "-", "KEYWORD": "~", "THREAD": "`"}

    def __init__(self, outfile, log_level=None):
        self._indent = 0
        self._kw_level = 0
        self._separator_written_last = False
        self._outfile = outfile
        self._write_lock = threading.Lock()
        # cuongnht add thread: THREAD workers keep their own indentation and
        # keyword nesting so that the main thread's bookkeeping stays intact.
        self._thread_info = ThreadSafeDict()
        # cuongnht log level shortening: previously hard coded level 'DEBUG'
        # replaced by the actual log level. The LogLevel object is shared
        # with Output, so `Set Log Level` is honoured here as well.
        if isinstance(log_level, str):
            log_level = LogLevel(log_level)
        self._log_level = log_level or LogLevel("DEBUG")
        self._is_logged = self._log_level.is_logged

    def _level_is_logged(self, level):
        return LEVELS[level] >= self._log_level.priority

    # cuongnht add thread: per-thread bookkeeping ---------------------------

    def _in_worker_thread(self):
        return threading.current_thread() is not threading.main_thread()

    def _info(self):
        # Created lazily: with a log level above INFO start_thread does not
        # register the thread, but a keyword with a visible own level (e.g.
        # `Log msg USER` at --loglevel USER) still needs the bookkeeping.
        name = threading.current_thread().name
        if name not in self._thread_info:
            self._thread_info[name] = {"level": 0, "indent": 0}
        return self._thread_info[name]

    def _get_kw_level(self):
        return self._info()["level"] if self._in_worker_thread() else self._kw_level

    def _change_kw_level(self, delta):
        if self._in_worker_thread():
            self._info()["level"] += delta
        else:
            self._kw_level += delta

    # -----------------------------------------------------------------------

    def start_suite(self, data, result):
        if self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator("SUITE")
            self._start("SUITE", data.full_name, result.start_time)
            self._separator("SUITE")

    def end_suite(self, data, result):
        if self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator("SUITE")
            self._end("SUITE", data.full_name, result.end_time, result.elapsed_time)
            self._separator("SUITE")
        if self._indent == 0:
            LOGGER.debug_file(Path(self._outfile.name))
            self.close()

    def start_test(self, data, result):
        if self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator("TEST")
            self._start("TEST", result.name, result.start_time)
            self._separator("TEST")

    def end_test(self, data, result):
        if self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator("TEST")
            self._end("TEST", result.name, result.end_time, result.elapsed_time)
            self._separator("TEST")

    def start_thread(self, data, result):
        # cuongnht add thread: called on the spawning thread and on the worker.
        if not self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            return
        if self._in_worker_thread():
            self._info()
            self._separator("THREAD")
            self._start("THREAD", result.name, result.start_time)
            self._separator("THREAD")

    def end_thread(self, data, result):
        if not self._in_worker_thread():
            return
        if self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            self._separator("THREAD")
            self._end("THREAD", result.name, result.end_time, result.elapsed_time)
            self._separator("THREAD")
        # cuongnht memory cap: forget the worker's bookkeeping.
        self._thread_info.pop(threading.current_thread().name, None)

    def _keyword_level(self, result):
        # `BuiltIn.Log` has its own log level: its start/end lines follow the
        # explicit literal level argument instead of the default.
        if result.full_name == "BuiltIn.Log":
            level = level_from_log_keyword_args(result.args)
            if level is not None:
                return level
        return LOG_LEVEL_DEBUG_FILE

    def start_keyword(self, data, result):
        if not self._level_is_logged(self._keyword_level(result)):
            return
        if self._get_kw_level() == 0:
            self._separator("KEYWORD")
        self._start(
            result.type, result.full_name, result.start_time, seq2str2(result.args)
        )
        self._change_kw_level(1)

    def end_keyword(self, data, result):
        if not self._level_is_logged(self._keyword_level(result)):
            return
        self._end(result.type, result.full_name, result.end_time, result.elapsed_time)
        self._change_kw_level(-1)

    def start_body_item(self, data, result):
        if not self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            return
        if self._get_kw_level() == 0:
            self._separator("KEYWORD")
        self._start(result.type, result._log_name, result.start_time)
        self._change_kw_level(1)

    def end_body_item(self, data, result):
        if not self._level_is_logged(LOG_LEVEL_DEBUG_FILE):
            return
        self._end(result.type, result._log_name, result.end_time, result.elapsed_time)
        self._change_kw_level(-1)

    def log_message(self, msg):
        if self._is_logged(msg):
            self._write(f"{msg.timestamp} - {msg.level} - {msg.message}")

    def close(self):
        if not self._outfile.closed:
            self._outfile.close()

    def _start(self, type, name, timestamp, extra=""):
        if extra:
            extra = f" {extra}"
        if self._in_worker_thread():
            # Worker-thread lines carry their thread name as prefix, which is
            # enough to tell them apart from the main thread's lines.
            info = self._info()
            indent = "-" * info["indent"]
            prefix = f"{threading.current_thread().name}> "
            info["indent"] += 1
        else:
            indent = "-" * self._indent
            prefix = ""
            self._indent += 1
        self._write(
            f"{timestamp} - INFO - {prefix}+{indent} START {type}: {name}{extra}"
        )

    def _end(self, type, name, timestamp, elapsed):
        if self._in_worker_thread():
            info = self._info()
            info["indent"] = max(info["indent"] - 1, 0)
            indent = "-" * info["indent"]
            prefix = f"{threading.current_thread().name}> "
        else:
            self._indent -= 1
            indent = "-" * self._indent
            prefix = ""
        elapsed = elapsed.total_seconds()
        self._write(
            f"{timestamp} - INFO - {prefix}+{indent} END {type}: {name} ({elapsed} s)"
        )

    def _separator(self, type_):
        self._write(self._separators[type_] * 78, separator=True)

    def _write(self, text, separator=False):
        with self._write_lock:
            if separator and self._separator_written_last:
                return
            self._outfile.write(text.rstrip() + "\n")
            self._outfile.flush()
            self._separator_written_last = separator
