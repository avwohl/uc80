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
_BREAKS: dict[int, dict] = {}


def register(path: str, line_map, line_breaks=None) -> None:
    """Remember the line map of the input file PATH (as the parser got it).
    ``line_breaks`` is ``Preprocessor.line_breaks``: the columns of an
    output line where a later source line begins (a continued line)."""
    fid = FILE_TABLE.intern(path)
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
