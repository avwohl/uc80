"""Copies and dead register writes, by a liveness pass (docs/k2_code_size.md).

The pass runs on the assembly text after the peephole rules, on each run of
text between two inline-asm regions (main._optimize_outside_asm).  It has
three steps.

1. Copy propagation inside a basic block.  After ``ld r,s`` (both byte
   registers) a later read of r in ``ld x,r`` or in an ALU instruction on
   r reads s, as long as neither r nor s has been written.  A label, a
   call or an unconditional jump ends the block.
2. Dead register writes.  A backward liveness pass over the bytes A, B, C,
   D, E, H and L (the flags are not tracked) finds ``ld r,s`` and
   ``ld rr,n`` instructions whose result is never read, and deletes them.
   Only a load from a register or a number goes: a memory read might be
   volatile, the address of a symbol can be a link hook, and ``ld A,I``
   sets the flags.  A call reads every register and writes every register, a
   return reads every register, and a jump to a label outside the run reads
   every register.  A run with an instruction that the effect tables do
   not know is left alone.
3. Dead code.  An instruction behind an unconditional jump, a return or
   ``jp (HL)`` with no label between is deleted, and so is a jump to the
   label that follows it.
4. ``inc SP / inc SP`` after a call (the cleanup of an argument when DE or
   BC holds a register local) becomes ``pop AF`` when A is dead: one byte
   for two, and 4 T-states less.  The flags after a call are never used.
5. A word that a register pair already holds.  After ``ld (X),HL`` or
   ``ld HL,(X)`` the pair HL holds the word at X until a write to HL or to
   X.  A later ``ld HL,(X)`` goes, and a later ``ld DE,(X)`` becomes
   ``ld D,H`` and ``ld E,L``.  A store through a pointer, a call, a block
   move or a label clears what the pass knows.  The pass tracks labels
   only, and it ignores the objects of fixed addresses (``___abs...``).
6. A constant that a register already holds.  After ``ld HL,5`` a later
   ``ld HL,5`` goes (also for a byte register).  A label, a call or a write
   to the register ends the fact.
7. The test ``while (n--)`` on a 16-bit value in memory:
   ``push HL / dec HL / ld (X),HL / pop HL / ld A,H / or L / jcc`` becomes
   ``ld A,H / or L / dec HL / ld (X),HL`` (HL holds n-1 afterwards; when
   HL is live after the test, one ``inc HL`` follows).

The pass turns off with ``--no-flow-opt``.
"""

from __future__ import annotations

import re

try:  # the register effects of one instruction
    from upeepz80.z80 import effect as _effect
except Exception:  # pragma: no cover - the pass is then off
    _effect = None

ENABLED = True
# a debug aid that bisects a wrong result: UC80_FLOW_LIMIT=N makes only the
# first N edits of the whole compile
_LIMIT = int(__import__("os").environ.get("UC80_FLOW_LIMIT", "-1"))
_COUNT = [0]


def _allow() -> bool:
    if _LIMIT < 0:
        return True
    _COUNT[0] += 1
    return _COUNT[0] <= _LIMIT

_BIT = {"a": 1, "b": 2, "c": 4, "d": 8, "e": 16, "h": 32, "l": 64}
_ALL = 127
_REG8 = tuple(_BIT)
_PAIRS = {"bc": ("b", "c"), "de": ("d", "e"), "hl": ("h", "l")}
_LABEL = re.compile(r"^([A-Za-z_@?$.][A-Za-z0-9_@?$.]*):")
_IMM = re.compile(r"^[-+]?(\d+|[0-9][0-9a-f]*h|0x[0-9a-f]+)$", re.IGNORECASE)
_ALU = ("add", "adc", "sub", "sbc", "and", "or", "xor", "cp")


class _Bail(Exception):
    pass


class _R:
    """One line of the run."""

    __slots__ = ("line", "kind", "op", "ops", "rd", "wr", "flow", "target",
                 "dead", "cond", "fw")

    def __init__(self, line: str):
        self.line = line
        self.kind = "nop"
        self.op = ""
        self.ops: list[str] = []
        self.rd = 0
        self.wr = 0
        self.flow = "next"
        self.target = None
        self.dead = False
        self.cond = False
        self.fw = False             # the instruction writes a flag


def _mask(regs) -> int:
    m = 0
    for r in regs:
        m |= _BIT.get(r, 0)
    return m


def _parse(lines: list[str]) -> list[_R]:
    out = []
    for ln in lines:
        r = _R(ln)
        out.append(r)
        if not ln or not ln.strip() or ln.lstrip().startswith(";"):
            continue
        if ln[0] not in " \t":
            if _LABEL.match(ln):
                r.kind = "label"
                r.op = _LABEL.match(ln).group(1)
            else:
                r.kind = "other"
            continue
        text = ln.split(";", 1)[0].strip()
        if not text:
            continue
        parts = text.split(None, 1)
        r.op = parts[0].lower()
        if r.op in ("db", "dw", "dd", "defb", "defw", "ds", "defs", "equ",
                    "public", "extrn", "cseg", "dseg", "common", "aseg",
                    "end", "org", "name", "title", ".z80", "if", "else",
                    "endif", "macro", "endm", "assume"):
            r.kind = "other"
            continue
        r.ops = [o.strip() for o in parts[1].split(",")] if len(parts) > 1 \
            else []
        r.kind = "ins"
        try:
            eff = _effect(r.op, ",".join(r.ops))
        except Exception:
            raise _Bail()
        if eff.size is None:
            raise _Bail()
        r.rd = _mask(eff.reads)
        r.wr = _mask(eff.writes)
        if eff.swap_de_hl:
            r.rd |= 8 | 16 | 32 | 64
            r.wr |= 8 | 16 | 32 | 64
        r.flow = eff.flow
        r.target = eff.target
        r.cond = bool(eff.cond)
        r.fw = any(w.startswith("f") for w in eff.writes)
        if r.op == "halt":
            r.flow = "next"        # an interrupt resumes at the next line
        if eff.flow == "call":
            r.rd = _ALL
            r.wr = _ALL
            if eff.cond:
                raise _Bail()
        if r.op in ("exx",):
            raise _Bail()
        if r.op == "ex" and r.ops and r.ops[0].lower() == "af":
            raise _Bail()
    return out


def _liveness(rs: list[_R]):
    """live_out of every record."""
    n = len(rs)
    labels = {r.op: i for i, r in enumerate(rs) if r.kind == "label"}
    # the next real record after i (labels and nops flow through)
    live_in = [0] * n
    live_out = [0] * n
    succs: list[list[int]] = [[] for _ in range(n)]
    exit_live = [0] * n
    for i, r in enumerate(rs):
        if r.kind != "ins":
            if i + 1 < n:
                succs[i] = [i + 1]
            else:
                exit_live[i] = _ALL
            continue
        f = r.flow
        nxt = i + 1 if i + 1 < n else None
        if f in ("next", "call"):
            if nxt is None:
                exit_live[i] = _ALL
            else:
                succs[i] = [nxt]
        elif f == "branch":
            if nxt is None:
                exit_live[i] = _ALL
            else:
                succs[i].append(nxt)
            t = labels.get(r.target) if r.target else None
            if t is None:
                exit_live[i] = _ALL
            else:
                succs[i].append(t)
        elif f == "jump":
            t = labels.get(r.target) if r.target else None
            if t is None:
                exit_live[i] = _ALL
            else:
                succs[i] = [t]
        else:                        # return, stop
            exit_live[i] = _ALL
            if r.cond and nxt is not None:
                succs[i] = [nxt]     # ret cc also falls through
    changed = True
    order = list(range(n - 1, -1, -1))
    while changed:
        changed = False
        for i in order:
            out = exit_live[i]
            for s in succs[i]:
                out |= live_in[s]
            r = rs[i]
            if r.kind == "ins":
                new = (out & ~r.wr) | r.rd
                if r.flow in ("return", "stop"):
                    new |= _ALL
            else:
                new = out
            if out != live_out[i] or new != live_in[i]:
                live_out[i] = out
                live_in[i] = new
                changed = True
    return live_out, live_in


def _is_reg8(o: str) -> bool:
    return o.lower() in _BIT


def _copy_prop(rs: list[_R]) -> bool:
    """Step 1."""
    changed = False
    cp: dict[str, str] = {}
    for r in rs:
        if r.kind == "label":
            cp.clear()
            continue
        if r.kind != "ins":
            continue
        ops = r.ops
        new_ops = None
        if r.op == "ld" and len(ops) == 2 and _is_reg8(ops[1]):
            src = ops[1].lower()
            dst = ops[0]
            ok_dst = _is_reg8(dst) or bool(
                re.match(r"^\((hl|(ix|iy)\s*[+-][^)]*)\)$", dst, re.I))
            if ok_dst and src in cp:
                new_ops = [dst, cp[src].upper()]
        elif r.op in _ALU:
            idx = len(ops) - 1
            if idx >= 0 and _is_reg8(ops[idx]) and (
                    len(ops) == 1 or (len(ops) == 2
                                      and ops[0].lower() == "a")):
                src = ops[idx].lower()
                if src in cp:
                    new_ops = list(ops)
                    new_ops[idx] = cp[src].upper()
        if new_ops is not None and new_ops != ops and _allow():
            # an address register in a store keeps its name; the register
            # operand is the only one that changes
            r.ops = new_ops
            r.line = f"\t{r.op}\t{','.join(new_ops)}"
            changed = True
            try:
                eff = _effect(r.op, ",".join(r.ops))
                r.rd = _mask(eff.reads)
            except Exception:
                raise _Bail()
        # invalidate by the writes
        if r.wr:
            for k in list(cp):
                if _BIT[k] & r.wr or _BIT[cp[k]] & r.wr:
                    del cp[k]
        if r.flow in ("call", "jump", "return", "stop"):
            cp.clear()
            continue
        if (r.op == "ld" and len(r.ops) == 2 and _is_reg8(r.ops[0])
                and _is_reg8(r.ops[1])):
            d, s = r.ops[0].lower(), r.ops[1].lower()
            if d != s:
                cp[d] = s
    return changed


def _dead_writes(rs: list[_R]) -> bool:
    """Step 2."""
    live_out, _ = _liveness(rs)
    changed = False
    for i, r in enumerate(rs):
        if r.kind != "ins" or r.op != "ld" or len(r.ops) != 2 or r.fw:
            continue                # ld A,I and ld A,R set the flags
        d = r.ops[0].lower()
        s = r.ops[1]
        if d in _BIT:
            dm = _BIT[d]
            # a symbol is not deleted: a load of an address can be the
            # way that the compiler makes the linker take a module
            # (__sc_float_on)
            if not (_is_reg8(s) or _IMM.match(s)):
                continue
            if s.lower() == d:
                if _allow():
                    r.dead = True
                    changed = True
                continue
            if not (dm & live_out[i]) and _allow():
                r.dead = True
                changed = True
        elif d in _PAIRS and _IMM.match(s):
            dm = _mask(_PAIRS[d])
            if not (dm & live_out[i]) and _allow():
                r.dead = True
                changed = True
    return changed


def _dead_code(rs: list[_R]) -> bool:
    """Step 3."""
    changed = False
    unreachable = False
    n = len(rs)
    labels_after: dict[int, set] = {}
    for i, r in enumerate(rs):
        if r.kind == "label":
            unreachable = False
            continue
        if r.kind != "ins":
            continue
        if unreachable:
            if _allow():
                r.dead = True
                changed = True
            continue
        if r.flow == "jump" and not r.dead and r.target:
            # a jump to the label that follows it
            j = i + 1
            while j < n and rs[j].kind in ("nop", "label"):
                if (rs[j].kind == "label" and rs[j].op == r.target
                        and _allow()):
                    r.dead = True
                    changed = True
                    break
                j += 1
            if r.dead:
                continue
        if not r.cond and r.flow in ("jump", "stop", "return"):
            unreachable = True
    return changed


_ADDR = re.compile(
    r"^\(\s*([A-Za-z_@?$.][A-Za-z0-9_@?$.]*)\s*(?:([+-])\s*(\d+))?\s*\)$")
_PAIR_NAMES = ("hl", "de", "bc")
_MEM_WRITE_OPS = frozenset((
    "inc", "dec", "rl", "rr", "rlc", "rrc", "sla", "sra", "srl", "sll",
    "set", "res", "ldi", "ldir", "ldd", "lddr", "ini", "inir", "ind",
    "indr", "rld", "rrd", "ex", "call", "rst", "halt"))


def _label_addr(op: str):
    """(label, offset) of an operand ``(label+n)``, else None.  The names
    of the objects at fixed addresses can alias each other: no."""
    m = _ADDR.match(op.strip())
    if m is None:
        return None
    name = m.group(1)
    if name.lower() in ("hl", "de", "bc", "ix", "iy", "sp", "af", "c"):
        return None
    if name.startswith("___abs") or name.lower() == "___abs":
        return None
    off = int(m.group(3)) if m.group(3) else 0
    if m.group(2) == "-":
        off = -off
    return name, off


def _mem_forward(rs: list[_R]) -> bool:
    """Step 5."""
    changed = False
    facts: dict[tuple[str, int], str] = {}

    def drop_overlap(sym: str, off: int, width: int) -> None:
        for (fs, fo) in list(facts):
            if fs == sym and fo <= off + width - 1 and fo + 1 >= off:
                del facts[(fs, fo)]

    def drop_pair_bytes(wr: int) -> None:
        for k in list(facts):
            if _mask(_PAIRS[facts[k]]) & wr:
                del facts[k]

    for r in rs:
        if r.kind == "label":
            facts.clear()
            continue
        if r.kind != "ins" or r.dead:
            continue
        ops = [o.strip() for o in r.ops]
        low = [o.lower() for o in ops]
        # a load of a pair from a label
        if (r.op == "ld" and len(ops) == 2 and low[0] in _PAIR_NAMES
                and _label_addr(ops[1]) is not None):
            sym, off = _label_addr(ops[1])
            held = facts.get((sym, off))
            if held == low[0]:
                if _allow():
                    r.dead = True
                    changed = True
                    continue
            elif held is not None and _allow():
                d, h = _PAIRS[low[0]], _PAIRS[held]
                r.line = (f"\tld\t{d[0].upper()},{h[0].upper()}\n"
                          f"\tld\t{d[1].upper()},{h[1].upper()}")
                r.op = "ld"
                r.ops = [d[0].upper(), h[0].upper()]
                r.wr = _mask(d)
                r.rd = _mask(h)
                drop_pair_bytes(r.wr)
                facts[(sym, off)] = low[0]
                changed = True
                continue
            drop_pair_bytes(r.wr)
            facts[(sym, off)] = low[0]
            continue
        # a store of a pair to a label
        if (r.op == "ld" and len(ops) == 2 and low[1] in _PAIR_NAMES
                and _label_addr(ops[0]) is not None):
            sym, off = _label_addr(ops[0])
            drop_overlap(sym, off, 2)
            facts[(sym, off)] = low[1]
            continue
        # other writes
        if r.flow == "call" or r.op in _MEM_WRITE_OPS and (
                r.op in ("ldi", "ldir", "ldd", "lddr", "ini", "inir", "ind",
                         "indr", "rld", "rrd", "call", "rst", "halt")
                or any(o.startswith("(") for o in ops)):
            facts.clear()
            continue
        if r.op == "ld" and ops and ops[0].startswith("("):
            tgt = _label_addr(ops[0])
            if tgt is None:
                facts.clear()
            else:
                drop_overlap(tgt[0], tgt[1], 1)
            continue
        if r.wr:
            drop_pair_bytes(r.wr)
        if r.flow in ("jump", "return", "stop"):
            facts.clear()
    return changed


def _int_of(op: str):
    op = op.strip()
    if not _IMM.match(op):
        return None
    t = op.lower()
    try:
        if t.endswith("h"):
            return int(t[:-1], 16)
        if t.startswith(("0x", "-0x", "+0x")):
            return int(t, 16)
        return int(t, 10)
    except ValueError:
        return None


def _const_forward(rs: list[_R]) -> bool:
    """Step 6."""
    changed = False
    known: dict[str, int] = {}
    for r in rs:
        if r.kind == "label":
            known.clear()
            continue
        if r.kind != "ins" or r.dead:
            continue
        ops = r.ops
        low = [o.lower() for o in ops]
        if r.op == "ld" and len(ops) == 2:
            dst = low[0]
            v = _int_of(ops[1])
            if v is not None and dst in _BIT:
                v &= 0xFF
                if known.get(dst) == v:
                    if _allow():
                        r.dead = True
                        changed = True
                        continue
                known[dst] = v
                continue
            if v is not None and dst in _PAIRS:
                v &= 0xFFFF
                hi, lo = _PAIRS[dst]
                if known.get(hi) == v >> 8 and known.get(lo) == v & 255:
                    if _allow():
                        r.dead = True
                        changed = True
                        continue
                known[hi] = v >> 8
                known[lo] = v & 255
                continue
            if dst in _BIT and low[1] in _BIT:
                if low[1] in known:
                    known[dst] = known[low[1]]
                else:
                    known.pop(dst, None)
                continue
        if r.wr:
            for k in list(known):
                if _BIT[k] & r.wr:
                    del known[k]
        if r.flow in ("jump", "return", "stop", "call"):
            known.clear()
    return changed


def _inc_sp(rs: list[_R]) -> bool:
    """Step 4."""
    live_out, _ = _liveness(rs)
    n = len(rs)
    changed = False
    i = 0
    while i < n:
        r = rs[i]
        if not (r.kind == "ins" and r.flow == "call" and not r.dead):
            i += 1
            continue
        j = i + 1
        run = []
        while j < n:
            q = rs[j]
            if q.kind == "nop":
                j += 1
                continue
            if (q.kind == "ins" and not q.dead and q.op == "inc"
                    and [o.lower() for o in q.ops] == ["sp"]):
                run.append(j)
                j += 1
                continue
            break
        pairs = len(run) // 2
        if (pairs and not (live_out[run[2 * pairs - 1]] & 1)
                and _allow()):
            for k in range(pairs):
                a, b = run[2 * k], run[2 * k + 1]
                rs[a].line = "\tpop\tAF"
                rs[b].dead = True
            changed = True
        i = j if run else i + 1
    return changed


def _post_dec(rs: list[_R]) -> bool:
    """Step 3."""
    live_out, _ = _liveness(rs)
    n = len(rs)
    changed = False
    i = 0
    while i < n:
        r = rs[i]
        if not (r.kind == "ins" and not r.dead and r.op == "push"
                and r.ops == ["HL"]):
            i += 1
            continue
        idx = [i]
        j = i + 1
        while j < n and len(idx) < 7:
            if rs[j].kind == "nop":
                j += 1
                continue
            if rs[j].kind != "ins" or rs[j].dead:
                break
            idx.append(j)
            j += 1
        if len(idx) < 7:
            i += 1
            continue
        if _LIMIT >= 0 and not _allow():
            i += 1
            continue
        seq = [rs[k] for k in idx]
        sig = [(x.op, tuple(o.lower() for o in x.ops)) for x in seq[:6]]
        if not (sig[0] == ("push", ("hl",)) and sig[1] == ("dec", ("hl",))
                and sig[2][0] == "ld" and len(sig[2][1]) == 2
                and sig[2][1][0].startswith("(") and sig[2][1][1] == "hl"
                and "(" not in sig[2][1][0][1:]
                and not re.search(r"\b(hl|ix|iy|bc|de)\b", sig[2][1][0])
                and sig[3] == ("pop", ("hl",))
                and sig[4] == ("ld", ("a", "h")) and sig[5] == ("or", ("l",))):
            i += 1
            continue
        jmp = seq[6]
        if jmp.flow not in ("branch", "return") or not jmp.rd & 0:
            pass
        if jmp.op not in ("jr", "jp", "ret") or not jmp.ops or \
                jmp.ops[0].lower() not in ("z", "nz"):
            i += 1
            continue
        hl_live = bool(live_out[idx[6]] & (32 | 64))
        store = seq[2].line
        a, o = seq[4].line, seq[5].line
        dec = seq[1].line
        new = [a, o, dec, store]
        if hl_live:
            new.append("\tinc\tHL")
        # place the new text in the records of the old sequence
        slots = idx[:6]
        for k, text in zip(slots, new + [None] * (6 - len(new))):
            if text is None:
                rs[k].dead = True
            else:
                rs[k].line = text
        changed = True
        i = idx[6] + 1
    return changed


def optimize(code: str) -> str:
    """Apply the steps to the text of one run of code."""
    if not ENABLED or _effect is None:
        return code
    lines = code.split("\n")
    try:
        rs = _parse(lines)
    except _Bail:
        return code
    try:
        any_change = False
        for _ in range(4):
            c = _copy_prop(rs)
            d = _dead_writes(rs)
            if d:
                rs = _drop(rs)
            k = _dead_code(rs)
            if k:
                rs = _drop(rs)
            m = _inc_sp(rs)
            if m:
                rs = _drop(rs)
                rs = _parse(_text(rs))
            f = _mem_forward(rs)
            if f:
                rs = _drop(rs)
                rs = _parse(_text(rs))
                m = True
            q = _const_forward(rs)
            if q:
                rs = _drop(rs)
                rs = _parse(_text(rs))
            p = _post_dec(rs)
            if p:
                rs = _drop(rs)
                rs = _parse(_text(rs))
            any_change = any_change or c or d or p or k or m or q
            if not (c or d or p or k or m or q):
                break
    except _Bail:
        return code
    if not any_change:
        return code
    return "\n".join(r.line for r in rs)


def _text(rs: list[_R]) -> list[str]:
    out: list[str] = []
    for r in rs:
        out.extend(r.line.split("\n"))
    return out


def _drop(rs: list[_R]) -> list[_R]:
    return [r for r in rs if not r.dead]
