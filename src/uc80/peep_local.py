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
4. ``jr cc,L`` or ``jp cc,L`` where the label L stands directly before a
   ``ret`` becomes ``ret cc``: the jump would only run the return.  A
   one-byte instruction, and 1 T-state less when the jump is taken and
   2 less when it is not (uc80 --no-param-reg turns the rule off).
5. A reload of A that the path already did (docs/byte_regs.md).  In
   ``ld A,x / and K / cp C1 / jr nz,L`` ... ``L: ld A,x / and K / cp C2``
   the label L has no other reference and follows an unconditional jump,
   so A holds ``x & K`` at L: the two instructions at L go.  The rule
   needs the instructions to be next to each other (a volatile access has
   a fence label beside it) and turns off with ``--no-byte-cse``.
"""

from __future__ import annotations

import re

RET_CC = True
RELOAD = True

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


def _label_before_ret(lines: list[str]) -> set[str]:
    """The labels that stand directly before a ``ret`` (other labels and
    comments may lie between)."""
    found: set[str] = set()
    pending: list[str] = []
    for ln in lines:
        if _is_comment(ln):
            continue
        if ln[0] not in " \t":
            text = ln.split(";", 1)[0].strip()
            if text.endswith(":") and " " not in text and "\t" not in text:
                pending.append(text[:-1])
                continue
            pending = []
            continue
        if _split(ln) == ("ret", []):
            found.update(pending)
        pending = []
    return found


_ALU_IMM = ("and", "or", "xor", "sub", "add", "adc", "sbc")


def _alu_a_imm(ins) -> bool:
    """``and K``, ``or K``, ``xor K``, ``sub K`` or ``add A,K`` with an
    immediate K: an instruction that changes A and reads no memory."""
    if ins is None:
        return False
    mnem, ops = ins
    if mnem not in _ALU_IMM:
        return False
    if mnem in ("add", "adc", "sbc"):
        return len(ops) == 2 and ops[0].lower() == "a" and bool(
            _IMM.match(ops[1]))
    return len(ops) == 1 and bool(_IMM.match(ops[0]))


def _norm(ins):
    return (ins[0], tuple(o.lower() for o in ins[1])) if ins else None


def _redundant_reload(lines: list[str]) -> tuple[list[str], bool]:
    """Rule 5."""
    n = len(lines)
    label_at: dict[int, str] = {}
    name_idx: dict[str, int] = {}
    for i, ln in enumerate(lines):
        if ln and ln[0] not in " \t":
            t = ln.split(";", 1)[0].strip()
            if t.endswith(":") and " " not in t and "\t" not in t:
                label_at[i] = t[:-1]
                name_idx[t[:-1]] = i
    if not label_at:
        return lines, False
    refs: dict[str, int] = {}
    ref_line: dict[str, int] = {}
    tok = re.compile(r"[@?$.A-Za-z_][@?$.A-Za-z0-9_]*")
    for i, ln in enumerate(lines):
        if i in label_at:
            continue
        for t in tok.findall(ln.split(";", 1)[0]):
            if t in name_idx:
                refs[t] = refs.get(t, 0) + 1
                ref_line[t] = i
    ins = [_split(ln) for ln in lines]
    drop: set[int] = set()

    def prev_ins(i):
        """Index of the instruction line before i; None when a label, a
        directive or the start of the text comes first."""
        j = i - 1
        while j >= 0 and _is_comment(lines[j]):
            j -= 1
        if j < 0 or ins[j] is None:
            return None
        return j

    for p, name in label_at.items():
        if refs.get(name) != 1:
            continue
        jline = ref_line[name]
        jins = ins[jline]
        if (jins is None or jins[0] not in ("jr", "jp") or len(jins[1]) != 2
                or jins[1][1] != name):
            continue
        # the line before the label ends the code of the previous path
        q = prev_ins(p)
        if q is None:
            continue
        qi = ins[q]
        if not ((qi[0] in ("jp", "jr") and len(qi[1]) == 1
                 and qi[1][0].lower() not in ("(hl)",))
                or qi == ("ret", []) or qi[0] in ("reti", "retn")):
            continue
        # S: ld A,x and the ALU instructions after the label
        s_idx = []
        k = p + 1
        while k < n and _is_comment(lines[k]):
            k += 1
        if k >= n or ins[k] is None:
            continue
        first = ins[k]
        if not (first[0] == "ld" and len(first[1]) == 2
                and first[1][0].lower() == "a"
                and first[1][1].lower() not in ("a",)):
            continue
        s_idx.append(k)
        k += 1
        while len(s_idx) < 3:
            while k < n and _is_comment(lines[k]):
                k += 1
            if k < n and _alu_a_imm(ins[k]):
                s_idx.append(k)
                k += 1
            else:
                break
        if len(s_idx) < 2:
            continue                    # a bare reload gains nothing here
        # before the jump: S, then at most one cp
        want = [_norm(ins[i]) for i in s_idx]
        j = prev_ins(jline)
        if j is None:
            continue
        if ins[j][0] == "cp":
            j = prev_ins(j)
            if j is None:
                continue
        seq = []
        while j is not None and len(seq) < len(want):
            seq.append(_norm(ins[j]))
            j = prev_ins(j)
        seq.reverse()
        if seq != want:
            continue
        drop.update(s_idx)
    if not drop:
        return lines, False
    return [ln for i, ln in enumerate(lines) if i not in drop], True


def _pass(lines: list[str]) -> tuple[list[str], bool]:
    reloaded = False
    if RELOAD:
        lines, reloaded = _redundant_reload(lines)
    out = list(lines)
    changed = reloaded
    drop: set[int] = set()
    n = len(out)
    ret_labels = _label_before_ret(out) if RET_CC else set()
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
        # Rule 4.
        if (ret_labels and ins[0] in ("jr", "jp") and len(ins[1]) == 2
                and ins[1][1] in ret_labels
                and ins[1][0].lower() in (("z", "nz", "c", "nc")
                                          if ins[0] == "jr" else
                                          ("z", "nz", "c", "nc", "po", "pe",
                                           "p", "m"))):
            out[i] = f"\tret\t{ins[1][0]}"
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
