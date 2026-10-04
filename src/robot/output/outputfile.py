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

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from robot.errors import DataError
from robot.utils import get_error_message

from .jsonlogger import JsonLogger
from .loggerapi import LoggerApi
from .loggerhelper import Message
from .loglevel import LEVELS, LogLevel
from .xmllogger import LegacyXmlLogger, NullLogger, XmlLogger

# cuongnht: levels a literal `Log` keyword argument is matched against when
# deciding whether the keyword element is written at all. Deliberately not
# LEVELS, which contains statuses as well.
LOG_KEYWORD_LEVELS = ("ERROR", "WARN", "USER", "INFO", "DEBUG", "TRACE")


def level_from_log_keyword_args(args):
    """Returns the explicit literal level argument of a `BuiltIn.Log` call.

    ``None`` when the level is absent or non-literal (e.g. a ``${variable}``
    that cannot be resolved at write time).
    """
    for arg in args or ():
        if isinstance(arg, str) and arg in LOG_KEYWORD_LEVELS:
            return arg
    return None


class OutputFile(LoggerApi):
    # cuongnht memory cap: WARN/ERROR/UNKNOWN messages are collected for the
    # <errors> section until the run ends. Only the first `max_errors` stay
    # in memory; further ones are spilled to a sidecar file next to the
    # output file and read back when the section is written, so the section
    # stays complete with bounded memory. Without an output file the
    # overflow is only counted.
    max_errors = 10000
    # cuongnht log level shortening: suppress `BuiltIn.Log` keyword elements
    # whose explicit literal level argument is below the log level. The
    # element is only held back, not dropped outright: if anything visible is
    # logged inside it (e.g. the FAIL message when the keyword fails), the
    # element is written after all so the output stays balanced and failures
    # stay visible. Applies only while executing tests; re-serializing
    # existing results (rebot) goes through XmlLogger directly and preserves
    # the data.
    suppress_log_keywords = True

    def __init__(
        self,
        path: "Path | None",
        log_level: LogLevel,
        rpa: bool = False,
        legacy_output: bool = False,
        segment_interval: "float | None" = None,
    ):
        # cuongnht add segmented output: interval in seconds or None.
        self._segment_interval = segment_interval
        # `self.logger` is replaced with `NullLogger` when flattening.
        self.logger = self.real_logger = self._get_logger(path, rpa, legacy_output)
        self.log_level = log_level
        self.is_logged = log_level.is_logged
        self.flatten_level = 0
        self.errors = []
        self._errors_dropped = 0
        self._errors_spill_path = None
        self._errors_spill_file = None
        self._errors_spill_lock = threading.Lock()
        self._path = path
        self._delayed_messages = None
        # Held-back `BuiltIn.Log` keywords, keyed by thread name because
        # each thread streams to its own writer.
        self._pending_log_kws = {}

    def _get_logger(self, path, rpa, legacy_output):
        if not path:
            return NullLogger()
        try:
            file = open(path, "w", encoding="UTF-8")
        except Exception:
            raise DataError(
                f"Opening output file '{path}' failed: {get_error_message()}"
            )
        if path.suffix.lower() == ".json":
            if self._segment_interval:
                from .logger import LOGGER

                LOGGER.warn("Segmented output is not supported with JSON output.")
            return JsonLogger(file, rpa)
        # cuongnht add thread: the path is needed to derive per-thread files.
        logger_class = LegacyXmlLogger if legacy_output else XmlLogger
        return logger_class(
            file, rpa, path=path, segment_interval=self._segment_interval
        )

    @property
    def thread_output_files(self):
        # cuongnht add thread: {thread name: path} of the per-thread files
        # written by THREAD blocks, to be merged into the main output.
        return getattr(self.real_logger, "thread_output_files", {})

    @property
    def segment_paths(self):
        # cuongnht add segmented output: sealed segments of the main output.
        return getattr(self.real_logger, "segment_paths", [])

    @property
    @contextmanager
    def delayed_logging(self):
        self._delayed_messages, previous = [], self._delayed_messages
        try:
            yield
        finally:
            self._release_delayed_messages()
            self._delayed_messages = previous

    @property
    @contextmanager
    def delayed_logging_paused(self):
        self._release_delayed_messages()
        self._delayed_messages = None
        try:
            yield
        finally:
            self._delayed_messages = []

    def _release_delayed_messages(self):
        for msg in self._delayed_messages or ():
            self.log_message(msg, no_delay=True)

    def start_suite(self, data, result):
        self.logger.start_suite(result)

    def end_suite(self, data, result):
        self.logger.end_suite(result)

    def start_test(self, data, result):
        self.logger.start_test(result)

    def end_test(self, data, result):
        self.logger.end_test(result)

    def start_keyword(self, data, result):
        if self._should_suppress_log_kw(result):
            # cuongnht log level shortening: hold the element back;
            # log_message writes it if anything visible is logged inside.
            self._pending_log_kws[threading.current_thread().name] = result
            return
        self._flush_pending_log_kw()
        self.logger.start_keyword(result)
        if result.tags.robot("flatten"):
            self.flatten_level += 1
            self.logger = NullLogger()

    def end_keyword(self, data, result):
        # NOTE: `BuiltIn.Log` has no child items, so a pending entry here
        # always belongs to this keyword.
        pending = self._pending_log_kws.pop(threading.current_thread().name, None)
        if pending is not None:
            if result.status == "PASS":
                # Executed successfully and nothing visible was logged
                # inside: drop the whole element (balanced suppression).
                return
            # NOT RUN (e.g. dry run) or failed without a visible message:
            # structure must stay visible, so write the element after all.
            self.logger.start_keyword(pending)
        if self.flatten_level and result.tags.robot("flatten"):
            self.flatten_level -= 1
            if self.flatten_level == 0:
                self.logger = self.real_logger
        self.logger.end_keyword(result)

    def _should_suppress_log_kw(self, result):
        if not self.suppress_log_keywords or result.full_name != "BuiltIn.Log":
            return False
        level = level_from_log_keyword_args(result.args)
        return level is not None and LEVELS[level] < self.log_level.priority

    def _flush_pending_log_kw(self):
        kw = self._pending_log_kws.pop(threading.current_thread().name, None)
        if kw is not None:
            self.logger.start_keyword(kw)

    def start_for(self, data, result):
        self.logger.start_for(result)

    def end_for(self, data, result):
        self.logger.end_for(result)

    def start_for_iteration(self, data, result):
        self.logger.start_for_iteration(result)

    def end_for_iteration(self, data, result):
        self.logger.end_for_iteration(result)

    def start_while(self, data, result):
        self.logger.start_while(result)

    def end_while(self, data, result):
        self.logger.end_while(result)

    def start_while_iteration(self, data, result):
        self.logger.start_while_iteration(result)

    def end_while_iteration(self, data, result):
        self.logger.end_while_iteration(result)

    def start_if(self, data, result):
        self.logger.start_if(result)

    def end_if(self, data, result):
        self.logger.end_if(result)

    def start_if_branch(self, data, result):
        self.logger.start_if_branch(result)

    def end_if_branch(self, data, result):
        self.logger.end_if_branch(result)

    def start_try(self, data, result):
        self.logger.start_try(result)

    def end_try(self, data, result):
        self.logger.end_try(result)

    def start_try_branch(self, data, result):
        self.logger.start_try_branch(result)

    def end_try_branch(self, data, result):
        self.logger.end_try_branch(result)

    def start_group(self, data, result):
        self.logger.start_group(result)

    def end_group(self, data, result):
        self.logger.end_group(result)

    def start_thread(self, data, result):
        # cuongnht add thread
        self.logger.start_thread(result)

    def end_thread(self, data, result):
        self.logger.end_thread(result)

    def start_var(self, data, result):
        self.logger.start_var(result)

    def end_var(self, data, result):
        self.logger.end_var(result)

    def start_break(self, data, result):
        self.logger.start_break(result)

    def end_break(self, data, result):
        self.logger.end_break(result)

    def start_continue(self, data, result):
        self.logger.start_continue(result)

    def end_continue(self, data, result):
        self.logger.end_continue(result)

    def start_return(self, data, result):
        self.logger.start_return(result)

    def end_return(self, data, result):
        self.logger.end_return(result)

    def start_error(self, data, result):
        self.logger.start_error(result)

    def end_error(self, data, result):
        self.logger.end_error(result)

    def log_message(self, message, no_delay=False):
        if self.is_logged(message):
            if self._delayed_messages is None or no_delay:
                # A visible message inside a held-back `BuiltIn.Log` keyword
                # resurrects the keyword element to keep output balanced.
                self._flush_pending_log_kw()
                # Use the real logger also when flattening.
                self.real_logger.message(message)
            else:
                # Logging is delayed when using timeouts to avoid writing to output
                # files being interrupted. There are still problems, though:
                # https://github.com/robotframework/robotframework/issues/5417
                self._delayed_messages.append(message)

    def message(self, message):
        # nhtcuong: UNKNOWN level messages (e.g. import errors) belong to the
        # errors section as well.
        if message.level in ("WARN", "ERROR", "UNKNOWN"):
            if len(self.errors) < self.max_errors:
                self.errors.append(message)
            elif self._path:
                self._spill_error(message)
            else:
                self._errors_dropped += 1

    def _spill_error(self, message):
        """Writes an overflowing errors-section message to the sidecar file."""
        with self._errors_spill_lock:
            if self._errors_spill_file is None:
                base, _ = os.path.splitext(str(self._path))
                self._errors_spill_path = base + "_errors_spill.jsonl"
                self._errors_spill_file = open(
                    self._errors_spill_path, "w", encoding="UTF-8"
                )
            data = {
                "timestamp": message.timestamp.isoformat() if message.timestamp else None,
                "level": message.level,
                "message": message.message,
                "html": message.html,
            }
            json.dump(data, self._errors_spill_file)
            self._errors_spill_file.write("\n")

    def _spilled_errors(self):
        with self._errors_spill_lock:
            spill_file = self._errors_spill_file
            self._errors_spill_file = None
        if spill_file is None:
            return
        spill_file.close()
        try:
            with open(self._errors_spill_path, encoding="UTF-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    yield Message(
                        data["message"],
                        data.get("level", "WARN"),
                        data.get("html", False),
                        data.get("timestamp"),
                    )
            os.remove(self._errors_spill_path)
        except Exception as err:
            yield Message(
                f"Reading spilled error messages from "
                f"'{self._errors_spill_path}' failed: {err}",
                "WARN",
            )

    def _all_errors(self):
        yield from self.errors
        yield from self._spilled_errors()
        if self._errors_dropped:
            # Only possible without an output file (nowhere to spill to).
            yield Message(
                f"{self._errors_dropped} further warning/error messages were "
                f"not collected (in-memory limit {self.max_errors}, no "
                f"output file to spill to).",
                "WARN",
            )

    def statistics(self, stats):
        self.logger.statistics(stats)

    def close(self):
        # A generator: spilled messages are streamed, not loaded at once.
        self.logger.errors(self._all_errors())
        self.logger.close()
