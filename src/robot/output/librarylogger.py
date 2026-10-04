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

"""Implementation of the public logging API for libraries.

This is exposed via :py:mod:`robot.api.logger`. Implementation must reside
here to avoid cyclic imports.
"""

from threading import current_thread
from typing import Literal

from robot.utils import safe_str

from .logger import LOGGER
from .loggerhelper import Message, MessageLevel, PseudoLevel, write_to_console

# This constant is used by BackgroundLogger.
# https://github.com/robotframework/robotbackgroundlogger
LOGGING_THREADS = ["MainThread", "RobotFrameworkTimeoutThread"]


def add_thread_logging(thread_name):
    # cuongnht add thread: messages logged by THREAD workers must reach
    # the output like messages from the main thread.
    if thread_name not in LOGGING_THREADS:
        LOGGING_THREADS.append(thread_name)


def remove_thread_logging(thread_name):
    while thread_name in LOGGING_THREADS:
        LOGGING_THREADS.remove(thread_name)


def write(
    msg: object,
    level: "MessageLevel | PseudoLevel" = "INFO",
    html: bool = False,
    console: "bool | None" = None,
):
    if not isinstance(msg, str):
        msg = safe_str(msg)
    if level == "FAIL":
        raise ValueError(f"Invalid log level '{level}'.")
    if current_thread().name in LOGGING_THREADS:
        LOGGER.log_message(Message(msg, level, html=html, console=console))


def trace(msg, html=False):
    write(msg, "TRACE", html)


def debug(msg, html=False):
    write(msg, "DEBUG", html)


def info(msg, html=False, console=False):
    write(msg, "INFO", html, console)


def user(msg, html=False):
    # nhtcuong: fork extension, see the USER level in loglevel.py.
    write(msg, "USER", html)


def warn(msg, html=False):
    write(msg, "WARN", html)


def error(msg, html=False):
    write(msg, "ERROR", html)


def console(
    msg: object,
    newline: bool = True,
    stream: Literal["stdout", "stderr"] = "stdout",
):
    write_to_console(msg, newline, stream)
