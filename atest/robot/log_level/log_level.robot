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
# Test file log_level.robot with Log keyword calls for all available log levels.
#
# This file is executed by log_level_trigger.robot with all available log levels.
#
# --------------------------------------------------------------------------------------------------------------

*** Settings ***

Library           Collections
Library           BuiltIn

# 'ClogLevelTest' defines all required test settings, invl. versioning
Library    ./libs/ClogLevelTest.py

Documentation    "log_level" test suite

*** Test Cases ***

# TODO: Read from resource file
# TODO: Read from Python keyword library
# TODO: Execute keyword with Log keywords

# TODO: disclaimer?

# **************************************************************************************************************

# Get Single Log Message:
# * parameter 1: the origin of the 'Log' keyowrd; one of "ROBOT_FILE", "RESOURCE_FILE", "PYTHON_LIBRARY"
# * parameter 2: the log level of the 'Log' keyowrd

Log Level Test

   # -- with explicite log level

   ${log_message}=    Get Single Log Message    ROBOT_FILE    ERROR
   Log    ${log_message}    ERROR

   ${log_message}=    Get Single Log Message    ROBOT_FILE    WARN
   Log    ${log_message}    WARN

   ${log_message}=    Get Single Log Message    ROBOT_FILE    USER
   Log    ${log_message}    USER

   ${log_message}=    Get Single Log Message    ROBOT_FILE    INFO
   Log    ${log_message}    INFO

   ${log_message}=    Get Single Log Message    ROBOT_FILE    DEBUG
   Log    ${log_message}    DEBUG

   ${log_message}=    Get Single Log Message    ROBOT_FILE    TRACE
   Log    ${log_message}    TRACE

   # -- without explicite log level (default log level assumed to be INFO)

   ${log_message}=    Get Single Log Message    ROBOT_FILE    DEFAULT
   Log    ${log_message}


# **************************************************************************************************************

