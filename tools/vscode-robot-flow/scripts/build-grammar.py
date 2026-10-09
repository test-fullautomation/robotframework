"""Write syntaxes/robotframework.tmLanguage.json (the TextMate grammar of
.robot / .resource files). Edit the rules here, then run:

    python scripts/build-grammar.py

Kept as Python so the regular expressions stay readable (raw strings, no
JSON escaping). Scopes are the common ones colour themes know: section
headers as headings, test and keyword names as function definitions, calls
as functions, settings as storage/keywords, control words as control
keywords, variables, named arguments as parameters, comments.
"""

import json
import os

CELL = r"[^\s#](?:[^\t ]| (?! ))*"   # one cell: up to two spaces or a tab
SEP = r"(?: {2,}|\t| \| )+"
END = r"(?= {2,}|\t| \||\s*$)"
CONTROL = r"FOR|END|IF|ELSE IF|ELSE|WHILE|TRY|EXCEPT|FINALLY|BREAK|CONTINUE|RETURN|VAR|GROUP|THREAD"
INLINE_CONTROL = r"IN RANGE|IN ENUMERATE|IN ZIP|IN|AND|ELSE IF|ELSE|AS|type=|limit=|on_limit="

variables = {"patterns": [
    {"name": "meta.embedded.expression.robotframework", "match": r"\$\{\{.*?\}\}"},
    {"name": "variable.other.robotframework",
     "match": r"[$@&%]\{(?:[^{}]|\{[^{}]*\})*\}(?:\[[^\]]*\])*"},
    {"name": "constant.character.escape.robotframework", "match": r"\\(?:[ntr\\$@&%#=|]|x[0-9a-fA-F]{2})"},
]}

common = [
    {"include": "#comment"},
    {"include": "#variables"},
    {"comment": "name=value argument",
     "match": r"(?<=  |\t)([A-Za-z_][\w ]*?)(=)(?!=)",
     "captures": {"1": {"name": "variable.parameter.robotframework"},
                  "2": {"name": "keyword.operator.assignment.robotframework"}}},
    {"comment": "IN, IN RANGE, AND, ELSE inside a line",
     "match": r"(?<=  |\t)(" + INLINE_CONTROL + r")" + END,
     "name": "keyword.control.robotframework"},
]

comment = {"patterns": [
    {"name": "comment.line.number-sign.robotframework", "match": r"(?:^|(?<=  |\t)|(?<=^ ))#.*$"},
]}

header = lambda names, scope: {   # noqa: E731
    "begin": r"(?i)^(\*+\s*(?:" + names + r")\s*\**)(.*)$",
    "beginCaptures": {"1": {"name": "entity.name.section.robotframework markup.heading.robotframework"},
                      "2": {"name": "comment.line.robotframework"}},
    "end": r"^(?=\*)",
    "name": scope,
}

continuation = {"match": r"^(\s*)(\.\.\.)(?=\s|$)",
                "captures": {"2": {"name": "punctuation.separator.continuation.robotframework"}}}

keyword_call = lambda prefix, prefix_caps: {   # noqa: E731
    "match": prefix + r"((?:[$@&]\{[^}]*\}\s?=?" + SEP + r")*)(" + CELL + r")",
    "captures": dict(prefix_caps, **{
        str(len(prefix_caps) + 1): {"patterns": [{"include": "#variables"}]},
        str(len(prefix_caps) + 2): {"name": "support.function.robotframework",
                                    "patterns": [{"include": "#variables"}]},
    }),
}

body = [   # Test Cases, Tasks, Keywords
    {"include": "#comment"},
    {"comment": "a test, task or keyword: its name at the start of the line",
     "match": r"^(?![\s#*]|\.\.\.)(" + CELL + r")",
     "captures": {"1": {"name": "entity.name.function.robotframework",
                        "patterns": [{"include": "#variables"}]}}},
    {"comment": "documentation",
     "match": r"(?i)^(\s+)(\[Documentation\])(.*)$",
     "captures": {"2": {"name": "keyword.other.setting.robotframework"},
                  "3": {"name": "string.unquoted.documentation.robotframework"}}},
    {"comment": "[Setup] / [Teardown] / [Template]: the next cell is a keyword",
     "match": r"(?i)^(\s+)(\[(?:Setup|Teardown|Template)\])(" + SEP + r")(" + CELL + r")?",
     "captures": {"2": {"name": "keyword.other.setting.robotframework"},
                  "4": {"name": "support.function.robotframework", "patterns": [{"include": "#variables"}]}}},
    {"match": r"^(\s+)(\[[^\]]+\])", "captures": {"2": {"name": "keyword.other.setting.robotframework"}}},
    {"comment": "control structures",
     "match": r"^(\s+)(" + CONTROL + r")" + END,
     "captures": {"2": {"name": "keyword.control.robotframework"}}},
    continuation,
    keyword_call(r"^(\s+)", {"1": {}}),
] + common

settings = [
    {"include": "#comment"},
    {"match": r"(?i)^(Documentation)(" + SEP + r")(.*)$",
     "captures": {"1": {"name": "storage.type.setting.robotframework"},
                  "3": {"name": "string.unquoted.documentation.robotframework"}}},
    {"comment": "imports: the library, resource or variable file",
     "match": r"(?i)^(Library|Resource|Variables)(" + SEP + r")(" + CELL + r")?",
     "captures": {"1": {"name": "storage.type.setting.robotframework"},
                  "3": {"name": "entity.name.namespace.robotframework", "patterns": [{"include": "#variables"}]}}},
    {"comment": "setups and teardowns, templates: the next cell is a keyword",
     "match": r"(?i)^((?:Suite|Test|Task) (?:Setup|Teardown)|(?:Test|Task) Template)(" + SEP + r")(" + CELL + r")?",
     "captures": {"1": {"name": "storage.type.setting.robotframework"},
                  "3": {"name": "support.function.robotframework", "patterns": [{"include": "#variables"}]}}},
    {"match": r"^([^\s#.](?:[^\t ]| (?! ))*)", "captures": {"1": {"name": "storage.type.setting.robotframework"}}},
    continuation,
] + common

variable_section = [
    {"include": "#comment"},
    {"match": r"^([$@&]\{[^}]*\})(\s?=)?",
     "captures": {"1": {"name": "variable.other.definition.robotframework"},
                  "2": {"name": "keyword.operator.assignment.robotframework"}}},
    continuation,
] + common

grammar = {
    "$schema": "https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json",
    "name": "Robot Framework",
    "scopeName": "source.robotframework",
    "fileTypes": ["robot", "resource"],
    "patterns": [
        dict(header(r"Settings?", "meta.section.settings.robotframework"), patterns=settings),
        dict(header(r"Variables?", "meta.section.variables.robotframework"), patterns=variable_section),
        dict(header(r"Test Cases?|Tasks?|Keywords?", "meta.section.body.robotframework"), patterns=body),
        dict(header(r"Comments?", "meta.section.comments.robotframework"),
             patterns=[{"name": "comment.block.robotframework", "match": r".+"}]),
        {"include": "#comment"},
    ],
    "repository": {"comment": comment, "variables": variables},
}

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "syntaxes",
                   "robotframework.tmLanguage.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(grammar, fh, indent=2)
    fh.write("\n")
print("wrote", out)
