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
7. A constant offset in two steps.  ``ld HL,4 / add HL,BC / inc HL / inc HL``
   becomes ``ld HL,6 / add HL,BC``, and ``ld DE,4 / add HL,DE / inc HL``
   becomes ``ld DE,5 / add HL,DE`` when DE is dead afterwards.  The carry
   of the add must not be read.
8. The test ``while (n--)`` on a 16-bit value in memory:
   ``push HL / dec HL / ld (X),HL / pop HL / ld A,H / or L / jcc`` becomes
   ``ld A,H / or L / dec HL / ld (X),HL`` (HL holds n-1 afterwards; when
   HL is live after the test, one ``inc HL`` follows).

9. A compare of a register local with a word in memory.  The compiler
   saves the register pair that it needs to load the word, loads the word,
   and restores the pair afterwards::

       push DE / ld DE,(X) / ld A,C / sub E / ld A,B / sbc A,D / pop DE

   When HL is dead after the compare, the word goes to HL instead, and
   push and pop go: ``ld HL,(X) / ld A,C / sub L / ld A,B / sbc A,H``.
   The signed compare of a byte local with a byte in memory
   (``ld HL,X / ld D,A / ld A,(HL) / xor 128 / push DE / ld E,A / ld A,D /
   cp E / pop DE / jr nc,T``) becomes ``ld HL,X / sub (HL) / jp po,N / xor
   128 / N: jp p,T`` when A and D are dead at both ends of the jump.
   ``--no-cmp-mem`` turns this step off (docs/family_l.md).
10. A load of A that A already holds.  After ``ld A,(X)`` or ``ld (X),A``
   the register A holds the byte at X until a write to A, a call, a store
   to memory or a label that some other path reaches with another fact.
   A later ``ld A,(X)`` goes.  The fact crosses a label of the compiler
   (``@name``) when every jump to the label has the same fact.
   ``--no-a-mem`` turns this step off (docs/family_l.md).
12. ``ld HL,X / add HL,BC`` (or DE) goes when HL already holds X plus the
   pair, also when a conditional jump stands between.  ``--no-hl-reuse``.
13. An instruction that writes only A and the flags, such as the ``rlca``
   and ``sbc A,A`` of a sign extension that nothing uses, goes when A is dead
   and no flag is read.  ``--no-dead-alu``.
15. A chain of stores through a base pointer.  After ``ld HL,N / add HL,BC``
   HL holds BC plus N.  A later ``ld HL,N2 / add HL,BC`` with N2 at most three
   away from the value that HL holds now becomes ``inc HL`` (or ``dec HL``)
   in the right number (``--no-hl-reuse``).
16. The word ``(p[0] << 8) | p[1]`` of two bytes, with the pointer in BC,
   becomes ``ld D,(HL) / inc HL / ld E,(HL) / ex DE,HL`` (``--no-be16``).
17. ``ld HL,(X) / ex DE,HL`` becomes ``ld DE,(X)`` when HL is dead
   (``--no-ex-load``).
14. ``inc SP / inc SP`` after a call becomes ``pop HL`` (or DE, BC) when A is
   live and the pair is dead.  ``--no-pop-cleanup``.
11. ``ld A,0`` becomes ``xor A`` when no flag is read before the next
   instruction that sets all flags, a call or a return.  ``--no-zero-xor``
   turns this step off.

The pass turns off with ``--no-flow-opt``.
"""

from __future__ import annotations

import re

try:  # the register effects of one instruction
    from upeepz80.z80 import effect as _effect
except Exception:  # pragma: no cover - the pass is then off
    _effect = None

ENABLED = True
STACK_FUNCS: dict = {}   # label -> bytes that a call of the C function reads
CHAR_L = True       # step 15, --no-char-noext
CMP_MEM = True      # step 9, --no-cmp-mem
# a debug aid that bisects a wrong result: UC80_FLOW_LIMIT=N makes only the
# first N edits of the whole compile
_LIMIT = int(__import__("os").environ.get("UC80_FLOW_LIMIT", "-1"))
_COUNT = [0]
_LAB = [0]


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


_DEBUG = bool(__import__("os").environ.get("UC80_FLOW_DEBUG"))


def _bail(why: str):
    """Leave the run alone (UC80_FLOW_DEBUG=1 says why)."""
    if _DEBUG:
        import sys
        print(f"peep_flow: run left alone: {why}", file=sys.stderr)
    raise _Bail(why)


class _R:
    """One line of the run."""

    __slots__ = ("line", "kind", "op", "ops", "rd", "wr", "flow", "target",
                 "dead", "cond", "fw", "retmask")

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
        self.retmask = _ALL         # the registers that a return reads


def _mask(regs) -> int:
    m = 0
    for r in regs:
        m |= _BIT.get(r, 0)
    return m


def _parse(lines: list[str]) -> list[_R]:
    out = []
    char_l = False      # inside a function that returns a char in L only
    for ln in lines:
        r = _R(ln)
        out.append(r)
        if ln.startswith("; Function "):
            char_l = False
        elif ln.strip() == ";@CHARL":
            char_l = CHAR_L
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
            _bail(f"no effect for {ln.strip()}")
        if eff.size is None:
            _bail(f"unknown instruction {ln.strip()}")
        r.rd = _mask(eff.reads)
        r.wr = _mask(eff.writes)
        if eff.swap_de_hl:
            r.rd |= 8 | 16 | 32 | 64
            r.wr |= 8 | 16 | 32 | 64
        r.flow = eff.flow
        r.target = eff.target
        r.cond = bool(eff.cond)
        if char_l and r.op == "ret" and not r.cond:
            r.retmask = 64
        r.fw = any(w.startswith("f") for w in eff.writes)
        if r.op == "halt":
            r.flow = "next"        # an interrupt resumes at the next line
        if eff.flow == "call":
            r.rd = _ALL
            if (not eff.cond and len(r.ops) == 1
                    and r.ops[0] in STACK_FUNCS):
                r.rd = STACK_FUNCS[r.ops[0]]    # a function of the program
            # a conditional call may not run: it kills nothing
            r.wr = 0 if eff.cond else _ALL
        if r.op in ("exx",):
            _bail("exx")
        if r.op == "ex" and r.ops and r.ops[0].lower() == "af":
            _bail("ex af,af'")
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
                exit_live[i] = STACK_FUNCS.get(r.target, _ALL)
            else:
                succs[i] = [t]
        else:                        # return, stop
            exit_live[i] = r.retmask
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
                    new |= r.retmask
            else:
                new = out
            if out != live_out[i] or new != live_in[i]:
                live_out[i] = out
                live_in[i] = new
                changed = True
    return live_out, live_in


def _is_reg8(o: str) -> bool:
    return o.lower() in _BIT


_WORD = re.compile(r"[@A-Za-z_?$.][A-Za-z0-9_@?$.]*")
GLOBAL_COPY = True      # copies cross labels (step 1b), --no-cmp-mem


def _cp_labels(rs: list[_R]) -> set:
    """The compiler labels (``@name``) whose every use in the run is the
    target of a jump or branch of the run: the code reaches them only by
    these jumps and by falling through."""
    labs = {r.op for r in rs if r.kind == "label" and r.op.startswith("@")}
    if not labs:
        return labs
    bad = set()
    for r in rs:
        if r.kind == "label":
            continue
        text = r.line.split(";", 1)[0]
        toks = set(_WORD.findall(text))
        if r.kind == "ins" and r.flow in ("jump", "branch") and r.target:
            toks.discard(r.target)
        bad |= toks & labs
    return labs - bad


def _meet(a: dict, b: dict) -> dict:
    return {k: v for k, v in a.items() if b.get(k) == v}


def _cp_walk(rs, eligible, edge, out_edge, rewrite):
    """One forward walk of the copy facts (``cp[d] = s``: register d holds
    what register s holds).  ``edge`` holds the facts of the jumps into a
    label, as the last walk found them (a label with no entry has no jump
    that was reached yet).  The walk puts the facts at each jump into
    ``out_edge``.  Returns True when ``rewrite`` and a line changed."""
    changed = False
    cp: dict[str, str] = {}
    reach = True
    stk: list = []          # (pair, facts at the push, bytes written since)
    for r in rs:
        if r.kind == "label":
            stk = []
            if eligible is not None and r.op in eligible:
                st = None
                if reach:
                    st = dict(cp)
                e = edge.get(r.op)
                if e is not None:
                    st = dict(e) if st is None else _meet(st, e)
                cp = st if st is not None else {}
            else:
                cp = {}
            reach = True
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
        if rewrite and new_ops is not None and new_ops != ops and _allow():
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
        # the stack of saved pairs
        restore = None
        if r.op in ("push", "pop") and len(ops) == 1 \
                and ops[0].lower() in ("bc", "de", "hl") \
                and GLOBAL_COPY:
            pr = ops[0].lower()
            if r.op == "push":
                for ent in stk:
                    ent[2] |= r.wr & ~_mask(_PAIRS[ent[0]])
                stk.append([pr, dict(cp), 0])
            elif stk and stk[-1][0] == pr:
                ent = stk.pop()
                restore = ent
                for o in stk:
                    o[2] |= r.wr & ~_mask(_PAIRS[o[0]])
            else:
                stk = []
        else:
            if r.op in ("push", "pop") or any(
                    o.lower() == "sp" for o in ops):
                stk = []
            else:
                for ent in stk:
                    ent[2] |= r.wr & ~_mask(_PAIRS[ent[0]])
        # invalidate by the writes
        if r.wr:
            for k in list(cp):
                if _BIT[k] & r.wr or _BIT[cp[k]] & r.wr:
                    del cp[k]
        if restore is not None:
            _, snap, w = restore
            for d, sr in snap.items():
                if not ((_BIT[d] | _BIT[sr]) & w):
                    cp[d] = sr
        if r.flow in ("jump", "branch") and r.target and out_edge is not None:
            prev = out_edge.get(r.target)
            out_edge[r.target] = dict(cp) if prev is None \
                else _meet(prev, cp)
        if r.flow in ("call", "jump", "return", "stop"):
            cp.clear()
            if r.flow in ("jump", "return", "stop") and not r.cond:
                reach = False
                stk = []
            continue
        if (r.op == "ld" and len(r.ops) == 2 and _is_reg8(r.ops[0])
                and _is_reg8(r.ops[1])):
            d, s = r.ops[0].lower(), r.ops[1].lower()
            if d != s:
                cp[d] = s
    return changed


def _copy_prop(rs: list[_R]) -> bool:
    """Step 1 and 1b."""
    eligible = _cp_labels(rs) if GLOBAL_COPY else None
    edge: dict = {}
    if eligible:
        for _ in range(8):
            out: dict = {}
            _cp_walk(rs, eligible, edge, out, False)
            if out == edge:
                break
            edge = out
        else:
            eligible = None
    else:
        eligible = None
    return _cp_walk(rs, eligible, edge, None, True)


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


def _loop_labels(rs: list[_R]) -> set[str]:
    """Labels ``@FORB...`` that only backward jumps and the line before
    reach, and that the run names nowhere else (step 5, loop entry)."""
    pos = {r.op: i for i, r in enumerate(rs) if r.kind == "label"
           and r.op.startswith("@FORB")}
    if not pos:
        return set()
    bad: set[str] = set()
    for i, r in enumerate(rs):
        if r.kind == "label":
            continue
        for name in pos:
            if name in r.line:
                if (r.kind == "ins" and r.flow in ("jump", "branch")
                        and r.target == name and i > pos[name]
                        and r.op in ("jr", "jp", "djnz")):
                    continue
                bad.add(name)
    return set(pos) - bad


def _mem_forward(rs: list[_R]) -> bool:
    """Step 5.  A loop entry label (``@FORB``) keeps the facts that the
    line before it and every jump to it agree on."""
    loops = _loop_labels(rs)
    if not loops:
        return _mem_walk(rs, True, {}, None)[0]
    allowed: dict[str, object] = {name: None for name in loops}
    for _ in range(8):
        entry: dict = {}
        jf: dict = {name: [] for name in loops}
        _mem_walk(rs, False, allowed, (entry, jf))
        changed = False
        for name in loops:
            e = dict(entry.get(name) or {})
            for f in jf[name]:
                e = {k: v for k, v in e.items() if f.get(k) == v}
            if allowed[name] != e:
                allowed[name] = e
                changed = True
        if not changed:
            break
    else:
        allowed = {name: {} for name in loops}
    return _mem_walk(rs, True, allowed, None)[0]


def _mem_walk(rs: list[_R], apply: bool, allowed: dict, rec):
    """One walk of step 5.  ``apply`` False only follows the facts.
    ``allowed`` maps a loop label to the facts that it keeps (None: the
    facts of the line before).  ``rec`` collects (entry facts, facts at
    each jump to a loop label)."""
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
            if r.op in allowed:
                keep = allowed[r.op]
                if keep is None:
                    keep = dict(facts)
                if rec is not None:
                    rec[0][r.op] = dict(facts)
                facts.clear()
                facts.update(keep)
            else:
                facts.clear()
            continue
        if r.kind != "ins" or r.dead:
            continue
        if (rec is not None and r.target in allowed and r.target
                and r.flow in ("jump", "branch")):
            rec[1][r.target].append(dict(facts))
        ops = [o.strip() for o in r.ops]
        low = [o.lower() for o in ops]
        # a load of a pair from a label
        if (r.op == "ld" and len(ops) == 2 and low[0] in _PAIR_NAMES
                and _label_addr(ops[1]) is not None):
            sym, off = _label_addr(ops[1])
            held = facts.get((sym, off))
            if held == low[0]:
                if not apply:
                    continue
                if _allow():
                    r.dead = True
                    changed = True
                    continue
            elif held is not None and not apply:
                drop_pair_bytes(_mask(_PAIRS[low[0]]))
                facts[(sym, off)] = low[0]
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
                wide = len(low) > 1 and low[1] in ("hl", "de", "bc", "sp",
                                                   "ix", "iy")
                drop_overlap(tgt[0], tgt[1], 2 if wide else 1)
            continue
        if r.wr:
            drop_pair_bytes(r.wr)
        if r.flow in ("jump", "return", "stop"):
            facts.clear()
    return changed, facts


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


_CARRY_READERS = frozenset(("adc", "sbc", "rl", "rr", "rla", "rra", "ccf",
                            "daa"))
# instructions that set the carry flag and do not read it
_CARRY_WRITERS = frozenset(("add", "sub", "and", "or", "xor", "cp", "neg",
                            "rlca", "rrca", "rlc", "rrc", "sla", "sra",
                            "srl", "scf"))


def _carry_dead_after(rs: list[_R], start: int) -> bool:
    """The carry flag (and the half carry) is not read before an
    instruction sets it again, on the straight line from record ``start``.
    A conditional jump or return ends the search with "no"."""
    steps = 0
    for r in rs[start:]:
        if r.kind != "ins":
            continue
        steps += 1
        if steps > 14:
            return False
        if r.op in _CARRY_READERS:
            return False
        if r.flow == "call":
            return True
        if r.flow == "branch" or (r.flow == "return" and r.cond):
            return False
        if r.flow in ("jump", "return", "stop"):
            return True
        if r.op in ("push", "ex") and r.ops and r.ops[0].lower() == "af":
            return False
        if r.op in _CARRY_WRITERS:
            return True
    return False


def _fold_offsets(rs: list[_R]) -> bool:
    """Step 7."""
    live_out, _ = _liveness(rs)
    n = len(rs)
    changed = False
    i = 0
    while i < n:
        r = rs[i]
        if not (r.kind == "ins" and not r.dead and r.op == "ld"
                and len(r.ops) == 2 and r.ops[0].lower() in _PAIR_NAMES):
            i += 1
            continue
        v = _int_of(r.ops[1])
        if v is None:
            i += 1
            continue
        j = i + 1
        while j < n and rs[j].kind == "nop":
            j += 1
        if j >= n or rs[j].kind != "ins" or rs[j].dead:
            i += 1
            continue
        a = rs[j]
        x = r.ops[0].lower()
        src = a.ops[1].lower() if len(a.ops) == 2 else ""
        if not (a.op == "add" and len(a.ops) == 2 and a.ops[0].lower() == "hl"
                and src in ("bc", "de") and (x == "hl" or x == src)):
            i += 1
            continue
        run = []
        k = j + 1
        net = 0
        while k < n:
            q = rs[k]
            if q.kind == "nop":
                k += 1
                continue
            if (q.kind == "ins" and not q.dead and q.op in ("inc", "dec")
                    and [o.lower() for o in q.ops] == ["hl"]):
                run.append(k)
                net += 1 if q.op == "inc" else -1
                k += 1
                continue
            break
        if not run:
            i += 1
            continue
        last = run[-1]
        ok = _carry_dead_after(rs, last + 1)
        if ok and x != "hl":
            ok = not (live_out[last] & _mask(_PAIRS[x]))
        if not ok or not _allow():
            i += 1
            continue
        nv = (v + net) & 0xFFFF
        r.ops = [r.ops[0], str(nv)]
        r.line = f"\tld\t{r.ops[0]},{nv}"
        for q in run:
            rs[q].dead = True
        changed = True
        i = last + 1
    return changed


def _ins_after(rs: list[_R], i: int, count: int):
    """The indexes of the ``count`` instructions that follow record i with
    no label, comment-only line or other record between, or None."""
    out = []
    j = i
    while len(out) < count:
        j += 1
        if j >= len(rs):
            return None
        r = rs[j]
        if r.kind == "nop" and (not r.line.strip()):
            continue
        if r.kind != "ins" or r.dead:
            return None
        out.append(j)
    return out


def _lc(r: _R) -> tuple:
    return (r.op, tuple(o.lower() for o in r.ops))


_MEM_WORD = re.compile(r"^\([A-Za-z_@?$.][A-Za-z0-9_@?$.]*([+-]\d+)?\)$")


BIAS_CMP = True     # step 9b, --no-cmp-mem
_SCRATCH = (("h", "l"), ("d", "e"))


def _bias_cmp(rs: list[_R], i: int, live_out) -> bool:
    """The signed byte compare of two operands through the bias 128 (see
    _cmp_mem).  Returns True after one rewrite."""
    for count in (9, 7):
        k = _ins_after(rs, i, count - 1)
        if k is None:
            continue
        recs = [rs[i]] + [rs[x] for x in k]
        idx = [i] + k
        a = [_lc(r) for r in recs]
        if a[1] != ("xor", ("128",)) or a[4] != ("xor", ("128",)):
            continue
        if not (a[3][0] == "ld" and a[3][1][0] == "a"):
            continue
        p_src = recs[0].ops[1]
        q_src = recs[3].ops[1]
        if a[2][0] != "ld" or a[2][1][1] != "a" or a[2][1][0] not in "hd" \
                or len(a[2][1][0]) != 1:
            continue
        t1 = a[2][1][0]
        t2 = {"h": "l", "d": "e"}[t1]
        if count == 7:
            if not (a[5] == ("cp", (t1,)) and a[6][0] in ("jr", "jp")
                    and len(a[6][1]) == 2 and a[6][1][0] in ("c", "nc")):
                continue
            lhs, rhs = q_src, p_src
            jmp = idx[6]
        else:
            if not (a[5] == ("ld", (t2, "a")) and a[6] == ("ld", ("a", t1))
                    and a[7] == ("cp", (t2,)) and a[8][0] in ("jr", "jp")
                    and len(a[8][1]) == 2 and a[8][1][0] in ("c", "nc")):
                continue
            lhs, rhs = p_src, q_src
            jmp = idx[8]
        # operands: P, Q are plain loads (a register, (IX+d), (sym), (HL))
        def okld(o):
            ol = o.lower()
            return (ol in _BIT or ol == "(hl)" or ol.startswith("(ix")
                    or _sym_mem(o) is not None)
        if not (okld(lhs) and okld(rhs)):
            continue
        # a scratch register that the compare names must not be a source
        if any(x.lower() in (t1, t2) for x in (lhs, rhs)):
            continue
        rl = rhs.lower()
        pre = []
        if rl in _BIT:
            operand = rhs
        elif rl == "(hl)" or rl.startswith("(ix"):
            operand = rhs
        else:
            # (sym): the address goes to HL, which must be dead after
            if live_out[jmp] & 96 or lhs.lower() in ("h", "l"):
                continue
            pre = [f"\tld\tHL,{rhs.strip()[1:-1]}"]
            operand = "(HL)"
        if live_out[jmp] & 1 or not _allow():
            continue
        _LAB[0] += 1
        lab = f"@CMP_NV{900000 + _LAB[0]}"
        cond = "M" if a[-1][1][0] == "c" else "P"
        t = recs[-1].ops[1]
        lines = [f"\tld\tA,{lhs}"] + pre + [
            f"\tsub\t{operand}", f"\tjp\tPO,{lab}", "\txor\t128",
            f"{lab}:", f"\tjp\t{cond},{t}"]
        # replace the records: the first gets all the new text, the others go
        recs[0].line = "\n".join(lines)
        for r in recs[1:]:
            r.dead = True
        return True
    return False


def _cmp_mem(rs: list[_R]) -> bool:
    """Step 9."""
    live_out, _ = _liveness(rs)
    n = len(rs)
    changed = False
    for i in range(n):
        r = rs[i]
        if r.kind != "ins" or r.dead:
            continue
        # 16 bits: push P / ld P,(X) / ld A,r1 / sub Pl / ld A,r2 /
        # sbc A,Ph / pop P
        if r.op == "push" and len(r.ops) == 1 and r.ops[0].lower() in (
                "de", "bc"):
            pr = r.ops[0].lower()
            ph, pl = _PAIRS[pr]
            k = _ins_after(rs, i, 6)
            if k is None:
                continue
            a = [_lc(rs[x]) for x in k]
            x_op = rs[k[0]].ops[1] if len(rs[k[0]].ops) == 2 else ""
            if not (a[0][0] == "ld" and a[0][1][0] == pr
                    and _MEM_WORD.match(x_op)
                    and a[1][0] == "ld" and a[1][1][0] == "a"
                    and a[1][1][1] in _BIT
                    and a[2] == ("sub", (pl,))
                    and a[3][0] == "ld" and a[3][1][0] == "a"
                    and a[3][1][1] in _BIT
                    and a[4] == ("sbc", ("a", ph))
                    and a[5] == ("pop", (pr,))):
                continue
            # a compare operand in H or L is a copy of the pair, made just
            # before the push: ld H,D / ld L,E
            cpy = {}
            back = []
            q = i - 1
            while q >= 0 and len(back) < 2:
                if rs[q].kind == "ins" and not rs[q].dead:
                    back.append(q)
                elif rs[q].kind != "nop":
                    break
                q -= 1
            for q in back:
                w = _lc(rs[q])
                if (w[0] == "ld" and len(w[1]) == 2 and w[1][0] in ("h", "l")
                        and w[1][1] in (ph, pl)):
                    cpy[w[1][0]] = w[1][1]
            regs = []
            for x in (1, 3):
                v = a[x][1][1]
                if v in ("h", "l"):
                    v = cpy.get(v)
                elif v in (ph, pl):
                    v = None
                if v is None or v in ("h", "l"):
                    break
                regs.append(v)
            if len(regs) != 2:
                continue
            if live_out[k[5]] & (32 | 64) or not _allow():
                continue
            rs[k[0]].ops = ["HL", x_op]
            rs[k[0]].line = f"\tld\tHL,{x_op}"
            rs[k[1]].ops = ["A", regs[0].upper()]
            rs[k[1]].line = f"\tld\tA,{regs[0].upper()}"
            rs[k[3]].ops = ["A", regs[1].upper()]
            rs[k[3]].line = f"\tld\tA,{regs[1].upper()}"
            rs[k[2]].ops = ["L"]
            rs[k[2]].line = "\tsub\tL"
            rs[k[4]].ops = ["A", "H"]
            rs[k[4]].line = "\tsbc\tA,H"
            r.dead = True
            rs[k[5]].dead = True
            changed = True
            continue
        # signed byte compare through the bias 128 (two forms):
        #   ld A,P / xor 128 / ld H,A / ld A,Q / xor 128 / cp H / jcc
        #   ld A,P / xor 128 / ld H,A / ld A,Q / xor 128 / ld L,A /
        #   ld A,H / cp L / jcc
        # becomes ld A,lhs / sub rhs / jp po,N / xor 128 / N: jp m|p,T
        if r.op == "ld" and len(r.ops) == 2 and r.ops[0].lower() == "a" \
                and BIAS_CMP:
            if _bias_cmp(rs, i, live_out):
                changed = True
                continue
        # signed byte against a byte in memory:
        # ld A,r / xor 128 / ld HL,X / ld D,A / ld A,(HL) / xor 128 /
        # push DE / ld E,A / ld A,D / cp E / pop DE / jr nc|c,T
        if r.op == "ld" and len(r.ops) == 2 and r.ops[0].lower() == "a" \
                and r.ops[1].lower() in _BIT:
            k = _ins_after(rs, i, 11)
            if k is None:
                continue
            a = [_lc(rs[x]) for x in k]
            if not (a[0] == ("xor", ("128",))
                    and a[1][0] == "ld" and a[1][1][0] == "hl"
                    and a[2] == ("ld", ("d", "a"))
                    and a[3] == ("ld", ("a", "(hl)"))
                    and a[4] == ("xor", ("128",))
                    and a[5] == ("push", ("de",))
                    and a[6] == ("ld", ("e", "a"))
                    and a[7] == ("ld", ("a", "d"))
                    and a[8] == ("cp", ("e",))
                    and a[9] == ("pop", ("de",))
                    and a[10][0] in ("jr", "jp")
                    and len(a[10][1]) == 2 and a[10][1][0] in ("c", "nc")):
                continue
            if rs[i].ops[1].lower() in ("h", "l"):
                continue
            jr = rs[k[10]]
            t = jr.ops[1]
            # A is dead where the jump goes and where it falls (D and E
            # keep their values, HL keeps the address)
            if live_out[k[10]] & 1 or not _allow():
                continue
            _LAB[0] += 1
            lab = f"@CMP_NV{900000 + _LAB[0]}"
            cond = "M" if a[10][1][0] == "c" else "P"
            hl_line = rs[k[1]].line
            rs[k[0]].line = hl_line
            rs[k[1]].line = "\tsub\t(HL)"
            rs[k[2]].line = f"\tjp\tPO,{lab}"
            rs[k[3]].line = "\txor\t128"
            rs[k[4]].line = f"{lab}:"
            for x in k[5:10]:
                rs[x].dead = True
            jr.line = f"\tjp\t{cond},{t}"
            changed = True
    return changed


_SYM_MEM = re.compile(
    r"^\(\s*([A-Za-z_?@$.][A-Za-z0-9_@?$.]*)(\s*[+-]\s*\d+)?\s*\)$")
_NOT_SYM = {"hl", "bc", "de", "ix", "iy", "sp", "af"}
_MEM_RMW = ("ld", "inc", "dec", "res", "set", "rl", "rr", "rlc", "rrc", "sla",
            "sra", "srl", "sll", "sl1")
_MEM_WRITERS = ("ldi", "ldir", "ldd", "lddr", "ini", "inir", "ind", "indr")
POP_CLEAN = True    # step 4 with a pair, --no-pop-cleanup
A_MEM = True        # step 10, --no-a-mem


def _sym_mem(op: str):
    """The text of ``(sym+n)`` without spaces, or None."""
    m = _SYM_MEM.match(op.strip())
    if not m or m.group(1).lower() in _NOT_SYM:
        return None
    return "(" + m.group(1) + (m.group(2) or "").replace(" ", "") + ")"


def _amem_walk(rs, eligible, edge, out_edge, rewrite):
    """Forward walk of the fact "A holds the byte at (X)".  Returns True
    when ``rewrite`` and a load went."""
    changed = False
    st = None
    reach = True
    for r in rs:
        if r.kind == "label":
            if eligible is not None and r.op in eligible:
                e = edge.get(r.op, False)
                if e is False:
                    new = st if reach else None
                elif not reach:
                    new = e
                else:
                    new = e if e == st else None
                st = new
            else:
                st = None
            reach = True
            continue
        if r.kind != "ins":
            continue
        op = r.op
        ops = r.ops
        mem = _sym_mem(ops[1]) if (op == "ld" and len(ops) == 2
                                   and ops[0].lower() == "a") else None
        if mem is not None:
            if st == mem and rewrite and _allow():
                r.dead = True
                changed = True
            st = mem
            continue
        if op == "ld" and len(ops) == 2 and ops[1].lower() == "a" \
                and _sym_mem(ops[0]) is not None:
            st = _sym_mem(ops[0])
            continue
        if r.wr & 1:
            st = None
        if ((op in _MEM_RMW and ops and ops[0].startswith("("))
                or op in _MEM_WRITERS
                or (op == "ex" and ops and ops[0].lower() == "(sp)")):
            st = None
        if r.flow in ("jump", "branch") and r.target and out_edge is not None:
            prev = out_edge.get(r.target, False)
            out_edge[r.target] = st if prev is False else (
                st if prev == st else None)
        if r.flow in ("call",):
            st = None
        elif r.flow in ("jump", "return", "stop"):
            if not r.cond:
                reach = False
            st = None
    return changed


def _a_mem(rs: list[_R]) -> bool:
    """Step 10."""
    eligible = _cp_labels(rs)
    edge: dict = {}
    if eligible:
        for _ in range(8):
            out: dict = {}
            _amem_walk(rs, eligible, edge, out, False)
            if out == edge:
                break
            edge = out
        else:
            eligible = None
    else:
        eligible = None
    return _amem_walk(rs, eligible, edge or {}, None, True)


ZERO_A = True       # step 11, --no-zero-xor
_FLAG_KILL = ("cp", "and", "or", "xor", "sub", "neg")


def _flags_dead_after(rs: list[_R], start: int) -> bool:
    """No flag is read before an instruction sets all of them, on the
    straight line from record ``start`` (a call or a plain return also ends
    the search with "dead": the flags after a call are never used)."""
    steps = 0
    for r in rs[start:]:
        if r.kind != "ins":
            continue
        steps += 1
        if steps > 30:
            return False
        if r.op in _FLAG_KILL or (r.op == "add" and r.ops
                                  and r.ops[0].lower() == "a"):
            return True
        if r.flow == "call":
            return not r.cond
        if r.flow == "return":
            return not r.cond
        if r.flow == "jump" and not r.cond and r.target in STACK_FUNCS:
            return True         # a tail jump to a C function
        if r.flow in ("jump", "branch", "stop") or r.cond:
            return False
        if r.op in ("push", "pop", "ex") and r.ops and r.ops[0].lower() in (
                "af", "af'"):
            return False
        if r.fw or r.op in ("rla", "rra", "rlca",
                            "rrca", "cpl", "ccf", "scf", "daa", "adc",
                            "sbc", "djnz", "ldi", "ldir", "ldd", "lddr",
                            "halt", "di", "ei", "im", "reti", "retn"):
            return False
    return False


def _zero_a(rs: list[_R]) -> bool:
    """Step 11: ``ld A,0`` becomes ``xor A`` (one byte less) when no flag
    is read afterwards."""
    changed = False
    for i, r in enumerate(rs):
        if (r.kind == "ins" and not r.dead and r.op == "ld"
                and [o.lower() for o in r.ops] == ["a", "0"]
                and _flags_dead_after(rs, i + 1) and _allow()):
            r.op = "xor"
            r.ops = ["A"]
            r.line = "\txor\tA"
            changed = True
    return changed


HL_SUM = True       # step 12, --no-hl-reuse
DEAD_ALU = True     # step 13, --no-dead-alu


def _split_base(x: str):
    """``X`` of ``ld HL,X`` as (symbol or None, offset), or None."""
    v = _int_of(x)
    if v is not None:
        return (None, v)
    m = re.match(r"^([A-Za-z_@?$.][A-Za-z0-9_@?$.]*)\s*([+-]\s*\d+)?$", x)
    if not m or m.group(1).lower() in _PAIRS or m.group(1).lower() in _BIT:
        return None
    return (m.group(1), int((m.group(2) or "0").replace(" ", "")))


def _hl_sum(rs: list[_R]) -> bool:
    """Step 12: ``ld HL,X / add HL,BC`` (or DE) when HL already holds X
    plus that pair, or a few bytes away from it.  HL holds ``base + pair +
    k``; ``inc HL`` and ``dec HL`` change k; a conditional jump does not
    change HL on the way that falls through; a label, a call, a jump and a
    write to HL or to the pair end the fact.  The new add becomes at most
    three ``inc HL`` or ``dec HL`` (a chain of stores through a base
    pointer, step L7).  The carry of the add must be dead."""
    changed = False
    state = None            # (base, pair, k)
    n = len(rs)
    i = 0
    while i < n:
        r = rs[i]
        if r.kind == "label":
            state = None
            i += 1
            continue
        if r.kind != "ins" or r.dead:
            i += 1
            continue
        if (r.op == "ld" and len(r.ops) == 2 and r.ops[0].lower() == "hl"
                and r.ops[1].lower() not in _PAIRS
                and not r.ops[1].startswith("(")):
            k = _ins_after(rs, i, 1)
            sb = _split_base(r.ops[1])
            if k is not None and sb is not None:
                a = rs[k[0]]
                if (a.op == "add" and [o.lower() for o in a.ops] in (
                        ["hl", "bc"], ["hl", "de"])):
                    pair = a.ops[1].lower()
                    if (state is not None and state[0] == sb[0]
                            and state[1] == pair
                            and abs(sb[1] - state[2]) <= 3
                            and _carry_dead_after(rs, k[0] + 1)
                            and _allow()):
                        delta = sb[1] - state[2]
                        if delta == 0:
                            r.dead = True
                            a.dead = True
                        else:
                            op = "inc" if delta > 0 else "dec"
                            r.line = "\n".join(
                                [f"\t{op}\tHL"] * abs(delta))
                            r.op = ""
                            r.kind = "nop"
                            a.dead = True
                        changed = True
                        state = (sb[0], pair, sb[1])
                    else:
                        state = (sb[0], pair, sb[1])
                    i = k[0] + 1
                    continue
        if state is not None:
            if (r.op in ("inc", "dec") and [o.lower() for o in r.ops]
                    == ["hl"]):
                state = (state[0], state[1],
                         state[2] + (1 if r.op == "inc" else -1))
            else:
                m = 96 | _mask(_PAIRS[state[1]])
                if r.wr & m or r.flow in ("call", "jump", "return", "stop"):
                    state = None
        i += 1
    return changed


_BE16_TAIL = [("ld", ("l", "(hl)")), ("ld", ("h", "l")), ("ld", ("l", "0")),
              ("push", ("hl",)), ("ld", ("l", "c")), ("ld", ("h", "b")),
              ("inc", ("hl",)), ("ld", ("l", "(hl)")), ("ld", ("h", "0")),
              ("pop", ("de",)), ("ld", ("a", "h")), ("or", ("d",)),
              ("ld", ("h", "a")), ("ld", ("a", "l")), ("or", ("e",)),
              ("ld", ("l", "a"))]
BE16 = True         # step 16, --no-be16
EX_LOAD = True      # step 17, --no-ex-load


def _be16(rs: list[_R]) -> bool:
    """Step 16: the word ``(p[0] << 8) | p[1]`` of two bytes, with the
    pointer in BC and in HL, becomes ``ld D,(HL) / inc HL / ld E,(HL) / ex
    DE,HL``.  A and DE hold other values afterwards, so they must be dead,
    and so must the flags."""
    live_out, _ = _liveness(rs)
    changed = False
    for i, r in enumerate(rs):
        if (r.kind != "ins" or r.dead or r.op != "ld"
                or [o.lower() for o in r.ops] != ["l", "(hl)"]):
            continue
        k = _ins_after(rs, i, len(_BE16_TAIL) - 1)
        if k is None:
            continue
        idx = [i] + k
        got = [_lc(rs[x]) for x in idx]
        if got[4:6] == [("ld", ("h", "b")), ("ld", ("l", "c"))]:
            got[4:6] = got[5], got[4]       # the copy in the other order
        if got != _BE16_TAIL:
            continue
        # HL must be a copy of BC before the block
        back = []
        q = i - 1
        while q >= 0 and len(back) < 2:
            if rs[q].kind == "ins" and not rs[q].dead:
                back.append(q)
            elif rs[q].kind != "nop":
                break
            q -= 1
        have = {_lc(rs[q]) for q in back}
        if not ({("ld", ("l", "c")), ("ld", ("h", "b"))} <= have
                or {("ld", ("c", "l")), ("ld", ("b", "h"))} <= have):
            continue
        last = idx[-1]
        if live_out[last] & (1 | 8 | 16) or not _flags_dead_after(
                rs, last + 1) or not _allow():
            continue
        lines = ["\tld\tD,(HL)", "\tinc\tHL", "\tld\tE,(HL)",
                 "\tex\tDE,HL"]
        rs[idx[0]].line = "\n".join(lines)
        for x in idx[1:]:
            rs[x].dead = True
        changed = True
    return changed


def _ex_load(rs: list[_R]) -> bool:
    """Step 17: ``ld HL,(X) / ex DE,HL`` becomes ``ld DE,(X)`` when HL is
    dead afterwards."""
    live_out, _ = _liveness(rs)
    changed = False
    for i, r in enumerate(rs):
        if (r.kind != "ins" or r.dead or r.op != "ld" or len(r.ops) != 2
                or r.ops[0].lower() != "hl"
                or not _MEM_WORD.match(r.ops[1])):
            continue
        k = _ins_after(rs, i, 1)
        if k is None:
            continue
        a = rs[k[0]]
        if (a.op != "ex" or [o.lower() for o in a.ops] != ["de", "hl"]
                or live_out[k[0]] & 96 or not _allow()):
            continue
        r.ops = ["DE", r.ops[1]]
        r.line = f"\tld\tDE,{r.ops[1]}"
        a.dead = True
        changed = True
    return changed


_ALU_A_ONLY = frozenset(("rlca", "rrca", "rla", "rra", "cpl", "neg"))


def _dead_alu(rs: list[_R]) -> bool:
    """Step 13: an instruction that writes only A and the flags, when A is
    dead afterwards and no flag is read before the next instruction that
    sets all flags (``rlca`` or ``sbc A,A`` of a sign extension that nobody
    uses)."""
    live_out, _ = _liveness(rs)
    changed = False
    for i, r in enumerate(rs):
        if r.kind != "ins" or r.dead or r.flow != "next":
            continue
        ops = [o.lower() for o in r.ops]
        pure = (r.op in _ALU_A_ONLY and not ops) \
            or (r.op in ("sbc", "adc") and ops == ["a", "a"]) \
            or (r.op in ("add", "sub", "and", "or", "xor", "inc", "dec")
                and ops[:1] == ["a"] and len(ops) <= 2
                and (len(ops) == 1 or _IMM.match(r.ops[1])
                     or ops[1] in _BIT)) \
            or (r.op in ("inc", "dec") and ops == ["a"])
        if not pure or live_out[i] & 1:
            continue
        if r.op in ("sbc", "adc", "rla", "rra"):
            pass
        if _flags_dead_after(rs, i + 1) and _allow():
            r.dead = True
            changed = True
    return changed


def _inc_sp(rs: list[_R]) -> bool:
    """Step 4."""
    live_out, _ = _liveness(rs)
    n = len(rs)
    changed = False
    i = 0
    while i < n:
        r = rs[i]
        if not (r.kind == "ins" and r.flow == "call" and not r.dead
                and not r.cond):
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
        # the register that the pops can fill: A (pop AF), else a pair
        # that is dead afterwards (pop HL, DE or BC; --no-pop-cleanup)
        reg = None
        if pairs:
            lv = live_out[run[2 * pairs - 1]]
            if not lv & 1:
                reg = "AF"
            elif POP_CLEAN:
                for nm, bits in (("HL", 96), ("DE", 24), ("BC", 6)):
                    if not lv & bits:
                        reg = nm
                        break
        if reg is not None and _allow():
            for k in range(pairs):
                a, b = run[2 * k], run[2 * k + 1]
                rs[a].line = f"\tpop\t{reg}"
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
    """Apply the steps to the text of one run of code.  A run with exx
    (the names of the registers change meaning) is split at the labels of
    functions, and each part without exx is done alone."""
    if not ENABLED or _effect is None:
        return code
    lines = code.split("\n")
    try:
        res = _optimize_lines(lines)
        return code if res == code else res
    except _Bail as e:
        if not e.args or e.args[0] not in ("exx", "ex af,af'"):
            return code
    # split at the labels that are not local labels of the compiler
    parts: list[list[str]] = [[]]
    for ln in lines:
        if (ln and ln[0] not in " \t;" and _LABEL.match(ln)
                and ln[0] not in "@?" and parts[-1]):
            parts.append([])
        parts[-1].append(ln)
    out: list[str] = []
    for part in parts:
        try:
            out.append(_optimize_lines(part))
        except _Bail:
            out.append("\n".join(part))
    res = "\n".join(out)
    return code if res == code else res


def _optimize_lines(lines: list[str]) -> str:
    rs = _parse(lines)
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
        o = _fold_offsets(rs)
        if o:
            rs = _drop(rs)
            rs = _parse(_text(rs))
            q = True
        p = _post_dec(rs)
        if p:
            rs = _drop(rs)
            rs = _parse(_text(rs))
        v = False
        if CMP_MEM:
            v = _cmp_mem(rs)
            if v:
                rs = _drop(rs)
                rs = _parse(_text(rs))
        if HL_SUM and _hl_sum(rs):
            v = True
            rs = _drop(rs)
            rs = _parse(_text(rs))
        if BE16 and _be16(rs):
            v = True
            rs = _drop(rs)
            rs = _parse(_text(rs))
        if EX_LOAD and _ex_load(rs):
            v = True
            rs = _drop(rs)
            rs = _parse(_text(rs))
        if DEAD_ALU and _dead_alu(rs):
            v = True
            rs = _drop(rs)
            rs = _parse(_text(rs))
        if ZERO_A and _zero_a(rs):
            v = True
            rs = _parse(_text(rs))
        if A_MEM:
            if _a_mem(rs):
                v = True
                rs = _drop(rs)
                rs = _parse(_text(rs))
        any_change = any_change or c or d or p or k or m or q or v
        if not (c or d or p or k or m or q or v):
            break
    if not any_change:
        return "\n".join(lines)
    return "\n".join(r.line for r in rs)


def _text(rs: list[_R]) -> list[str]:
    out: list[str] = []
    for r in rs:
        out.extend(r.line.split("\n"))
    return out


def _drop(rs: list[_R]) -> list[_R]:
    return [r for r in rs if not r.dead]
