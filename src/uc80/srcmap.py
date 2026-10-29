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


def register(path: str, line_map) -> None:
    """Remember the line map of the input file PATH (as the parser got it)."""
    _MAPS[FILE_TABLE.intern(path)] = list(line_map or [])


def _show(path: str) -> str:
    try:
        rel = os.path.relpath(path)
    except ValueError:
        return path
    return rel if not rel.startswith("..") else path


def locate_line(path: str, line: int) -> str:
    return f"{_show(path)}:{line}"


def locate(token, default_path: str = "<input>") -> str:
    """file:line of a token."""
    line = getattr(token, "line", 0) or 0
    fid = getattr(token, "file_id", 0)
    m = _MAPS.get(fid)
    if m and 1 <= line <= len(m):
        f, l = m[line - 1]
        return f"{_show(f)}:{l}"
    name = FILE_TABLE.name(fid) if fid else None
    return f"{_show(name or default_path)}:{line}"
