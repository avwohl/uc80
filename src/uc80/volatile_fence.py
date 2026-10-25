"""Keep the peephole pass away from volatile accesses.

C23 5.1.2.3 and 6.7.3: every access to a volatile object happens as
written.  The peephole optimizer (upeepz80) sees assembly text only, and
the text does not say which loads and stores are volatile, so it merges
``ld (_g),A / ld A,(_g)``, drops the second of two equal reads, and removes
the store of ``g = g``.

The code generator therefore brackets the code of every statement part
that may access a volatile object with two comment lines:

    ; __UC80_VOL_BEGIN__
    ...
    ; __UC80_VOL_END__

Just before the peephole pass, ``fence_regions`` puts a label between each
memory-accessing instruction of such a region and its neighbours.  The
peephole matcher never matches a pattern across a label (a label is a place
another path could enter), so no rule can see a volatile load or store
together with another instruction, and none can merge, move or delete one.
The labels are named ``??VOLn``: upeepz80 treats a ``??`` label as a
compiler-internal join point, never a procedure entry.  ``strip`` takes the
labels and the markers out again afterwards, so the assembler sees neither.
Code outside the regions is optimized as before.
"""

from __future__ import annotations

import re

VOL_BEGIN = "; __UC80_VOL_BEGIN__"
VOL_END = "; __UC80_VOL_END__"
_FENCE_RE = re.compile(r"^\?\?VOL\d+:\s*$")

# Instructions that touch memory or a port without a parenthesised operand.
_BLOCK_OPS = frozenset((
    "ldi", "ldd", "ldir", "lddr", "cpi", "cpd", "cpir", "cpdr",
    "ini", "ind", "inir", "indr", "outi", "outd", "otir", "otdr",
    "rld", "rrd", "in", "out"))


def _is_memory_access(line: str) -> bool:
    """Does the instruction on ``line`` read or write memory?"""
    text = line.split(";", 1)[0]
    if not text.strip() or not text[0].isspace():
        return False                      # blank, comment, label, directive
    parts = text.split(None, 1)
    op = parts[0].lower()
    if op in _BLOCK_OPS:
        return True
    return len(parts) > 1 and "(" in parts[1]


def fence_regions(code: str) -> str:
    """Put a ``??VOLn`` label around each memory access inside the volatile
    regions of ``code``.  The markers stay (``strip`` removes them)."""
    if VOL_BEGIN not in code:
        return code
    out: list[str] = []
    depth = 0
    n = 0
    fenced = False          # the last line written is a fence label
    for line in code.split("\n"):
        s = line.strip()
        if s == VOL_BEGIN:
            depth += 1
            out.append(line)
            continue
        if s == VOL_END:
            depth = max(0, depth - 1)
            out.append(line)
            continue
        if depth and _is_memory_access(line):
            if not fenced:
                n += 1
                out.append(f"??VOL{n}:")
            out.append(line)
            n += 1
            out.append(f"??VOL{n}:")
            fenced = True
            continue
        out.append(line)
        if s and not s.startswith(";"):
            fenced = False
    return "\n".join(out)


def strip(code: str) -> str:
    """Remove the region markers and the fence labels."""
    if VOL_BEGIN not in code and "??VOL" not in code:
        return code
    return "\n".join(
        line for line in code.split("\n")
        if line.strip() not in (VOL_BEGIN, VOL_END)
        and not _FENCE_RE.match(line))
