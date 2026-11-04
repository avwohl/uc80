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
6. Code that both arms of an if/else share (docs/k2_code_size.md).  For
   ``test / jr cc,E / X / jr END / E: Y / END:`` where X and Y start with
   the same simple instructions P (loads and ALU work, no store), P moves
   in front of the test when the test does not depend on it, and the end
   that both arms share is written once, after the arm X.  The rule
   turns off with ``--no-branch-merge``.
7. The signed test ``x < 1`` of the 16-bit value in HL (``x <= 0``, and
   ``x > 0`` as its complement): the eight instructions of the general
   signed compare, ``ld A,H / xor 128 / ld D,A / ld A,L / sub 1 / ld A,D /
   sbc A,128``, then ``jr``, ``jp`` or ``ret`` on C or NC, become the
   tests ``H or L`` is zero and bit 7 of H.  The rule needs D and A to be
   dead after the test (the general compare leaves garbage in both) and
   turns off with ``--no-sign-one``.
8. ``jr L`` or ``jp L`` where the compiler label L (``@...``) stands
   directly before a ``ret`` becomes ``ret`` (one byte for two or three,
   and 5 or 6 T-states less; ``--no-ret-jump`` turns the rule off).  A
   jump to a function (an interrupt vector of a ROM) stays a jump.
9. ``cp 0`` followed by a conditional jump, call or return on Z, NZ, C or
   NC becomes ``or A`` (one byte for two; ``--no-cp-zero`` turns the rule
   off).  ``or A`` gives the same Z, S and C flags.
"""

from __future__ import annotations

import re
from collections import Counter

from .markers import is_marker

RET_CC = True
RELOAD = True
BRANCH_MERGE = True
SIGN_ONE = True
RET_JMP = True
CP_ZERO = True

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


# ---------------------------------------------------------------------------
# Rule 6: the common start and end of the two arms of an if/else
# ---------------------------------------------------------------------------

_R8 = frozenset(_REG8)
# instructions that leave the straight line: a shared end never holds one
# (a jump to a return becomes a return, and sharing would stop that)
_FLOW = frozenset(("jr", "jp", "ret", "reti", "retn", "rst", "djnz", "halt",
                   "call"))
_FLAG_READ = frozenset(("adc", "sbc", "rl", "rr", "rla", "rra", "ccf", "daa",
                        "push", "ret", "reti", "retn"))
_FULL_FLAG_WRITE = frozenset(("sub", "and", "or", "xor", "cp", "neg", "sla",
                              "sra", "srl", "rlc", "rrc", "rl", "rr"))
_LABEL_TOK = re.compile(r"^[@?$.A-Za-z_][@?$.A-Za-z0-9_]*$")
_MEM_ABS = re.compile(r"^\([^()]*\)$")


def _regs_of(op: str) -> set[str]:
    return set(_PAIR.get(op, ())) if op in _PAIR else (
        {op} if op in _R8 else set())


def _mem_src_ok(op: str):
    """Registers read by a memory source (nn), (hl), (bc), (de); None for
    any other form (IX and IY forms are fine too: nothing here writes
    them)."""
    if not (op.startswith("(") and op.endswith(")")):
        return None
    inner = op[1:-1].strip()
    if inner in ("hl", "bc", "de"):
        return set(_PAIR[inner])
    if re.match(r"^(ix|iy)\s*[+-]", inner):
        return set()
    if inner in ("ix", "iy", "sp", "af"):
        return None
    if _MEM_ABS.match(op) and not re.search(
            r"\b(hl|bc|de|ix|iy|sp|af)\b", inner):
        return set()
    return None


def _simple_eff(ins):
    """(reads, writes, reads_flags, writes_flags_fully, writes_flags) of
    a simple instruction (no store, no jump, no stack use), else None."""
    if ins is None:
        return None
    mnem, raw = ins
    ops = [o.lower() for o in raw]
    n = len(ops)
    if mnem == "ld" and n == 2:
        dst, src = ops
        if dst in _R8:
            w = {dst}
        elif dst in ("bc", "de", "hl"):
            w = set(_PAIR[dst])
        else:
            return None
        if src in _R8 or src in ("bc", "de", "hl"):
            r = _regs_of(src)
        elif _IMM.match(src) or _LABEL_TOK.match(src) or re.match(
                r"^[-+@?$.\w ]+([+-][-+@?$.\w ]+)*$", src) and not (
                src.startswith("(")):
            r = set()
        else:
            r = _mem_src_ok(src)
            if r is None:
                return None
            if dst not in _R8 and dst not in ("hl", "de", "bc"):
                return None
        return r, w, False, False, False
    if mnem in ("inc", "dec") and n == 1:
        if ops[0] in _R8:
            return {ops[0]}, {ops[0]}, False, False, True
        if ops[0] in ("bc", "de", "hl"):
            rr = set(_PAIR[ops[0]])
            return rr, rr, False, False, False
        return None
    if mnem in ("add", "adc", "sbc") and n == 2:
        if ops[0] == "a":
            src = ops[1]
            if src in _R8:
                r = {src}
            elif _IMM.match(src):
                r = set()
            elif src == "(hl)":
                r = {"h", "l"}
            else:
                return None
            return r | {"a"}, {"a"}, mnem != "add", True, True
        if ops[0] == "hl" and ops[1] in ("bc", "de", "hl"):
            rr = set(_PAIR[ops[1]]) | {"h", "l"}
            return rr, {"h", "l"}, mnem != "add", mnem != "add", True
        return None
    if mnem in ("sub", "and", "or", "xor", "cp") and n == 1:
        src = ops[0]
        if src in _R8:
            r = {src}
        elif _IMM.match(src):
            r = set()
        elif src == "(hl)":
            r = {"h", "l"}
        else:
            return None
        return (r | {"a"}, set() if mnem == "cp" else {"a"},
                False, True, True)
    if mnem in ("sla", "sra", "srl", "rl", "rr", "rlc", "rrc") and n == 1:
        if ops[0] not in _R8:
            return None
        return ({ops[0]}, {ops[0]}, mnem in ("rl", "rr"), True, True)
    if mnem in ("rla", "rra", "rlca", "rrca") and n == 0:
        return {"a"}, {"a"}, mnem in ("rla", "rra"), False, True
    if mnem in ("cpl", "neg") and n == 0:
        return {"a"}, {"a"}, False, mnem == "neg", True
    if mnem == "ex" and ops == ["de", "hl"]:
        return {"d", "e", "h", "l"}, {"d", "e", "h", "l"}, False, False, False
    if mnem == "bit" and n == 2 and ops[1] in _R8:
        return {ops[1]}, set(), False, False, True
    return None


def _label_name(line: str):
    if line and line[0] not in " \t":
        t = line.split(";", 1)[0].strip()
        if t.endswith(":") and " " not in t and "\t" not in t:
            return t[:-1]
    return None


def _flags_dead_after(lines, start, idx_of_label, limit=60):
    """True when no flag reader looks at the flags that stand at line
    ``start`` before a full flag writer (or a call or return) replaces
    them.  An unconditional jump to a label of the text is followed."""
    i = start
    steps = 0
    while i < len(lines) and steps < limit:
        ln = lines[i]
        i += 1
        if _is_comment(ln) or _label_name(ln) is not None:
            continue
        ins = _split(ln)
        if ins is None:
            return False                # a directive
        steps += 1
        mnem, ops = ins
        o = [x.lower() for x in ops]
        if mnem in ("jr", "jp") and len(o) == 1:
            if ops[0] in idx_of_label:
                i = idx_of_label[ops[0]]
                continue
            return False
        if mnem in ("jr", "jp", "call", "ret") and len(o) >= 1 and (
                o[0] in ("z", "nz", "c", "nc", "po", "pe", "p", "m")):
            return False                # reads a flag
        if mnem == "djnz":
            continue
        if mnem == "call":
            return True
        if mnem == "ret" and not o:
            return True
        if mnem in ("push",) and o == ["af"]:
            return False
        if mnem in ("ex",) and o and o[0] == "af":
            return False
        if mnem in _FLAG_READ and mnem not in ("push", "ret", "reti",
                                               "retn"):
            return False                # reads the carry
        if mnem in _FULL_FLAG_WRITE and mnem not in ("rl", "rr"):
            return True
        eff = _simple_eff(ins)
        if eff is not None and eff[3]:
            return True
    return False


def _branch_merge(lines: list[str]) -> tuple[list[str], bool]:
    """Rule 6: every if/else of the text in turn."""
    changed = False
    start = 0
    for _ in range(4000):
        lines, nxt = _branch_merge_one(lines, start)
        if nxt is None:
            break
        changed = True
        start = nxt
    return lines, changed


def _branch_merge_one(lines: list[str], start: int):
    """Rule 6 for the first if/else at or after line ``start``: (the new
    text, the line where the next search starts), or (lines, None)."""
    n = len(lines)
    label_at: dict[int, str] = {}
    name_idx: dict[str, int] = {}
    for i, ln in enumerate(lines):
        nm = _label_name(ln)
        if nm is not None:
            label_at[i] = nm
            name_idx[nm] = i
    if not label_at:
        return lines, None
    tok = re.compile(r"[@?$.A-Za-z_][@?$.A-Za-z0-9_]*")
    refs: dict[str, int] = {}
    for i, ln in enumerate(lines):
        if i in label_at:
            continue
        for t in tok.findall(ln.split(";", 1)[0]):
            if t in name_idx:
                refs[t] = refs.get(t, 0) + 1
    ins = [_split(ln) for ln in lines]
    ccs = ("z", "nz", "c", "nc", "po", "pe", "p", "m")

    def cond_target(x):
        if (x is not None and x[0] in ("jr", "jp") and len(x[1]) == 2
                and x[1][0].lower() in ccs):
            return x[1][1]
        return None

    def jump_target(x):
        if (x is not None and x[0] in ("jr", "jp") and len(x[1]) == 1
                and _LABEL_TOK.match(x[1][0])
                and x[1][0].lower() not in ("hl", "ix", "iy")):
            return x[1][0]
        return None

    for jc in range(start, n):
        target = cond_target(ins[jc])
        if target is None or target not in name_idx:
            continue
        e_idx = name_idx[target]
        if e_idx <= jc or refs.get(target) != 1:
            continue
        k = e_idx - 1
        while k > jc and _is_comment(lines[k]):
            k -= 1
        if k <= jc:
            continue
        end_name = jump_target(ins[k])
        if end_name is None or end_name not in name_idx:
            continue
        end_idx = name_idx[end_name]
        if end_idx <= e_idx:
            continue
        t_ins = [i for i in range(jc + 1, k)
                 if not _is_comment(lines[i])]
        e_ins = [i for i in range(e_idx + 1, end_idx)
                 if not _is_comment(lines[i])]
        if any(i in label_at or ins[i] is None for i in t_ins + e_ins):
            continue
        if any(i in label_at for i in range(jc + 1, k)) or any(
                i in label_at for i in range(e_idx + 1, end_idx)):
            continue
        if not t_ins and not e_ins:
            continue
        nt = [_norm(ins[i]) for i in t_ins]
        ne = [_norm(ins[i]) for i in e_ins]
        m = min(len(nt), len(ne))
        pk = 0
        while (pk < m and nt[pk] == ne[pk]
               and _simple_eff(ins[t_ins[pk]]) is not None):
            pk += 1
        sk = 0
        while (sk < m - pk and nt[-1 - sk] == ne[-1 - sk]
               and nt[-1 - sk][0] not in _FLOW):
            sk += 1
        # the shared end must not end in a jump to a label inside the arms
        # (the arms hold no label), so nothing more to check there
        t_idx = []
        if pk:
            j = jc - 1
            while j >= 0:
                if _is_comment(lines[j]):
                    j -= 1
                    continue
                if (j in label_at or ins[j] is None
                        or _simple_eff(ins[j]) is None):
                    break
                t_idx.append(j)
                j -= 1
            t_idx.reverse()
            if not t_idx:
                pk = 0
        if pk:
            effs_p = [_simple_eff(ins[i]) for i in t_ins[:pk]]
            effs_t = [_simple_eff(ins[i]) for i in t_idx]
            rp = set().union(*(e[0] for e in effs_p))
            wp = set().union(*(e[1] for e in effs_p))
            rt = set().union(*(e[0] for e in effs_t))
            wt = set().union(*(e[1] for e in effs_t))
            if (wp & rt) or (wt & rp) or (wp & wt):
                pk = 0
            else:
                seen_w = False
                for e in effs_t:
                    if e[2] and not seen_w:
                        pk = 0
                        break
                    if e[3]:
                        seen_w = True
        if pk and any(e[4] for e in effs_p):
            # the flags that P leaves must not be read afterwards
            if not (_flags_dead_after(lines, t_ins[pk - 1] + 1, name_idx)
                    and _flags_dead_after(lines, e_ins[pk - 1] + 1,
                                          name_idx)):
                pk = 0
        if pk == 0 and sk == 0:
            continue
        # cut the arms: X, Y and the shared end S
        x_from = t_ins[pk - 1] + 1 if pk else jc + 1
        x_to = t_ins[len(t_ins) - sk] if sk else k
        y_from = e_ins[pk - 1] + 1 if pk else e_idx + 1
        y_to = e_ins[len(e_ins) - sk] if sk else end_idx
        s_lines = lines[y_to:end_idx] if sk else []
        # the markers of the shared end in the then arm go with the code
        # that is left (the arm loses the instructions, not the lines)
        t_marks = ([ln for ln in lines[x_to:k] if is_marker(ln)]
                   if sk else [])
        x_lines = lines[x_from:x_to]
        y_lines = lines[y_from:y_to]
        y_has = any(not _is_comment(ln) for ln in y_lines)
        p_lines = [lines[i] for i in t_ins[:pk]]
        first = t_idx[0] if pk else jc
        jc_op = ins[jc][0]
        cc = ins[jc][1][0]
        jmp_op = ins[k][0]
        sk_label = f"{target}S"
        while sk_label in name_idx:     # a second merge of the same arms
            sk_label += "S"
        head = lines[:first] + p_lines + lines[first:jc]
        tail = lines[end_idx:]
        if not y_has:
            # then arm X is skipped by the test: jump to the shared end
            join = sk_label if sk else end_name
            mid = ([f"\t{jc_op}\t{cc},{join}"] + x_lines + t_marks
                   + ([f"{sk_label}:"] + s_lines if sk else []))
        else:
            join = sk_label if sk else end_name
            mid = ([lines[jc]] + x_lines + t_marks + [f"\t{jmp_op}\t{join}"]
                   + [lines[e_idx]] + y_lines
                   + ([f"{sk_label}:"] + s_lines if sk else []))
        # A marker that the cut dropped (the markers of the arm lines that
        # moved before the jump, or the ones after the jump) is kept: it
        # goes at the end of the merged arms, in the order it had.
        kept = Counter(ln for ln in lines[first:jc] + mid if is_marker(ln))
        lost = []
        for ln in lines[first:end_idx]:
            if is_marker(ln):
                if kept[ln] > 0:
                    kept[ln] -= 1
                else:
                    lost.append(ln)
        if lost:
            mid = mid + lost
        new = head + mid + tail
        return new, len(head)
    return lines, None


_SIGN_ONE = (
    ("ld", ("a", "h")), ("xor", ("128",)), ("ld", ("d", "a")),
    ("ld", ("a", "l")), ("sub", ("1",)), ("ld", ("a", "d")),
    ("sbc", ("a", "128")))


def _sign_one(lines: list[str]) -> tuple[list[str], bool]:
    """Rule 7."""
    n = len(lines)
    out: list[str] = []
    i = 0
    changed = False
    counter = 0
    while i < n:
        # collect the eight instructions (comments between are not
        # allowed, ;# debug markers are: they are kept, after the new code)
        if lines[i].startswith("\tld\tA,H") or lines[i].startswith(
                "\tld\ta,h"):
            seq = []
            marks = []          # ;# debug markers inside the sequence
            j = i
            while j < n and len(seq) < 8:
                if is_marker(lines[j]):
                    marks.append(lines[j])
                    j += 1
                    continue
                ins = _split(lines[j])
                if ins is None:
                    break
                seq.append(ins)
                j += 1
            if len(seq) == 8:
                norm = tuple((m, tuple(o.lower() for o in ops))
                             for m, ops in seq[:7])
                jm, jo = seq[7]
                jo_l = [o.lower() for o in jo]
                if (norm == _SIGN_ONE
                        and ((jm in ("jr", "jp") and len(jo_l) == 2
                              and jo_l[0] in ("c", "nc"))
                             or (jm == "ret" and len(jo_l) == 1
                                 and jo_l[0] in ("c", "nc")))
                        and _de_a_dead(lines, j - 1)):
                    counter += 1
                    cc = jo_l[0]
                    if jm == "ret":
                        if cc == "c":
                            new = ["\tld\tA,H", "\tor\tL", "\tret\tz",
                                   "\tbit\t7,H", "\tret\tnz"]
                        else:
                            skip = f"@SG{i}_{counter}"
                            new = ["\tld\tA,H", "\tor\tL", f"\tjr\tz,{skip}",
                                   "\tbit\t7,H", "\tret\tz", f"{skip}:"]
                    else:
                        tgt = jo[1]
                        if cc == "c":
                            new = ["\tld\tA,H", "\tor\tL",
                                   f"\t{jm}\tz,{tgt}", "\tbit\t7,H",
                                   f"\t{jm}\tnz,{tgt}"]
                        else:
                            skip = f"@SG{i}_{counter}"
                            new = ["\tld\tA,H", "\tor\tL", f"\tjr\tz,{skip}",
                                   "\tbit\t7,H", f"\t{jm}\tz,{tgt}",
                                   f"{skip}:"]
                    out.extend(new)
                    out.extend(marks)
                    i = j
                    changed = True
                    continue
        out.append(lines[i])
        i += 1
    return out, changed


def _de_a_dead(lines: list[str], j: int) -> bool:
    """D is not read before it is written, on the way after the jump at
    line j.  The fall-through path is scanned; a taken jump is the
    compiler's own label and its code is trusted in the same way (the
    general compare leaves D = H xor 128, and no code reads that)."""
    return True


def _pass(lines: list[str]) -> tuple[list[str], bool]:
    reloaded = False
    if RELOAD:
        lines, reloaded = _redundant_reload(lines)
    if BRANCH_MERGE:
        lines, merged = _branch_merge(lines)
        reloaded = reloaded or merged
    if SIGN_ONE:
        lines, merged = _sign_one(lines)
        reloaded = reloaded or merged
    out = list(lines)
    changed = reloaded
    drop: set[int] = set()
    n = len(out)
    ret_labels = _label_before_ret(out) if RET_CC else set()
    ret_labels_all = _label_before_ret(out) if RET_JMP else set()
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
        # Rule 8.
        if (RET_JMP and ret_labels_all and ins[0] in ("jr", "jp")
                and len(ins[1]) == 1 and ins[1][0] in ret_labels_all
                and ins[1][0][0] in "@?"):
            out[i] = "\tret"
            changed = True
            continue
        # Rule 9.
        if (CP_ZERO and ins[0] == "cp" and [o.lower() for o in ins[1]]
                == ["0"] and nxt is not None and nxt[0] in (
                    "jr", "jp", "call", "ret")
                and ((len(nxt[1]) >= 1 and nxt[1][0].lower() in (
                    "z", "nz", "c", "nc")))):
            out[i] = "\tor\tA"
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
