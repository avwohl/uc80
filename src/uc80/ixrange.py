"""The IX range of a function, for the frame records of ``-g``.

``;#func NAME F L frame=ix pops=0`` says that the function keeps a frame in
IX.  A debugger needs to know where IX holds the frame: from the
``add IX,SP`` of the prologue to the ``pop IX`` of the epilogue.  Outside the
range the return address is at an offset from SP, and the debugger works
out the offset from the code.  ``ixbegin=LABEL ixend=LABEL`` on the marker
names the two ends (docs/debug_info.md of um80_and_friends).

The labels are written here, after the last text pass.  A label stops the
peephole patterns, so a label in the text of a pass would change the code
(D2 of the debugger plan).  ``add_ix_ranges`` is the last step that changes
the text of ``-g``, and it only adds labels, which have no bytes.  uplm80
does the same (``add_ix_range_labels`` in its compiler.py).

The frame kind is read again from the final text, because the passes can
delete the frame (``frame=ix`` becomes ``frame=none``).  A function that
has no frame and names the shared automatic storage ``??AUTO`` keeps its
locals at fixed addresses and is ``static``.
"""

import re

from .markers import marker_kind

_FUNC = re.compile(r"^(\s*;\s*#func\s+\S+(?:\s+\d+\s+\d+)?)(.*)$", re.IGNORECASE)
_LABEL = re.compile(r"^[^\s;:]+:")


def _instruction(line: str):
    """The lower-case words of the instruction on a line, or None for a
    blank line, a comment or a label with nothing after it."""
    text = line.split(";", 1)[0]
    m = _LABEL.match(text.strip())
    if m:
        text = text.strip()[m.end():]
    words = text.lower().split()
    return words or None


def _frame_kind(body: list) -> str:
    """ix, static or none for the final text of a function."""
    for line in body:
        if _instruction(line) == ["push", "ix"]:
            return "ix"
    if any("??auto" in ln.lower() for ln in body):
        return "static"
    return "none"


def _ix_range(body: list):
    """(index of the ``add IX,SP`` line, index of the ``pop IX`` line) of the
    final text of an IX function, or None when the text does not have the
    plain frame.  The range is safe for a debugger when the function starts
    with ``push IX / ld IX,0 / add IX,SP`` (nothing is pushed in front of the
    saved IX, so the return address is at IX+2) and has a ``pop IX`` in front
    of a ``ret``."""
    ins = [(i, _instruction(ln)) for i, ln in enumerate(body)]
    ins = [(i, w) for i, w in ins if w]
    if len(ins) < 4:
        return None
    if [w for _i, w in ins[:3]] != [["push", "ix"], ["ld", "ix,0"],
                                    ["add", "ix,sp"]]:
        return None
    begin = ins[2][0]
    # A `push IX ... pop IX` pair inside the body lends IX to the code as a
    # pointer (it adds an offset to IX and restores IX).  It is not the
    # epilogue.  The `pop IX` of an epilogue is followed by `ret` (or by a
    # `jp`, a tail call).  The debugger reads the return address at SP at
    # a `ret`, so the epilogues before the last one do not matter.
    pops = [k for k in range(3, len(ins))
            if ins[k][1] == ["pop", "ix"] and k + 1 < len(ins)
            and ins[k + 1][1][0] in ("ret", "jp", "jr")]
    if not pops:
        return None
    end = ins[pops[-1]][0]
    if end <= begin:
        return None
    return begin, end


def add_ix_ranges(code: str) -> str:
    """Return ``code`` with ``frame=`` corrected and ``ixbegin=``/``ixend=``
    added on each ``;#func`` marker, and the two labels written.  Only
    marker lines and label lines change."""
    if "#func" not in code:
        return code
    lines = code.split("\n")
    out = []
    i = 0
    n = 0
    while i < len(lines):
        line = lines[i]
        m = _FUNC.match(line) if marker_kind(line) == "func" else None
        if m is None:
            out.append(line)
            i += 1
            continue
        j = i + 1
        while j < len(lines) and marker_kind(lines[j]) != "endfunc":
            if marker_kind(lines[j]) == "func":
                break
            j += 1
        if j >= len(lines) or marker_kind(lines[j]) != "endfunc":
            out.append(line)         # no end: leave the record alone
            i += 1
            continue
        body = lines[i + 1:j]
        head, tail = m.group(1), m.group(2)
        kind = _frame_kind(body)
        rng = _ix_range(body) if kind == "ix" else None
        tail = re.sub(r"\s*\b(frame|ixbegin|ixend)=\S+", "", tail)
        tail = f" frame={kind}" + tail
        if rng is not None:
            n += 1
            lb, le = f"??IXB{n}", f"??IXE{n}"
            body.insert(rng[1], f"{le}:")
            body.insert(rng[0] + 1, f"{lb}:")
            tail += f" ixbegin={lb} ixend={le}"
        out.append(head + tail)
        out.extend(body)
        i = j
    return "\n".join(out)
