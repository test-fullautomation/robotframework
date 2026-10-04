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

from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from .loggerhelper import Message


SettableLevel = Literal["TRACE", "DEBUG", "INFO", "USER", "WARN", "ERROR", "NONE"]
# nhtcuong: USER and UNKNOWN are fork extensions. The numbers have to fit
# 'var LEVELS' in htmldata/rebot/testdata.js.
LEVELS = {
    "NONE": 9,
    "UNKNOWN": 8,
    "SKIP": 7,
    "FAIL": 6,
    "ERROR": 5,
    "WARN": 4,
    "USER": 3,
    "INFO": 2,
    "DEBUG": 1,
    "TRACE": 0,
}


class LogLevel:

    def __init__(self, level: SettableLevel):
        self.level, self.priority = self._validate_level(level)

    def is_logged(self, msg: "Message") -> bool:
        return LEVELS[msg.level] >= self.priority and msg.message is not None

    def set(self, level: SettableLevel) -> SettableLevel:
        old = self.level
        self.__init__(level)
        return old

    def _validate_level(self, level) -> "tuple[SettableLevel, int]":
        upper = level.upper()
        if upper not in LEVELS or upper in ("SKIP", "FAIL", "UNKNOWN"):
            raise ValueError(f"Invalid log level '{level}'.")
        return upper, LEVELS[upper]
