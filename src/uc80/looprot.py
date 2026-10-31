"""Loop rotation.  See docs/loop_rotation.md.

The code generator tests a ``for`` or ``while`` loop at the top:

    @FOR4:   TEST
             jr cc,@ENDFOR6        ; leave the loop
             BODY
             jr @FOR4              ; back to the test
    @ENDFOR6:

Each pass of the loop runs the conditional jump (7 T-states, not taken) and
the unconditional back jump (12 T-states).  This pass copies the test to
the end of the loop and ends it with the opposite jump to the start of the
body:

    @FOR4:   TEST
             jr cc,@ENDFOR6
    @RB0:    BODY
             TEST                  ; the copy
             jp !cc,@RB0           ; 10 T-states
    @ENDFOR6:

The first test stays as it is, so a ``continue`` that jumps to @FOR4 and
the entry into the loop work as before.  The loop saves 9 T-states for
each pass and grows by the size of the test minus the 2 bytes of the old
back jump.

The pass runs on the text of the whole program after the peephole pass.
It rotates a loop when

- the head label is @FORn or @WHILEn, and the test up to the first
  conditional jump to the exit label @ENDFORn or @ENDWHILEn has at most
  12 instructions;
- the test holds no label except @CMP_NVn (the signed compare pair), no
  jump except to such a label, no return, no restart, no DJNZ and no
  comment;
- the back jump ``jr``/``jp`` to the head stands directly before the exit
  label.  When the exit is a conditional return (``ret nc``, made by the
  peephole pass from a jump to a ``ret``), the back jump is followed by
  labels and the ``ret``.

Only hot loops are rotated, and only while the copies fit in a budget of
2 percent of the code (at least 32 bytes), deepest loops first: an outer
loop needs a body of at most 32 bytes without a call, a loop inside another
loop a body of at most 100 bytes without a call.

A test that holds a call is copied as it is.  A test that is a short
``&&`` chain is rotated at its first jump only: the copy ends at the first
conditional exit and jumps to the label that is put after the first exit
jump in the top test.
"""

from __future__ import annotations

import os
import re

try:  # the size of one instruction
    from upeepz80.z80 import effect as _effect
except Exception:  # pragma: no cover
    _effect = None

_HEAD = re.compile(r"^(@(?:FOR|WHILE)\d+):\s*(;.*)?$")
_LABEL = re.compile(r"^(@?[A-Za-z_?$.][A-Za-z0-9_@?$.]*):")
_JCC = re.compile(
    r"^\t(jr|jp)\s+(nz|z|nc|c|po|pe|p|m),\s*(\S+?)\s*(?:;.*)?$", re.I)
_JMP = re.compile(r"^\t(jr|jp)\s+(@\S+?)\s*(?:;.*)?$", re.I)
_RETCC = re.compile(r"^\tret\s+(nz|z|nc|c|po|pe|p|m)\s*(?:;.*)?$", re.I)
_RET = re.compile(r"^\tret\s*(?:;.*)?$", re.I)
_END = re.compile(r"^@END(?:FOR|WHILE)\d+$")
_CMP_NV = re.compile(r"^@CMP_NV\d+$")
_BAD_OP = re.compile(r"^\t(ret|reti|retn|rst|djnz|halt)\b", re.I)
_CALL_COND = re.compile(r"^\tcall\s+\w+\s*,", re.I)
_JP_INDIRECT = re.compile(r"^\tjp\s+\(", re.I)
_INV = {"nc": "c", "c": "nc", "nz": "z", "z": "nz", "p": "m", "m": "p",
        "po": "pe", "pe": "po"}
MAX_TEST = 12
# the copy of the test may have this many bytes (UC80_ROT_BYTES: for tests)
MAX_BYTES = int(os.environ.get("UC80_ROT_BYTES", 14))
# rotate only a loop that holds no other loop (UC80_ROT_INNER=0: all loops)
OUTER_BYTES = int(os.environ.get("UC80_ROT_OUTER", 9))
INNERMOST = os.environ.get("UC80_ROT_INNER", "1") != "0"
# hot loops only (docs/loop_rotation.md).  A loop with a call or a long
# body gains too little for the bytes of the copy, unless it lies in
# another loop: then a body of up to NESTED_BYTES, without a call, is rotated.
BODY_BYTES = int(os.environ.get("UC80_ROT_BODY", 32))
NESTED_BYTES = int(os.environ.get("UC80_ROT_NESTED", 100))
CALLS_OK = os.environ.get("UC80_ROT_CALLS", "0") != "0"
# the copies of the tests may use this share of the code, at least
# MIN_BUDGET bytes (UC80_ROT_PERCENT, UC80_ROT_MIN: for tests)
BUDGET_PERCENT = int(os.environ.get("UC80_ROT_PERCENT", 2))
MIN_BUDGET = int(os.environ.get("UC80_ROT_MIN", 32))
_CALL = re.compile(r"^\t(call|rst)\b", re.I)


def _bytes(line: str) -> int:
    """The size of an instruction line in bytes (4 when unknown)."""
    if _effect is None:
        return 4
    parts = line.split(";", 1)[0].split(None, 1)
    try:
        size = _effect(parts[0].lower(), parts[1] if len(parts) > 1 else "").size
    except Exception:
        return 4
    return size or 4


def _is_instruction(line: str) -> bool:
    return bool(line) and line[0] in " \t" and bool(
        line.split(";", 1)[0].strip())


_DATA_OP = frozenset((
    "db", "dw", "dd", "ds", "defb", "defw", "defs", "equ", "public", "extrn",
    "cseg", "dseg", "common", "aseg", "end", "org", "name", "title", ".z80",
    "assume", "if", "else", "endif", "macro", "endm"))


def _is_code(line: str) -> bool:
    """An instruction, not a data or assembler directive line."""
    if not _is_instruction(line):
        return False
    return line.split(";", 1)[0].split()[0].lower() not in _DATA_OP


_ST = re.compile(r"^\tld\t\(([^()\s]+)\),(HL|DE|BC|A)\s*(?:;.*)?$", re.I)
_LD = re.compile(r"^\tld\t(HL|DE|BC|A),\(([^()\s]+)\)\s*(?:;.*)?$", re.I)
_LDR = re.compile(r"^\tld\t([ABCDEHL]),([ABCDEHL])\s*(?:;.*)?$", re.I)
_HALVES = {"HL": ("H", "L"), "DE": ("D", "E"), "BC": ("B", "C")}


def _trim_junction(lines: list[str], k: int, copy: list[str]) -> list[str]:
    """The copy of the test follows the update code.  Its first
    instructions are dropped when the update has just made them true: a
    load of what the last instruction stored, and the copy of a pair into
    HL that the last two instructions made from HL."""
    prev: list[str] = []
    j = k - 1
    while j >= 0 and len(prev) < 2:
        ln = lines[j]
        if not ln.strip() or ln.lstrip().startswith(";"):
            j -= 1
            continue
        if ln[0] not in " \t":
            return copy             # a label: another path joins here
        prev.append(ln)
        j -= 1
    if prev and copy:
        st, ld = _ST.match(prev[0]), _LD.match(copy[0])
        if (st and ld and st.group(1) == ld.group(2)
                and st.group(2).upper() == ld.group(1).upper()):
            return copy[1:]
    if len(prev) == 2 and len(copy) >= 2:
        # ld B,H / ld C,L  then  ld H,B / ld L,C  (either order)
        a = [_LDR.match(x) for x in prev]
        b = [_LDR.match(x) for x in copy[:2]]
        if all(a) and all(b):
            fwd = {(m.group(1).upper(), m.group(2).upper()) for m in a}
            back = {(m.group(2).upper(), m.group(1).upper()) for m in b}
            if fwd == back and any(
                    fwd == {(hi, "H"), (lo, "L")} or fwd == {("H", hi), ("L", lo)}
                    for hi, lo in (("B", "C"), ("D", "E"))):
                return copy[2:]
    return copy


def rotate(code: str) -> str:
    """Rotate the loops of the program text ``code``."""
    if "@FOR" not in code and "@WHILE" not in code:
        return code
    lines = code.split("\n")
    n = len(lines)
    label_at: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = _LABEL.match(ln)
        if m and ln[0] not in " \t":
            label_at.setdefault(m.group(1), i)
    spans = []                         # (first, last) line of each loop
    for lab, pos in label_at.items():
        if lab.startswith("@FOR") or lab.startswith("@WHILE"):
            for j in range(pos + 1, min(pos + 40, n)):
                jm = _JCC.match(lines[j])
                if jm and _END.match(jm.group(3)):
                    ex = label_at.get(jm.group(3))
                    if ex is not None and ex > j:
                        spans.append((pos, ex))
                    break
    cands: list = []                   # loops that may be rotated
    reserved: set[int] = set()
    out: dict[int, str] = {}           # index -> replacement text
    after: dict[int, str] = {}         # index -> text added after the line
    count = 0
    for i in range(n):
        m = _HEAD.match(lines[i])
        if not m or label_at.get(m.group(1)) != i:
            continue
        head = m.group(1)
        # the test: up to the first conditional jump to an exit label
        j = i + 1
        local: list[str] = []
        pending_nv: set[str] = set()
        test_end = None
        ninstr = 0
        while j < n and ninstr <= MAX_TEST:
            ln = lines[j]
            lab = _LABEL.match(ln) if ln and ln[0] not in " \t" else None
            if lab:
                if _CMP_NV.match(lab.group(1)):
                    local.append(lab.group(1))
                    j += 1
                    continue
                break
            if not _is_instruction(ln):
                break               # a comment, a blank line, a directive
            if _RETCC.match(ln):
                test_end = j        # the exit is a conditional return
                break
            if (_BAD_OP.match(ln) or _JP_INDIRECT.match(ln)
                    or _CALL_COND.match(ln)):
                break
            jm = _JCC.match(ln)
            if jm and _END.match(jm.group(3)):
                test_end = j
                break
            if jm and _CMP_NV.match(jm.group(3)):
                pending_nv.add(jm.group(3))
                ninstr += 1
                j += 1
                continue
            if _JMP.match(ln) or jm:
                break
            ninstr += 1
            j += 1
        if test_end is None or ninstr == 0:
            continue
        if sum(_bytes(x) for x in lines[i + 1:test_end]
               if _is_instruction(x)) > MAX_BYTES:
            continue
        # every jump to a @CMP_NV label stays inside the copied test
        if not pending_nv <= set(local):
            continue
        rc = _RETCC.match(lines[test_end])
        if rc:
            cond = rc.group(1)
            # the back jump is followed by labels and the RET of the exit
            k = None
            for x in range(test_end + 1, n):
                bm = _JMP.match(lines[x])
                if not bm or bm.group(2) != head:
                    continue
                y = x + 1
                while y < n and (not lines[y].strip()
                                 or lines[y].lstrip().startswith(";")
                                 or (lines[y][0] not in " \t"
                                     and _LABEL.match(lines[y]))):
                    y += 1
                if y < n and _RET.match(lines[y]):
                    k = x
                    break
            if k is None or k in reserved:
                continue
        else:
            jm = _JCC.match(lines[test_end])
            cond = jm.group(2)
            exit_label = jm.group(3)
            ex = label_at.get(exit_label)
            if ex is None or ex <= test_end:
                continue
            # the back jump: the last line before the exit label
            k = ex - 1
            while k > test_end and not lines[k].strip():
                k -= 1
            bm = _JMP.match(lines[k])
            if not bm or bm.group(2) != head or k in reserved:
                continue
        if any(idx in reserved for idx in (test_end, k)):
            continue
        if any(_HEAD.match(lines[x]) for x in range(test_end + 1, k)):
            # a loop inside: only a short test pays
            if INNERMOST and sum(_bytes(x) for x in lines[i + 1:test_end]
                                 if _is_instruction(x)) > OUTER_BYTES:
                continue
        body_bytes = sum(_bytes(x) for x in lines[test_end + 1:k]
                         if _is_instruction(x))
        has_call = any(_CALL.match(x) for x in lines[test_end + 1:k])
        nested = any(a < i and k < b for a, b in spans)
        if nested:
            ok = body_bytes <= NESTED_BYTES and (CALLS_OK or not has_call)
        else:
            ok = body_bytes <= BODY_BYTES and (CALLS_OK or not has_call)
        if not ok:
            continue
        body_label = f"@RB{count}"
        count += 1
        depth = sum(1 for a, b in spans if a < i and k < b)
        rename = {lab: lab + "r" for lab in local}
        copy: list[str] = []
        for idx in range(i + 1, test_end):
            ln = lines[idx]
            for a, b in rename.items():
                ln = re.sub(re.escape(a) + r"(?![A-Za-z0-9_@?$.])", b, ln)
            copy.append(ln)
        copy = _trim_junction(lines, k, copy)
        copy.append(f"\tjp\t{_INV[cond.lower()]},{body_label}")
        cost = sum(_bytes(x) for x in copy if _is_instruction(x)) + 1
        cands.append((-depth, body_bytes, k, test_end, copy, body_label, cost))
        reserved.update((k, test_end))
    # Budget: the deepest loops with the shortest bodies first, until the
    # copies have used about 1.5 percent of the code of the program.
    budget = max(MIN_BUDGET, sum(_bytes(x) for x in lines
                                 if _is_code(x)) * BUDGET_PERCENT // 100)
    cands.sort(key=lambda c: c[:2])
    for _d, _bb, k, test_end, copy, body_label, cost in cands:
        if cost > budget:
            continue
        budget -= cost
        out[k] = "\n".join(copy)
        after[test_end] = body_label + ":"
    if not out:
        return code
    res: list[str] = []
    for i, ln in enumerate(lines):
        res.append(out.get(i, ln))
        if i in after:
            res.append(after[i])
    return "\n".join(res)
