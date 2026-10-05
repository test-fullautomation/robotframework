#!/usr/bin/env python3
"""Resolve `git apply --3way` conflicts of the fork's acceptance test patch.

The RobotFramework AIO fork changes acceptance test expectations mostly in a
mechanical way (FAIL -> UNKNOWN status tokens, `level=UNKNOWN` cells, ", N
unknown" in statistics). Upstream reformatted many of the same lines between
RF 6.1 and 7.x (e.g. `${tc.kws[0].msgs[0]}` became `${tc[0, 0]}`), which turns
those mechanical edits into merge conflicts.

For every conflict hunk this script walks the *ours* (upstream) lines, finds
the fork's base line that corresponds to each of them (comparing the cells
that are not variables) and re-applies the fork's cell-level edit to the
upstream line. Fork-added lines without a base counterpart (comments, new
keywords) are inserted after the line they follow in the fork's version.
Hunks that cannot be resolved this way keep their conflict markers and are
listed in the report so they can be handled by hand.

The result is a best effort: run ``tools/fork/audit_atest_merge.py``
afterwards to find fork expectations that were lost and lines that ended up
in the file twice (once as upstream wrote it, once as the fork did), and
re-run the acceptance tests before trusting the merged expectations.

Usage::

    python tools/fork/resolve_atest_conflicts.py <patch> [file ...]

``<patch>`` is the fork's diff that was applied (needed for the base lines).
Without files, every conflicted file in the index is processed.
"""

import re
import subprocess
import sys
from pathlib import Path

CELL_SPLIT = re.compile(r" {2,}|\t")
VAR = re.compile(r"[$@&%]\{")
STATUS = re.compile(r"^(PASS|FAIL|SKIP|UNKNOWN|NOT RUN)$")
HUNK = re.compile(r"<<<<<<< ours\n(.*?)=======\n(.*?)>>>>>>> theirs\n", re.S)


def split_cells(line):
    stripped = line.lstrip(" ")
    indent = line[: len(line) - len(stripped)]
    return indent, [c for c in CELL_SPLIT.split(stripped.rstrip("\n")) if c != ""]


def join_cells(indent, cells):
    return indent + "    ".join(cells)


def normalize(line, ignore_status=False):
    _, cells = split_cells(line)
    out = []
    for cell in cells:
        if VAR.search(cell):
            cell = "VAR"
        elif ignore_status and STATUS.match(cell):
            cell = "STATUS"
        elif ignore_status and cell in ("level=UNKNOWN", "tc_status=UNKNOWN"):
            continue
        elif ignore_status:
            cell = re.sub(r", \d+ unknown", "", cell)
        out.append(cell)
    return "|".join(out)


def patch_lines(patch_text):
    """Returns {file: (removed lines, added lines)} from a unified diff."""
    result = {}
    current = None
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[1]
            result[current] = ([], [])
        elif current is None or line.startswith(("---", "+++", "@@", "index ")):
            continue
        elif line.startswith("-"):
            result[current][0].append(line[1:])
        elif line.startswith("+"):
            result[current][1].append(line[1:])
    return result


def find_base(theirs_line, removed, used):
    key = normalize(theirs_line, ignore_status=True)
    for index, line in enumerate(removed):
        if index not in used and normalize(line, ignore_status=True) == key:
            used.add(index)
            return line
    return None


def transform(ours_line, base_line, theirs_line):
    """Re-applies the base -> theirs cell edit onto the upstream line."""
    indent, o_cells = split_cells(ours_line)
    _, b_cells = split_cells(base_line)
    _, t_cells = split_cells(theirs_line)
    if b_cells == t_cells:
        return ours_line
    new = list(o_cells)
    for i, (b, t) in enumerate(zip(b_cells, t_cells)):
        if b == t:
            continue
        if VAR.search(b) and VAR.search(t):
            continue  # upstream spells variables differently; keep ours
        if b in new:
            new[new.index(b)] = t
        else:
            return None
    for extra in t_cells[len(b_cells):]:
        new.append(extra)
    if len(t_cells) < len(b_cells):
        for gone in b_cells[len(t_cells):]:
            if gone in new:
                new.remove(gone)
    return join_cells(indent, new) + "\n"


def resolve_hunk(ours, theirs, removed, added):
    o_lines = ours.splitlines(keepends=True)
    t_lines = theirs.splitlines(keepends=True)
    added_set = set(l.rstrip("\n") for l in added)
    used_bases = set()
    result = []
    pending_new = []
    consumed_t = set()
    # Map each theirs line to its base and target
    for ti, t in enumerate(t_lines):
        t_plain = t.rstrip("\n")
        if t_plain in added_set:
            base = find_base(t, removed, used_bases)
            if base is None:
                pending_new.append((ti, t))
    for o in o_lines:
        o_plain = o.rstrip("\n")
        if any(o_plain == t.rstrip("\n") for t in t_lines):
            result.append(o)
            continue
        key = normalize(o, ignore_status=True)
        match = None
        for ti, t in enumerate(t_lines):
            if ti in consumed_t:
                continue
            if normalize(t, ignore_status=True) == key:
                match = (ti, t)
                break
        if match is None:
            result.append(o)  # upstream-only line
            continue
        ti, t = match
        consumed_t.add(ti)
        t_plain = t.rstrip("\n")
        if t_plain not in added_set:
            result.append(o)  # same base line, upstream reformatted it
            continue
        base = find_base(t, removed, set())
        if base is None:
            return None
        new = transform(o, base, t)
        if new is None:
            return None
        result.append(new)
        # fork-added lines that directly follow this one in theirs
        for nti, nt in list(pending_new):
            if nti == ti + 1 or (nti > ti and all(j in consumed_t for j in range(ti + 1, nti))):
                result.append(nt)
                consumed_t.add(nti)
                pending_new.remove((nti, nt))
    for _, nt in pending_new:
        result.append(nt)
    return "".join(result)


def resolve_file(path, removed, added):
    text = path.read_text(encoding="UTF-8").replace("\r\n", "\n")
    unresolved = 0
    resolved = 0

    def repl(match):
        nonlocal unresolved, resolved
        merged = resolve_hunk(match.group(1), match.group(2), removed, added)
        if merged is None:
            unresolved += 1
            return match.group(0)
        resolved += 1
        return merged

    new_text = HUNK.sub(repl, text)
    if new_text != text:
        path.write_text(new_text, encoding="UTF-8", newline="\n")
    return resolved, unresolved


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    patch = patch_lines(Path(argv[0]).read_text(encoding="UTF-8"))
    files = argv[1:]
    if not files:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True, check=True,
        )
        files = out.stdout.split()
    total_resolved = total_unresolved = 0
    for file in files:
        removed, added = patch.get(file, ([], []))
        resolved, unresolved = resolve_file(Path(file), removed, added)
        total_resolved += resolved
        total_unresolved += unresolved
        if unresolved:
            print(f"MANUAL  {unresolved:2d} hunk(s) left  {file}")
        elif resolved:
            print(f"ok      {resolved:2d} hunk(s)        {file}")
    print(f"\nresolved {total_resolved} hunks, {total_unresolved} left for manual work")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
