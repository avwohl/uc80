"""Source positions for diagnostics: file and line of the real source.

The preprocessor output has no line markers (directives disappear,
continued lines join, an include is spliced in), so the line of a token
in the parsed text is not the line in the file.  ``Preprocessor.line_map``
holds the (file, line) of each output line; ``register`` keeps the map
of each input file under the file id that the parser stamps on the
tokens of that file, and ``locate`` turns a token into ``file:line``.
"""

from __future__ import annotations

import os

from uc_core.c23_parser import FILE_TABLE

_MAPS: dict[int, list] = {}
_LAST: list = [None]          # file id of the file registered last
_BREAKS: dict[int, dict] = {}
_OUTER: dict[int, list] = {}     # file id -> per output line: (file, line) or None


def register(path: str, line_map, line_breaks=None) -> None:
    """Remember the line map of the input file PATH (as the parser got it).
    ``line_breaks`` is ``Preprocessor.line_breaks``: the columns of an
    output line where a later source line begins (a continued line)."""
    fid = FILE_TABLE.intern(path)
    _LAST[0] = fid
    _MAPS[fid] = list(line_map or [])
    _BREAKS[fid] = dict(line_breaks or {})


def _show(path: str) -> str:
    try:
        rel = os.path.relpath(path)
    except ValueError:
        return path
    return rel if not rel.startswith("..") else path


def locate_line(path: str, line: int) -> str:
    """file:line for a line of the preprocessed text (no token at hand)."""
    m = _MAPS.get(FILE_TABLE.intern(path))
    if m and 1 <= line <= len(m):
        f, l = m[line - 1]
        return f"{_show(f)}:{l}"
    return f"{_show(path)}:{line}"


def locate(token, default_path: str = "<input>") -> str:
    """file:line of a token."""
    line = getattr(token, "line", 0) or 0
    fid = getattr(token, "file_id", 0)
    m = _MAPS.get(fid)
    if m and 1 <= line <= len(m):
        f, l = m[line - 1]
        col = (getattr(token, "column", 0) or 0) - 1
        for bcol, bf, bl in reversed(_BREAKS.get(fid, {}).get(line - 1, ())):
            if col >= bcol:
                f, l = bf, bl
                break
        return f"{_show(f)}:{l}"
    name = FILE_TABLE.name(fid) if fid else None
    return f"{_show(name or default_path)}:{line}"


def locate_end() -> "str | None":
    """file:line of the last line of the input file registered last (an
    error at the end of the input has no token to name)."""
    m = _MAPS.get(_LAST[0])
    if not m:
        return None
    f, l = m[-1]
    try:
        with open(f, errors="replace") as fh:
            l = max(1, min(l, len(fh.read().splitlines())))
    except OSError:
        pass
    return f"{_show(f)}:{l}"


def locate_pair(token):
    """(path, line) of a token, the parts of ``locate`` (the path as
    ``locate`` shows it).  ``-g`` writes these into the ``;#file`` and
    ``;#line`` markers."""
    s = locate(token)
    path, _, line = s.rpartition(":")
    return path, int(line)


def locate_at(file_id: int, line: int):
    """(path, line) for a line of the preprocessed text of the file with
    id ``file_id`` (the line of a node that has no token), or None."""
    m = _MAPS.get(file_id)
    if m and 1 <= line <= len(m):
        f, l = m[line - 1]
        return _show(f), l
    return None


def register_outer(path: str, outer_map) -> None:
    """Remember the physical position of each output line of the input file
    PATH where a ``#line`` directive is in effect (``outer_map[k]`` is
    ``(file, line)`` of output line ``k`` of the text, or None where the
    directive changed nothing).  ``-g`` writes it as ``outer=`` on ``;#line``
    (rule D8 of the debugger plan: the C line below a BASIC line)."""
    _OUTER[FILE_TABLE.intern(path)] = list(outer_map or [])


def _outer_at(fid: int, line: int):
    m = _OUTER.get(fid)
    if m and 1 <= line <= len(m) and m[line - 1] is not None:
        f, l = m[line - 1]
        return _show(f), l
    return None


def outer_pair(token):
    """(path, line) of the physical C position of a token under a ``#line``
    directive, or None when no directive moved the token."""
    return _outer_at(getattr(token, "file_id", 0),
                     getattr(token, "line", 0) or 0)


def outer_at(file_id: int, line: int):
    """The same for a line of the preprocessed text."""
    return _outer_at(file_id, line)
