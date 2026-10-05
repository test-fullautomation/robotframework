#!/usr/bin/env python3
"""Audit the acceptance tests after merging the fork's expectations onto a new
upstream release.

Two checks are made:

1. ``docs``: tests whose ``[Documentation]`` starts with ``UNKNOWN`` in the
   previous fork tree but still starts with ``FAIL`` in the merged tree (same
   file, same test name). These are fork expectations that a patch hunk lost.

2. ``dupes``: adjacent lines in the merged tree that are the same line twice,
   once with the upstream status (``FAIL``) and once with the fork's one
   (``UNKNOWN``), or that differ only in the result model syntax (``.kws[``
   versus ``[``). Conflict resolution can keep both the upstream and the fork
   line; one of them has to go.

Usage::

    python tools/fork/audit_atest_merge.py <previous fork tree> [<merged tree>]

``<previous fork tree>`` is a git checkout of the fork before the migration;
``<merged tree>`` defaults to the current directory. Changed files are found
with ``git diff --name-only <base>`` where ``<base>`` is the upstream tag the
merged tree was created from (``--base``, default ``v7.5``).
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

DOC_RE = re.compile(r"^\s+\[Documentation\]\s+(FAIL|UNKNOWN)\b")
STATUS_MAP = [("UNKNOWN", "FAIL"), ("unknown", "failed"), ("unknown", "fail")]


def tests_with_doc_status(text):
    """Returns {test name: FAIL|UNKNOWN} for tests whose doc starts with a status."""
    result = {}
    current = section = None
    for line in text.splitlines():
        if line.startswith("*** "):
            section = line.strip("* ").lower()
            current = None
        elif section and section.startswith(("test case", "task")):
            if line and not line[0].isspace() and not line.startswith("#"):
                current = line.strip()
            elif current:
                match = DOC_RE.match(line)
                if match:
                    result[current] = match.group(1)
    return result


def git(repo, *args):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if out.returncode:
        raise SystemExit(out.stderr.decode(errors="replace"))
    return out.stdout.decode("utf-8", "replace")


def audit_docs(fork, tree):
    files = git(fork, "grep", "-l", r"\[Documentation\]\s*UNKNOWN", "--",
                "atest/testdata").split()
    problems = []
    for rel in files:
        target = tree / rel
        if not target.exists():
            continue
        fork_docs = tests_with_doc_status(git(fork, "show", f"HEAD:{rel}"))
        tree_docs = tests_with_doc_status(
            target.read_text(encoding="utf-8", errors="replace")
        )
        for name, status in fork_docs.items():
            if status == "UNKNOWN" and tree_docs.get(name) == "FAIL":
                problems.append(f"{rel}: {name}")
    return problems


def _normalize(line):
    line = re.sub(r"\s+", " ", line.strip())
    for old in (".non_messages[", ".kws[", ".messages[", ".msgs["):
        line = line.replace(old, "[")
    for fork_token, upstream_token in STATUS_MAP:
        line = line.replace(fork_token, upstream_token)
    return line


def audit_dupes(tree, base, window=4):
    files = git(tree, "diff", "--name-only", base, "--", "atest").split()
    problems = []
    for rel in files:
        path = tree / rel
        if not path.is_file() or path.suffix not in (".robot", ".resource"):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        normalized = [_normalize(line) for line in lines]
        for i, norm in enumerate(normalized):
            if len(norm) < 12 or norm.startswith("#"):
                continue
            if not any(token in lines[i] for token in ("FAIL", "UNKNOWN", "fail", "unknown")):
                continue
            for j in range(i + 1, min(i + 1 + window, len(lines))):
                if normalized[j] == norm and lines[j].strip() != lines[i].strip():
                    problems.append(
                        f"{rel}:{i + 1}/{j + 1}\n    {lines[i].strip()}\n    {lines[j].strip()}"
                    )
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("fork", type=Path, help="previous fork tree (git checkout)")
    parser.add_argument("tree", type=Path, nargs="?", default=Path("."),
                        help="merged tree (default: current directory)")
    parser.add_argument("--base", default="v7.5",
                        help="upstream tag the merged tree is based on")
    args = parser.parse_args(argv)
    docs = audit_docs(args.fork, args.tree)
    dupes = audit_dupes(args.tree, args.base)
    print(f"{len(docs)} fork UNKNOWN expectation(s) still FAIL in the merged tree")
    for problem in docs:
        print(f"  {problem}")
    print(f"\n{len(dupes)} near-duplicate line pair(s) in the merged tree")
    for problem in dupes:
        print(f"  {problem}")
    return 1 if docs or dupes else 0


if __name__ == "__main__":
    sys.exit(main())
