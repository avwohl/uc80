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
  label.

A test that holds a call is copied as it is.  A test that is a short
``&&`` chain is rotated at its first jump only: the copy ends at the first
conditional exit and jumps to the label that is put after the first exit
jump in the top test.
"""

from __future__ import annotations

import re

_HEAD = re.compile(r"^(@(?:FOR|WHILE)\d+):\s*(;.*)?$")
_LABEL = re.compile(r"^(@?[A-Za-z_?$.][A-Za-z0-9_@?$.]*):")
_JCC = re.compile(
    r"^\t(jr|jp)\s+(nz|z|nc|c|po|pe|p|m),\s*(\S+?)\s*(?:;.*)?$", re.I)
_JMP = re.compile(r"^\t(jr|jp)\s+(@\S+?)\s*(?:;.*)?$", re.I)
_END = re.compile(r"^@END(?:FOR|WHILE)\d+$")
_CMP_NV = re.compile(r"^@CMP_NV\d+$")
_BAD_OP = re.compile(r"^\t(ret|reti|retn|rst|djnz|halt)\b", re.I)
_CALL_COND = re.compile(r"^\tcall\s+\w+\s*,", re.I)
_JP_INDIRECT = re.compile(r"^\tjp\s+\(", re.I)
_INV = {"nc": "c", "c": "nc", "nz": "z", "z": "nz", "p": "m", "m": "p",
        "po": "pe", "pe": "po"}
MAX_TEST = 12


def _is_instruction(line: str) -> bool:
    return bool(line) and line[0] in " \t" and bool(
        line.split(";", 1)[0].strip())


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
        # every jump to a @CMP_NV label stays inside the copied test
        if not pending_nv <= set(local):
            continue
        jm = _JCC.match(lines[test_end])
        exit_label = jm.group(3)
        ex = label_at.get(exit_label)
        if ex is None or ex <= test_end:
            continue
        # the back jump: the last line before the exit label
        k = ex - 1
        while k > test_end and not lines[k].strip():
            k -= 1
        bm = _JMP.match(lines[k])
        if not bm or bm.group(2) != head or k in out:
            continue
        if any(idx in out or idx in after for idx in (test_end, k)):
            continue
        body_label = f"@RB{count}"
        count += 1
        rename = {lab: lab + "r" for lab in local}
        copy: list[str] = []
        for idx in range(i + 1, test_end):
            ln = lines[idx]
            for a, b in rename.items():
                ln = re.sub(re.escape(a) + r"(?![A-Za-z0-9_@?$.])", b, ln)
            copy.append(ln)
        copy = _trim_junction(lines, k, copy)
        copy.append(f"\tjp\t{_INV[jm.group(2).lower()]},{body_label}")
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
