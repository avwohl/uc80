"""Debug markers in the assembler text (docs/debug_info.md of um80).

A marker is a whole-line comment: blanks, ``;``, blanks, ``#``, then the
kind (``;#line 1 12``).  The text passes of uc80 skip a marker as they skip
any comment, and keep each marker where it is.  A pass must never let a
marker change its decision: with ``-g`` the program is the same bytes as
without ``-g`` (docs/debug_info_g.md).
"""

import re

# The kinds of docs/debug_info.md.  A comment such as ``; #pragma ...`` has
# another word after the ``#`` and is a plain comment.
_MARKER = re.compile(
    r"^[ \t]*;[ \t]*#(file|line|inline|endinline|func|endfunc|noopt)(?![A-Za-z0-9_])",
    re.IGNORECASE)


def is_marker(line: str) -> bool:
    """True for a whole-line ``;#kind`` marker."""
    return bool(_MARKER.match(line))


def marker_kind(line: str) -> str:
    """The lower-case kind of a marker (``line``, ``func``, ...), else ``""``."""
    m = _MARKER.match(line)
    return m.group(1).lower() if m else ""
