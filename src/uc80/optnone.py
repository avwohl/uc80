"""optnone: no optimization for one function (docs/optnone.md).

A function under optnone is compiled the way the code generator writes it
and no pass changes it.  The routine is written between

    ;#noopt begin
    ; optnone _name
    ...
    ;#noopt end

(the debug marker of um80_and_friends ``docs/debug_info.md``; the comment
line after ``begin`` names the function, so that a pass that removes a whole
function can be told from a pass that damages the markers).  Three kinds of code use this module:

* the front: finding the functions that the source marks
  (``#pragma uc80 optnone``, ``[[uc80::optnone]]``,
  ``__attribute__((optimize("O0")))``);
* ``asm_dce``, which keeps every block of a region once one block is live
  (``noopt_groups``);
* the text passes of ``main.py``, which cannot all skip a region line by
  line.  ``guard`` runs a pass on the whole text and then puts the text of
  each region back as it was before the pass.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Callable

_BEGIN = re.compile(r"^[ \t]*;[ \t]*#noopt[ \t]+begin\b", re.IGNORECASE)
_END = re.compile(r"^[ \t]*;[ \t]*#noopt[ \t]+end\b", re.IGNORECASE)
# the line after ``;#noopt begin``: the label of the function.  (um80 takes
# ``;#noopt`` with the word begin or end and nothing else.)
_NAME = re.compile(r"^[ \t]*;[ \t]*optnone[ \t]+(\S+)[ \t]*$")


def begin_lines(label: str) -> list[str]:
    return ["\t;#noopt begin", f"\t; optnone {label}"]


def end_line() -> str:
    return "\t;#noopt end"


def is_begin(line: str) -> bool:
    return bool(_BEGIN.match(line))


def is_end(line: str) -> bool:
    return bool(_END.match(line))


def _name_after(lines: list[str], i: int) -> str | None:
    """The label word of the region that starts at line ``i``."""
    if i + 1 < len(lines):
        m = _NAME.match(lines[i + 1])
        if m:
            return m.group(1)
    return None


# ---- the text passes -------------------------------------------------------


def _split_regions(lines: list[str]):
    """``{name: [lines between begin and end]}`` for the named top-level
    regions, or None when the markers do not pair."""
    found: dict[str, list[str]] = {}
    i = 0
    n = len(lines)
    while i < n:
        if not _BEGIN.match(lines[i]):
            if _END.match(lines[i]):
                return None
            i += 1
            continue
        name = _name_after(lines, i)
        if not name or name in found:
            return None
        j = i + 1
        while j < n and not _END.match(lines[j]):
            if _BEGIN.match(lines[j]):
                return None
            j += 1
        if j >= n:
            return None
        found[name] = lines[i + 1:j]
        i = j + 1
    return found


def _warn(text: str) -> None:
    print(f"uc80: warning: optnone: {text}", file=sys.stderr)


def guard(fn: Callable[[str], str], code: str) -> str:
    """``fn(code)`` with the text of each ``;#noopt`` region as it was.

    The pass runs on the whole text, so what it knows about the callers and
    the callees of a region is right (the text of the region is the text
    that it reads).  Afterwards each region is put back.  When the markers
    no longer pair, the pass is dropped: the result is ``code``."""
    if "#noopt" not in code:
        return fn(code)
    before = _split_regions(code.split("\n"))
    if before is None:
        _warn("the ;#noopt markers do not pair; the pass runs unguarded")
        return fn(code)
    if not before:
        return fn(code)
    out = fn(code)
    lines = out.split("\n")
    after = _split_regions(lines)
    if after is None or set(after) - set(before):
        _warn("a text pass lost a ;#noopt marker; the pass is dropped")
        return code
    result: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if not _BEGIN.match(lines[i]):
            result.append(lines[i])
            i += 1
            continue
        name = _name_after(lines, i)
        j = i + 1
        while not _END.match(lines[j]):
            j += 1
        result.append(lines[i])
        result.extend(before[name])
        result.append(lines[j])
        i = j + 1
    return "\n".join(result)


def noopt_groups(block_lines: list[tuple[str, list[str]]]) -> list[set[str]]:
    """For ``asm_dce``: the block labels of each region.  ``block_lines``
    is (label, lines) in source order.  A block that holds a line of the
    region, the begin or the end marker too, belongs to the group."""
    groups: dict[str, set[str]] = {}
    open_name: str | None = None
    for label, lines in block_lines:
        if open_name is not None:
            groups[open_name].add(label)
        for k, ln in enumerate(lines):
            if "#noopt" not in ln:
                continue
            if _BEGIN.match(ln):
                open_name = _name_after(lines, k)
                if open_name:
                    groups.setdefault(open_name, set()).add(label)
                continue
            if _END.match(ln):
                if open_name is not None:
                    groups[open_name].add(label)
                open_name = None
    return [g for g in groups.values() if len(g) > 1]


# ---- the front: the functions that the source marks -----------------------

_TOKEN = re.compile(
    r"(?P<skip>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')"
    r"|(?P<gnu>__attribute(?:__)?\s*\(\(\s*[^()]*(?:\([^()]*\)[^()]*)*\)\))"
    r"|(?P<c23>\[\[[^\]]*\]\])")
_O0 = r"(?:__)?optimize(?:__)?\s*\(\s*\"-?O0\"\s*\)"
_GNU_OPTNONE = re.compile(r"(?<![\w])(?:__)?optnone(?:__)?(?![\w])|" + _O0)
_C23_OPTNONE = re.compile(
    r"(?<![\w:])(?:uc80|gnu|clang)\s*::\s*(?:__)?optnone(?:__)?(?![\w])|"
    r"(?<![\w:])gnu\s*::\s*" + _O0)


def attribute_lines(source: str) -> list[tuple[int, bool]]:
    """(1-based line, nested) of each optnone attribute in ``source`` (the
    preprocessed text, before the front end strips the attributes).
    ``nested`` is true for an attribute inside braces (a local variable, a
    member): that attribute marks no function."""
    if "optnone" not in source and "optimize" not in source:
        return []
    out = []
    depth = 0
    pos = 0
    for m in _TOKEN.finditer(source):
        gap = source[pos:m.start()]
        depth += gap.count("{") - gap.count("}")
        pos = m.end()
        kind = m.lastgroup
        if ((kind == "gnu" and _GNU_OPTNONE.search(m.group(0)))
                or (kind == "c23" and _C23_OPTNONE.search(m.group(0)))):
            out.append((source.count("\n", 0, m.start()) + 1, depth > 0))
    return out


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def mark_names(items, names_of: Callable, locate: Callable,
               attr_lines: list[tuple[int, bool]], pragmas: list[tuple[str, int, list[str]]]
               ) -> tuple[list[tuple[str, int, list[str]]], list[str]]:
    """The function names that the marks of one input file name.

    ``items``: the top-level items of the unit, in order.  ``names_of(item)``
    gives (is_definition, [function names]) for an item that declares
    functions, else (False, []).  ``locate(item)`` gives (path, line) of the
    source of the item, or None.  ``attr_lines``: (line, nested) of the preprocessed
    text (see ``attribute_lines``).  ``pragmas``: (path, line, words) of each
    ``#pragma uc80 optnone``.

    Returns ([(path, line, [names])...], [problem texts])."""
    found: list[tuple[str, int, list[str]]] = []
    problems: list[str] = []
    placed = []
    for item in items:
        pos = getattr(item, "pos", None)
        line = getattr(pos, "start_line", None)
        where = locate(item)
        placed.append((item, line, where))
    for aline, nested in attr_lines:
        if nested:
            problems.append(f"line {aline}: optnone attribute is not "
                            f"before a function")
            continue
        hit = next(((it, wh) for it, ln, wh in placed
                    if ln is not None and ln >= aline), None)
        names = names_of(hit[0])[1] if hit else []
        if not names:
            problems.append(f"line {aline}: optnone attribute is not "
                            f"before a function")
            continue
        found.append((hit[1][0] if hit[1] else "", hit[1][1] if hit[1] else 0,
                      names))
    for path, pline, words in pragmas:
        if words:
            found.append((path, pline, list(words)))
            continue
        hit = next(((it, wh) for it, ln, wh in placed
                    if wh is not None and _same_file(wh[0], path)
                    and wh[1] > pline), None)
        names = names_of(hit[0])[1] if hit else []
        if not names:
            problems.append(f"{path}:{pline}: #pragma uc80 optnone is not "
                            f"before a function")
            continue
        found.append((path, pline, names))
    return found, problems
