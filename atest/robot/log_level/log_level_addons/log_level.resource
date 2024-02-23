# **************************************************************************************************************
#
#  Copyright 2020-2024 Robert Bosch GmbH
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
#
# **************************************************************************************************************
#
# Resource file log_level.resource with Log keyword calls for all available log levels.
#
# The keyword in this file is executed in log_level.robot.
#
# --------------------------------------------------------------------------------------------------------------

*** Settings ***

Documentation     Log keyword calls for all available log levels.

*** Settings ***

Library    ../libs/ClogLevelTest.py

# --------------------------------------------------------------------------------------------------------------

*** Keywords ***

# Get Single Log Message:
# * parameter 1: the origin of the 'Log' keyowrd; one of "ROBOT_FILE", "RESOURCE_FILE", "PYTHON_LIBRARY"
# * parameter 2: the log level of the 'Log' keyowrd

Log Levels In Resource File
   [Documentation]    Executes the "Log" keyword with all available log levels

   # -- with explicite log level

   ${bAck}    ${log_message}=    Get Single Log Message    RESOURCE_FILE    ERROR
   Log    ${log_message}    ERROR
   Should Be Equal    ${bAck}    ${True}    ${log_message}

   ${bAck}    ${log_message}=    Get Single Log Message    RESOURCE_FILE    WARN
   Log    ${log_message}    WARN
   Should Be Equal    ${bAck}    ${True}    ${log_message}

   ${bAck}    ${log_message}=    Get Single Log Message    RESOURCE_FILE    USER
   Log    ${log_message}    USER
   Should Be Equal    ${bAck}    ${True}    ${log_message}

   ${bAck}    ${log_message}=    Get Single Log Message    RESOURCE_FILE    INFO
   Log    ${log_message}    INFO
   Should Be Equal    ${bAck}    ${True}    ${log_message}

   ${bAck}    ${log_message}=    Get Single Log Message    RESOURCE_FILE    DEBUG
   Log    ${log_message}    DEBUG
   Should Be Equal    ${bAck}    ${True}    ${log_message}

   ${bAck}    ${log_message}=    Get Single Log Message    RESOURCE_FILE    TRACE
   Log    ${log_message}    TRACE
   Should Be Equal    ${bAck}    ${True}    ${log_message}

   # -- without explicite log level (default log level assumed to be INFO)

   ${bAck}    ${log_message}=    Get Single Log Message    RESOURCE_FILE    DEFAULT
   Log    ${log_message}
   Should Be Equal    ${bAck}    ${True}    ${log_message}


# **************************************************************************************************************

