"""A few peephole rules that upeepz80 does not have.

The pass runs on the assembly text after upeepz80, on each run of text
between two inline-asm regions (main._optimize_outside_asm).  A label, a
directive or a volatile fence label is a barrier: no rule looks across one,
except rule 3, which keeps the labels where they are.

1. A dead register write.  ``ld R,x`` is deleted when the next
   instruction writes all of R again and does not read R.  ``x`` is an
   immediate or a register (a memory read might be volatile, and is not
   touched).  Example: ``ld H,0`` before ``ld H,L``.
2. ``ld L,S / ld H,L / ld L,0`` becomes ``ld H,S / ld L,0`` for S an
   immediate, a register A to E, or ``(IX+d)`` or ``(IY+d)``: the
   byte moves to the high half of HL.
3. ``call X`` followed by ``ret`` (labels may lie between them) becomes
   ``jp X``: the callee returns to the caller's caller.  Nothing is left
   to clean up, because a cleanup would stand between the two.  setjmp is
   excluded: it saves the return address.
"""

from __future__ import annotations

import re

_REG8 = ("a", "b", "c", "d", "e", "h", "l")
_PAIR = {"bc": ("b", "c"), "de": ("d", "e"), "hl": ("h", "l"),
         "af": ("a",), "sp": (), "ix": (), "iy": ()}
_TOKEN = re.compile(r"[a-z_][a-z_0-9']*", re.IGNORECASE)
_IMM = re.compile(r"^[-+]?(\d+|[0-9][0-9a-f]*h|0x[0-9a-f]+)$", re.IGNORECASE)
_IXD = re.compile(r"^\((ix|iy)[+-][^)]*\)$", re.IGNORECASE)
_NO_TAIL = re.compile(r"(^|_)(setjmp|sigsetjmp|_setjmp)$", re.IGNORECASE)


def _split(line: str):
    """(mnemonic, [operands]) of an instruction line, else None (a blank
    line, a comment, a label or a directive)."""
    if not line or line[0] not in " \t":
        return None
    text = line.split(";", 1)[0].strip()
    if not text:
        return None
    parts = text.split(None, 1)
    ops = []
    if len(parts) > 1:
        ops = [o.strip() for o in parts[1].split(",")]
    return parts[0].lower(), ops


def _is_comment(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith(";")


def _reads(operand: str) -> set[str]:
    """The 8-bit registers an operand reads (addresses included)."""
    regs: set[str] = set()
    for tok in _TOKEN.findall(operand.lower()):
        if tok in _REG8:
            regs.add(tok)
        elif tok in _PAIR:
            regs.update(_PAIR[tok])
    return regs


def _writes_whole(ins) -> set[str] | None:
    """The registers ``ins`` writes completely and reads not at all
    through its source: a ``ld r,y`` / ``ld rr,y`` / ``pop rr`` whose
    source y does not use a register of the destination; the set of
    destination registers, and None when ``ins`` is anything else."""
    if ins is None:
        return None
    mnem, ops = ins
    if mnem == "pop" and len(ops) == 1 and ops[0].lower() in _PAIR:
        return set(_PAIR[ops[0].lower()])
    if mnem != "ld" or len(ops) != 2:
        return None
    dst, src = ops[0].lower(), ops[1]
    if dst in _REG8:
        dest = {dst}
    elif dst in ("bc", "de", "hl"):
        dest = set(_PAIR[dst])
    else:
        return None
    if _reads(src) & dest:
        return None
    return dest


def _dead_ld(ins) -> str | None:
    """The register r when ``ins`` is ``ld r,x`` with x an immediate or a
    register, else None."""
    if ins is None:
        return None
    mnem, ops = ins
    if mnem != "ld" or len(ops) != 2 or ops[0].lower() not in _REG8:
        return None
    src = ops[1].lower()
    if src in _REG8 or _IMM.match(src):
        return ops[0].lower()
    return None


def optimize(code: str) -> str:
    """Apply the rules to ``code`` until nothing changes."""
    lines = code.split("\n")
    for _ in range(8):
        lines, changed = _pass(lines)
        if not changed:
            break
    return "\n".join(lines)


def _next_ins(lines: list[str], i: int):
    """Index of the next line after i that is not a comment or blank, or
    None; the line is returned as is (a label or directive stops a
    rule)."""
    j = i + 1
    while j < len(lines) and _is_comment(lines[j]):
        j += 1
    return j if j < len(lines) else None


def _pass(lines: list[str]) -> tuple[list[str], bool]:
    out = list(lines)
    changed = False
    drop: set[int] = set()
    n = len(out)
    for i in range(n):
        if i in drop:
            continue
        ins = _split(out[i])
        if ins is None:
            continue
        j = _next_ins(out, i)
        if j is None:
            continue
        nxt = _split(out[j])
        # Rule 1.
        reg = _dead_ld(ins)
        if reg is not None and nxt is not None:
            w = _writes_whole(nxt)
            if w is not None and reg in w:
                drop.add(i)
                changed = True
                continue
        # Rule 2.
        lowered = (ins[0], [o.lower() for o in ins[1]])
        if (lowered[0] == "ld" and len(lowered[1]) == 2
                and lowered[1][0] == "l" and nxt is not None
                and nxt[0] == "ld" and [o.lower() for o in nxt[1]]
                == ["h", "l"]):
            src = ins[1][1]
            k = _next_ins(out, j)
            third = _split(out[k]) if k is not None else None
            if (third is not None and third[0] == "ld"
                    and [o.lower() for o in third[1]] == ["l", "0"]
                    and (src.lower() in ("a", "b", "c", "d", "e")
                         or _IMM.match(src) or _IXD.match(src))):
                out[i] = f"\tld\tH,{src}"
                drop.add(j)
                changed = True
                continue
        # Rule 3.
        if ins[0] == "call" and len(ins[1]) == 1:
            target = ins[1][0]
            if _NO_TAIL.search(target):
                continue
            k = i + 1
            while k < n and (_is_comment(out[k]) or (
                    out[k] and out[k][0] not in " \t"
                    and out[k].split(";", 1)[0].strip().endswith(":"))):
                k += 1
            if k < n:
                r = _split(out[k])
                if r is not None and r == ("ret", []):
                    out[i] = f"\tjp\t{target}"
                    changed = True
    if drop:
        out = [l for idx, l in enumerate(out) if idx not in drop]
    return out, changed
