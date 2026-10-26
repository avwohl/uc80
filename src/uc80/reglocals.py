"""Scalar locals that live in BC or DE.  See docs/register_locals.md.

The code generator keeps every named local in memory: a static ``??AUTO``
slot or an IX frame slot.  This module works on the assembly text of one
function, after the body is generated, and moves up to two scalar locals
(a char, an int, a pointer) into BC or DE for the whole function.

The module is a text pass.  It needs from the code generator the slots
(``Var``) and a model of what each call reads (``Env``).  It finds
everything else in the text:

1. Each access to a slot byte becomes an access to a register byte
   (``ld A,(IX-4)`` becomes ``ld A,C``).  A text that names a slot in a
   form the pass does not know keeps the local in memory.
2. Other code of the function also uses BC and DE as scratch registers
   (``ld BC,_arr`` / ``add HL,BC``), and every call destroys them.  The
   pass finds each instruction that touches the register of a local (a
   "foreign" instruction).  A run of foreign instructions that works on
   one scratch value moves to the other pair when that pair is free
   (rename).  Otherwise the run gets ``push rr`` before it and ``pop rr``
   after it (wrap).
3. A checker proves, for the finished text, that every read of the local
   sees a write of the local and every foreign read sees a foreign write.
   A configuration that fails the check is dropped.

``prune`` runs on the whole program after the peephole pass.  It removes
a wrap that protects nothing, because the routines in the wrap never
write the register.  The wraps carry the comment ``; RL<n>``.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Callable, Optional

try:  # the register effects of one instruction
    from upeepz80.z80 import effect as _effect, split_operands as _split_ops
    from upeepz80.z80 import strip_comment as _strip_comment
except Exception:  # pragma: no cover - the pass is then off
    _effect = None

B, C, D, E = 1, 2, 4, 8
H, L, A = 16, 32, 64
BYTE_BIT = {"b": B, "c": C, "d": D, "e": E, "h": H, "l": L, "a": A}
ALL4 = B | C | D | E
ALL7 = ALL4 | H | L | A
RET_LIVE = D | E | H | L | A
PAIR_MASK = {"BC": B | C, "DE": D | E}
LO_NAME = {"BC": "C", "DE": "E"}
HI_NAME = {"BC": "B", "DE": "D"}
OTHER_PAIR = {"BC": "DE", "DE": "BC"}
_BIT_OF_LETTER = {"B": B, "C": C, "D": D, "E": E}
MAX_NODES = 2500
_DUMP = __import__("os").environ.get("UC80_RL_DUMP", "")
_DBG = bool(__import__("os").environ.get("UC80_RL_DEBUG"))


def _dbg(*a):
    if _DBG:
        import sys
        print("reglocals:", *a, file=sys.stderr)


class Bail(Exception):
    """The function has code the pass does not model."""


class Reject(Exception):
    """This configuration cannot be built."""


def enabled() -> bool:
    return _effect is not None


# ---------------------------------------------------------------------------
# The text of a function as nodes
# ---------------------------------------------------------------------------

class Node:
    """One line: an instruction, a label, a marker (a deleted no-op move
    that keeps the def-use facts) or text the pass leaves alone."""

    __slots__ = ("kind", "text", "op", "ops", "label", "rd", "wr", "flow",
                 "target", "size", "cond", "vr", "vw", "cpm", "xr", "w",
                 "pro", "wrap", "tight")

    def __init__(self, kind: str, text: str = ""):
        self.kind = kind          # ins label other data marker
        self.text = text
        self.op = ""
        self.ops: list[str] = []
        self.label = ""
        self.rd = 0               # scratch bytes the instruction reads
        self.wr = 0               # scratch bytes it writes
        self.flow = "next"
        self.target: Optional[str] = None
        self.size = 0
        self.cond = False
        self.vr = 0               # bytes read as a register local
        self.vw = 0               # bytes written as a register local
        self.cpm = 0              # bytes of a no-op copy (marker)
        self.xr = 0               # marker that reads a scratch value
        self.w = 1.0
        self.pro = False          # belongs to the prologue
        self.tight = False        # liveness treats the swap as a permutation
        self.wrap = 0


@dataclass
class Env:
    """What the pass needs to know from the code generator.  Both
    functions take the label of the callee.  ``callee_reads`` is a mask
    of the bytes B..E the callee reads; ``callee_writes`` is the mask it
    may write (the transformation always assumes ALL7 for the writes:
    the peephole pass can still change a callee)."""
    callee_reads: Callable[[str], int] = lambda name: ALL7
    callee_writes: Callable[[str], int] = lambda name: ALL7
    # an estimate of the writes, for the cost model only
    callee_writes_est: Callable[[str], int] = lambda name: ALL7
    # the bytes the function leaves live at its return (a result)
    exit_live: int = RET_LIVE


_NUM = re.compile(r"^[-+]?\d+$")
_LABEL = re.compile(r"^([A-Za-z_@?$.][A-Za-z0-9_@?$.]*):")


def _fmt(op: str, ops: list[str]) -> str:
    if ops:
        return f"\t{op}\t{','.join(ops)}"
    return f"\t{op}"


def _fill_effect(n: Node, env: Env, lenient: bool = False) -> None:
    ops_text = ",".join(n.ops)
    eff = _effect(n.op, ops_text)
    if eff.size is None or (eff.flow == "stop" and n.op not in
                            ("halt", "reti", "retn", "jp")):
        raise Bail(f"unknown instruction {n.text.strip()}")
    rd = wr = 0
    for r in eff.reads:
        rd |= BYTE_BIT.get(r, 0)
    for r in eff.writes:
        wr |= BYTE_BIT.get(r, 0)
    if eff.swap_de_hl:
        rd |= D | E | H | L
        wr |= D | E | H | L
    n.flow = eff.flow
    n.target = eff.target
    n.size = eff.size or 0
    n.cond = eff.cond
    if eff.flow == "call":
        if eff.cond:
            raise Bail("conditional call")
        tgt = eff.target
        rd = env.callee_reads(tgt) if tgt else ALL7
        wr = env.callee_writes(tgt) if tgt else ALL7
    if n.op == "exx":
        raise Bail("exx")
    n.rd = rd
    n.wr = wr


def make_ins(op: str, *ops: str, vr: int = 0, vw: int = 0,
             env: Env = None, tag: str = "") -> Node:
    n = Node("ins", _fmt(op, list(ops)) + (tag and "\t" + tag))
    n.op = op.lower()
    n.ops = list(ops)
    n.vr = vr
    n.vw = vw
    _fill_effect(n, env or Env())
    return n


def make_marker(vr: int = 0, cpm: int = 0, xr: int = 0) -> Node:
    n = Node("marker", "")
    n.vr = vr
    n.cpm = cpm
    n.xr = xr
    return n


_DATA_OPS = ("dw", "db", "defw", "defb", "ds", "defs", "defm")


_ASM_LABEL = re.compile(r"^([A-Za-z_@?$.][A-Za-z0-9_@?$.]*)::?$")


def parse(lines: list[str], env: Env) -> list[Node]:
    nodes: list[Node] = []
    in_asm = False
    for line in lines:
        if "__UC80_BARRIER" in line:
            raise Bail("barrier")
        if "__UC80_ASM_BEGIN__" in line:
            in_asm = True
            nodes.append(Node("other", line))
            continue
        if "__UC80_ASM_END__" in line:
            in_asm = False
            nodes.append(Node("other", line))
            continue
        s = _strip_comment(line).strip() if line else ""
        if not s:
            nodes.append(Node("other", line))
            continue
        if in_asm:
            # an asm block that only defines labels (the timer labels of
            # the benchmarks) is harmless; any other text is opaque
            m = _ASM_LABEL.match(s)
            if not m:
                raise Bail("asm")
            nd = Node("label", line)
            nd.label = m.group(1)
            nodes.append(nd)
            continue
        if line[0] not in " \t":
            m = _LABEL.match(s)
            if m:
                nd = Node("label", line if s[m.end():].strip() == ""
                          else line[:line.index(":") + 1])
                nd.label = m.group(1)
                nodes.append(nd)
                rest = s[m.end():].strip()
                if not rest:
                    continue
                line = "\t" + rest
                s = rest
            else:
                raise Bail(f"unknown line {line!r}")
        parts = s.split(None, 1)
        nd = Node("ins", line)
        nd.op = parts[0].lower()
        nd.ops = _split_ops(parts[1]) if len(parts) > 1 else []
        if nd.op in _DATA_OPS:
            nd.kind = "data"
            nodes.append(nd)
            continue
        _fill_effect(nd, env)
        nodes.append(nd)
    if len(nodes) > MAX_NODES:
        raise Bail("function too long")
    return nodes


def _retext(n: Node) -> None:
    n.text = _fmt(n.op, n.ops)


def render(nodes: list[Node]) -> list[str]:
    return [n.text for n in nodes if n.kind != "marker" and not n.pro]


# ---------------------------------------------------------------------------
# Flow graph, liveness, reaching definitions
# ---------------------------------------------------------------------------

def _dollar_target(nodes, i, k) -> Optional[int]:
    """The node ``$+k`` of the instruction at i, or None."""
    off = nodes[i].size
    j = i + 1
    while j < len(nodes):
        if off == k:
            return j
        if off > k:
            return None
        nd = nodes[j]
        if nd.kind == "data":
            return None
        if nd.kind == "ins":
            off += nd.size
        j += 1
    return len(nodes) if off == k else None


def _table_targets(nodes, i, labels) -> list[int]:
    """The nodes a ``jp (hl)`` can reach: the entries of the jump table that
    a ``ld DE,@SWTABn`` (or ``ld HL``) a few lines before names."""
    for j in range(i - 1, max(i - 40, -1), -1):
        nd = nodes[j]
        if nd.kind != "ins":
            if nd.kind == "label":
                break
            continue
        if nd.op == "ld" and len(nd.ops) == 2 and nd.ops[0].upper() in ("DE", "HL"):
            lab = nd.ops[1]
            if lab in labels and "SWTAB" in lab:
                k = labels[lab] + 1
                tg = []
                while k < len(nodes) and nodes[k].kind in ("data", "other"):
                    if nodes[k].kind == "data":
                        if nodes[k].op not in ("dw", "defw"):
                            raise Bail("jump table")
                        for t in nodes[k].ops:
                            if t not in labels:
                                raise Bail("jump table target")
                            tg.append(labels[t])
                    k += 1
                return tg
    raise Bail("jp (hl)")


def build_cfg(nodes: list[Node]) -> list[list[int]]:
    n = len(nodes)
    labels = {nd.label: i for i, nd in enumerate(nodes) if nd.kind == "label"}
    succ: list[list[int]] = []
    for i, nd in enumerate(nodes):
        nxt = i + 1          # n is the exit
        if nd.kind != "ins":
            succ.append([nxt])
            continue
        f = nd.flow
        if f in ("next", "call"):
            succ.append([nxt])
        elif f in ("jump", "branch"):
            tgt = nd.target or ""
            if tgt.startswith("$"):
                m = re.match(r"^\$\s*([+-])\s*(\d+)$", tgt)
                if not m or m.group(1) != "+":
                    raise Bail("odd $ target")
                t = _dollar_target(nodes, i, int(m.group(2)))
                if t is None:
                    raise Bail("$ target")
            else:
                t = labels.get(tgt, n)
            succ.append([t, nxt] if f == "branch" else [t])
        elif f == "return":
            succ.append([n, nxt] if nd.cond else [n])
        elif f == "stop":
            if nd.op == "jp":
                succ.append(_table_targets(nodes, i, labels))
            else:
                succ.append([n])
        else:
            raise Bail("flow")
    return succ


def liveness(nodes, succ, exit_live: int):
    """Live bytes at the entry of each node (B..E, H, L, A).  The exit
    reads D, E, H, L and A: a result, or the register arguments of a
    tail call."""
    n = len(nodes)
    live_in = [0] * (n + 1)
    live_in[n] = exit_live
    rd = [nd.rd for nd in nodes]
    wr = [nd.wr for nd in nodes]
    swap = [bool(sw) and _is_swap(nd) for nd, sw in
            zip(nodes, [getattr(nd, "tight", False) for nd in nodes])]
    changed = True
    while changed:
        changed = False
        for i in range(n - 1, -1, -1):
            out = 0
            for s in succ[i]:
                out |= live_in[s]
            if swap[i]:
                new = (out & ~(D | E | H | L)) | _swap_bits(out)
            else:
                new = (out & ~wr[i]) | rd[i]
            if new != live_in[i]:
                live_in[i] = new
                changed = True
    return live_in


def _is_swap(nd) -> bool:
    return (nd.kind == "ins" and nd.op == "ex"
            and [o.upper() for o in nd.ops] == ["DE", "HL"])


def _swap_bits(m: int) -> int:
    """The input bytes of ``ex DE,HL`` that the live output bytes need."""
    out = 0
    if m & D:
        out |= H
    if m & H:
        out |= D
    if m & E:
        out |= L
    if m & L:
        out |= E
    return out


def tighten_swaps(nodes, succ, exit_live: int) -> None:
    """``ex DE,HL`` reads both pairs, but a pair whose value ends up dead
    (the new HL or the new DE is never read) is not a real read.  The
    chain assignment ``ld HL,1 / ex DE,HL / ... ld (HL),E`` leaves such
    dead values in DE, and the false reads join unrelated values into one
    web.  This sets the read mask of each swap to what is live afterwards
    (from a liveness that treats the swap as a byte permutation).  A value
    that nobody reads stays unread when a later step renames or wraps
    another instruction, so the mask stays valid."""
    swaps = [i for i, nd in enumerate(nodes) if _is_swap(nd)]
    if not swaps:
        return
    for nd in nodes:
        nd.tight = False
    for i in swaps:
        nodes[i].tight = True
    live = liveness(nodes, succ, exit_live)
    for i in swaps:
        nodes[i].rd = live[i] & (D | E | H | L)
        nodes[i].tight = False


def live_out_of(succ, live_in, i) -> int:
    out = 0
    for s in succ[i]:
        out |= live_in[s]
    return out


class Reach:
    """Reaching definitions of the four scratch bytes.  Definitions 0..3
    are the values at the entry of the function; a node's definitions come
    after."""

    def __init__(self, nodes, succ):
        n = len(nodes)
        self.defs: list[tuple[int, int]] = [(-1, B), (-1, C), (-1, D), (-1, E)]
        self.bymask = {B: 1 << 0, C: 1 << 1, D: 1 << 2, E: 1 << 3}
        gen = [0] * n
        kill = [0] * n
        for i, nd in enumerate(nodes):
            w = nd.wr | nd.cpm
            if not w:
                continue
            for bit in (B, C, D, E):
                if w & bit:
                    k = len(self.defs)
                    self.defs.append((i, bit))
                    self.bymask[bit] |= 1 << k
                    gen[i] |= 1 << k
        for i, nd in enumerate(nodes):
            w = nd.wr | nd.cpm
            for bit in (B, C, D, E):
                if w & bit:
                    kill[i] |= self.bymask[bit]
        pred: list[list[int]] = [[] for _ in range(n + 1)]
        for i in range(n):
            for s in succ[i]:
                pred[s].append(i)
        entry = 0b1111
        rin = [0] * (n + 1)
        rout = [0] * n
        work = list(range(n))
        inq = [True] * n
        head = 0
        while head < len(work):
            i = work[head]
            head += 1
            inq[i] = False
            acc = entry if i == 0 else 0
            for p in pred[i]:
                acc |= rout[p]
            rin[i] = acc
            new = (acc & ~kill[i]) | gen[i]
            if new != rout[i]:
                rout[i] = new
                for s in succ[i]:
                    if s < n and not inq[s]:
                        inq[s] = True
                        work.append(s)
        self.rin = rin

    def defs_at(self, i: int, bit: int) -> list[int]:
        m = self.rin[i] & self.bymask[bit]
        out = []
        k = 0
        while m:
            if m & 1:
                out.append(k)
            m >>= 1
            k += 1
        return out


# ---------------------------------------------------------------------------
# The locals and the rewrite of their accesses
# ---------------------------------------------------------------------------

@dataclass
class Var:
    name: str
    kind: str            # "a": ??AUTO slot, "x": IX frame slot
    base: int            # ??AUTO offset or IX displacement of byte 0
    size: int            # bytes of the type (1 or 2)
    nbytes: int          # bytes of the slot (a char parameter in E has 2)
    is_param: bool = False
    param_loc: Optional[str] = None   # A HL E DE: where the value arrives
    weight: float = 0.0
    reg: Optional[str] = None          # "BC" or "DE" in a configuration
    hint: bool = False                 # declared with 'register'

    def bytemap(self):
        lo, hi = LO_NAME[self.reg], HI_NAME[self.reg]
        if self.size == 2:
            return [lo, hi]
        return [lo] + [None] * (self.nbytes - 1)

    def mask(self) -> int:
        if self.size == 2:
            return PAIR_MASK[self.reg]
        return _BIT_OF_LETTER[LO_NAME[self.reg]]


_AUTO_MEM = re.compile(r"^\(\?\?AUTO\+(\d+)\)$", re.IGNORECASE)
_AUTO_IMM = re.compile(r"^\?\?AUTO(?:\+(\d+))?$", re.IGNORECASE)
_IX_MEM = re.compile(r"^\(IX([+-]\d+)\)$", re.IGNORECASE)
_AUTO_ANY = re.compile(r"\?\?AUTO", re.IGNORECASE)
_IX_ANY = re.compile(r"(?<![A-Za-z0-9_@$.?])IX(?![A-Za-z0-9_@$.?])", re.IGNORECASE)
_ALU = ("add", "adc", "sub", "sbc", "and", "or", "xor", "cp")
_SHIFT = ("rlc", "rrc", "rl", "rr", "sla", "sra", "srl", "sll")
_R8 = ("A", "B", "C", "D", "E", "H", "L")
_R16 = {"HL": ("H", "L"), "DE": ("D", "E"), "BC": ("B", "C")}


def _slot_of(opnd: str):
    m = _AUTO_MEM.match(opnd)
    if m:
        return ("a", int(m.group(1)))
    m = _IX_MEM.match(opnd)
    if m:
        return ("x", int(m.group(1)))
    return None


def _owner(res, kind, addr, width):
    """The resident local that [addr, addr+width) touches, or None; a
    partial overlap rejects the configuration."""
    hit = None
    for v in res:
        if v.kind != kind:
            continue
        lo, hi = v.base, v.base + v.nbytes
        if addr + width <= lo or addr >= hi:
            continue
        if addr < lo or addr + width > hi:
            raise Reject("partial overlap")
        if hit is not None:
            raise Reject("two locals")
        hit = v
    return hit


def rewrite_accesses(nodes: list[Node], res: list[Var], env: Env) -> list[Node]:
    """The accesses of the resident locals become register moves."""
    out: list[Node] = []
    for nd in nodes:
        if nd.kind != "ins":
            out.append(nd)
            continue
        text = ",".join(nd.ops)
        if not (_AUTO_ANY.search(text) or _IX_ANY.search(text)):
            out.append(nd)
            continue
        out.extend(_rewrite_one(nd, res, env))
    return out


def _rewrite_one(nd: Node, res: list[Var], env: Env) -> list[Node]:
    op, ops = nd.op, nd.ops
    for o in ops:                       # the address of a slot
        m = _AUTO_IMM.match(o)
        if m:
            addr = int(m.group(1) or 0)
            for v in res:
                if v.kind == "a" and v.base <= addr < v.base + v.nbytes:
                    raise Reject("address of a slot")
    mems = [(k, _slot_of(o)) for k, o in enumerate(ops) if _slot_of(o)]
    if not mems:
        return [nd]
    if len(mems) != 1:
        raise Reject("two memory operands")
    k, (kind, addr) = mems[0]
    width = 1
    other = ops[1 - k] if len(ops) == 2 else None
    if op == "ld" and other is not None and other.upper() in _R16:
        width = 2
        if kind != "a":
            raise Reject("word access to a frame slot")
    var = _owner(res, kind, addr, width)
    if var is None:
        return [nd]
    bm = var.bytemap()
    j0 = addr - var.base
    mask = var.mask()

    def reg_of(j: int) -> str:
        r = bm[j]
        if r is None:
            raise Reject("access to a pad byte")
        return r

    if width == 2:                       # ld rr,(mem)   ld (mem),rr
        pair = other.upper()
        phi, plo = _R16[pair]
        loadform = (k == 1)
        if var.size == 2:
            if j0 != 0:
                raise Reject("word access off the base")
            lo, hi = reg_of(0), reg_of(1)
            if pair == var.reg:
                if loadform:
                    return [make_marker(vr=mask, cpm=mask)]
                return [make_marker(cpm=mask, xr=mask)]
            if loadform:
                return [make_ins("ld", phi, hi, vr=_BIT_OF_LETTER[hi], env=env),
                        make_ins("ld", plo, lo, vr=_BIT_OF_LETTER[lo], env=env)]
            return [make_ins("ld", hi, phi, vw=_BIT_OF_LETTER[hi], env=env),
                    make_ins("ld", lo, plo, vw=_BIT_OF_LETTER[lo], env=env)]
        # a one-byte local with a two-byte slot: only the store of a
        # parameter in E (with D) is understood; it writes the low byte
        if loadform or j0 != 0:
            raise Reject("word access to a byte local")
        lo = reg_of(0)
        lbit = _BIT_OF_LETTER[lo]
        if plo == lo:
            return [make_marker(cpm=lbit, xr=lbit)]
        return [make_ins("ld", lo, plo, vw=lbit, env=env)]

    # ---- byte forms
    if bm[j0] is None:
        if op == "ld" and k == 0:        # a store to a pad byte: dropped
            return [make_marker()]
        raise Reject("access to a pad byte")
    r = bm[j0]
    rbit = _BIT_OF_LETTER[r]

    def sub(newops, vr=0, vw=0):
        return make_ins(op, *newops, vr=vr, vw=vw, env=env)

    if op == "ld" and len(ops) == 2:
        if k == 1:                       # ld r,(mem)
            dst = ops[0].upper()
            if dst not in _R8:
                raise Reject("ld form")
            if dst == r:
                return [make_marker(vr=rbit, cpm=rbit)]
            return [sub([dst, r], vr=rbit)]
        src = ops[1]                     # ld (mem),src
        if src.upper() in _R8:
            if src.upper() == r:
                return [make_marker(cpm=rbit, xr=rbit)]
            return [sub([r, src.upper()], vw=rbit)]
        if src.upper() in _R16 or src.upper() in ("SP", "IX", "IY"):
            raise Reject("ld form")
        return [sub([r, src], vw=rbit)]  # an immediate
    if op in ("inc", "dec") and len(ops) == 1:
        return [sub([r], vr=rbit, vw=rbit)]
    if op in _ALU and k == len(ops) - 1:
        return [sub(ops[:-1] + [r], vr=rbit)]
    if op == "bit" and k == 1:
        return [sub([ops[0], r], vr=rbit)]
    if op in ("set", "res") and k == 1:
        return [sub([ops[0], r], vr=rbit, vw=rbit)]
    if op in _SHIFT and k == 0 and len(ops) == 1:
        return [sub([r], vr=rbit, vw=rbit)]
    raise Reject(f"form {op}")


# ---------------------------------------------------------------------------
# Foreign instructions: rename and wrap
# ---------------------------------------------------------------------------

def var_liveness(nodes, succ, M: int) -> list[int]:
    """Live bytes of the resident local (mask M) at each node entry.  Reads
    as a local make a byte live, writes as a local end it; foreign
    instructions do not count."""
    n = len(nodes)
    live_in = [0] * (n + 1)
    gen = [nd.vr & M for nd in nodes]
    kill = [nd.vw & M for nd in nodes]
    changed = True
    while changed:
        changed = False
        for i in range(n - 1, -1, -1):
            out = 0
            for s in succ[i]:
                out |= live_in[s]
            new = (out & ~kill[i]) | gen[i]
            if new != live_in[i]:
                live_in[i] = new
                changed = True
    return live_in


def _is_sp_use(nd: Node) -> bool:
    if nd.kind != "ins":
        return False
    for o in nd.ops:
        u = o.upper()
        if u == "SP" or "(SP)" in u:
            return True
    return False


def _stack_ok(nodes, lo, hi) -> bool:
    """The stack in [lo, hi] never goes below its depth at lo and ends
    at that depth, and nothing reads SP in a way the pass cannot follow.
    The cleanup ``ld HL,n / add HL,SP / ld SP,HL`` pops n/2 words."""
    depth = 0
    i = lo
    while i <= hi:
        nd = nodes[i]
        if nd.kind == "ins":
            if nd.op == "push":
                depth += 1
            elif nd.op == "pop":
                depth -= 1
                if depth < 0:
                    return False
            elif nd.op == "ex" and nd.ops and "(SP)" in nd.ops[0].upper():
                if depth < 1:
                    return False
            elif (nd.op == "ld" and len(nd.ops) == 2
                  and nd.ops[0].upper() == "HL" and _NUM.match(nd.ops[1])
                  and i + 2 <= hi
                  and nodes[i + 1].kind == "ins" and nodes[i + 1].op == "add"
                  and [o.upper() for o in nodes[i + 1].ops] == ["HL", "SP"]
                  and nodes[i + 2].kind == "ins" and nodes[i + 2].op == "ld"
                  and [o.upper() for o in nodes[i + 2].ops] == ["SP", "HL"]):
                v = int(nd.ops[1])
                if v < 0 or v % 2:
                    return False
                depth -= v // 2
                if depth < 0:
                    return False
                i += 3
                continue
            elif _is_sp_use(nd):
                return False
            elif nd.flow == "call" and nd.target in _STACK_RUNTIME:
                return False
        i += 1
    return depth == 0


_STACK_RUNTIME = {"__save_tmp64", "__restore_tmp64", "__push64_acc"}


def _region_ok(nodes, succ, s: int, e: int) -> bool:
    """[s, e] can hold a save and a restore: no label inside, no jump out
    of it, no way in but through its start."""
    n = len(nodes)
    for i in range(s, e + 1):
        nd = nodes[i]
        if i > s and nd.kind == "label":
            return False
        for t in succ[i]:
            if t == i + 1 and t <= e + 1 and t != n:
                continue
            if not (s <= t <= e) or t == n:
                if t == e + 1 and i == e:
                    continue
                return False
    for i in range(n):
        if s <= i <= e:
            continue
        for t in succ[i]:
            if s < t <= e:
                return False
    return True


def _call_group(nodes, c: int) -> tuple[int, int]:
    """For the call at c, the index of the first argument push (or c) and
    of the last cleanup instruction (or c)."""
    n = len(nodes)
    e = c
    j = c + 1
    pops = 0
    while j < n:
        nd = nodes[j]
        if nd.kind in ("other", "marker"):
            j += 1
            continue
        if (nd.kind == "ins" and nd.op == "pop" and nd.ops
                and nd.ops[0].upper() in ("BC", "DE", "HL", "AF")):
            pops += 1
            e = j
            j += 1
            continue
        if nd.kind == "ins" and nd.op == "inc" and [o.upper() for o in nd.ops] == ["SP"]:
            pops += 0.5
            e = j
            j += 1
            continue
        break
    sp_adjust = False
    if pops == 0:
        k = c + 1
        last = None
        cnt = 0
        while k < n and cnt < 14:
            nd = nodes[k]
            if nd.kind in ("other", "marker"):
                k += 1
                continue
            if nd.kind != "ins":
                break
            cnt += 1
            if nd.op == "ld" and [o.upper() for o in nd.ops] == ["SP", "HL"]:
                last = k
                break
            k += 1
        if last is not None:
            sp_adjust = True
            e = last
            nxt = last + 1
            while nxt < n and nodes[nxt].kind in ("other", "marker"):
                nxt += 1
            if (nxt < n and nodes[nxt].kind == "ins" and nodes[nxt].op == "ex"
                    and [o.upper() for o in nodes[nxt].ops] == ["DE", "HL"]):
                e = nxt
            # the 32-bit form saves DE in BC first: that is part of it
    s = c
    if pops > 0 or sp_adjust:
        need = int(pops) if pops > 0 else None
        depth = 0
        k = c - 1
        found = []
        while k >= 0 and (need is None or len(found) < need):
            nd = nodes[k]
            if nd.kind == "label":
                break
            if nd.kind == "ins":
                if nd.op == "pop":
                    depth += 1
                elif nd.op == "push":
                    if depth == 0:
                        found.append(k)
                    else:
                        depth -= 1
                elif nd.flow in ("jump", "branch", "return", "stop"):
                    break
            k -= 1
        if found:
            s = min(found)
    return s, e


def _cmp_ops(a: list[str], b: list[str]) -> bool:
    return [x.upper() for x in a] == b


def _renameable(nd: Node, src: str) -> bool:
    """Can the instruction use the other pair in place of ``src``?"""
    op = nd.op
    if nd.kind != "ins":
        return False
    if nd.flow != "next":
        return False
    if op in ("in", "out", "ini", "ind", "inir", "indr", "outi", "outd", "otir",
              "otdr", "ldi", "ldd", "ldir", "lddr", "cpi", "cpd", "cpir", "cpdr",
              "djnz", "exx", "daa", "rld", "rrd"):
        return False
    if op == "ex":
        return False
    for o in nd.ops:
        u = o.upper().replace(" ", "")
        if u in ("(HL)",):
            continue
        # a register token in an operand we cannot map
        if u in ("A", "H", "L", "HL", "SP", "AF", "IX", "IY", "I", "R"):
            continue
        if u in ("B", "C", "D", "E", "BC", "DE", "(BC)", "(DE)"):
            continue
        if re.match(r"^\(IX[+-]", u) or re.match(r"^\(IY[+-]", u):
            continue
        if u.startswith("(") and u.endswith(")"):
            if re.search(r"(?<![A-Za-z0-9_@$.?])(B|C|D|E|BC|DE|HL|SP)(?![A-Za-z0-9_@$.?])", u[1:-1]):
                return False
            continue
        # an immediate or a label expression
        if re.search(r"(?<![A-Za-z0-9_@$.?])(A|B|C|D|E|H|L|BC|DE|HL|SP|IX|IY)(?![A-Za-z0-9_@$.?])", u):
            return False
    if op in ("ld",) and len(nd.ops) == 2:
        d, s = nd.ops[0].upper(), nd.ops[1].upper()
        # ld (DE),r / ld r,(DE) exist only with A
        for p, q in ((d, s), (s, d)):
            if p in ("(BC)", "(DE)") and q != "A":
                return False
    return True


_SWAP = {"B": "D", "C": "E", "D": "B", "E": "C", "BC": "DE", "DE": "BC",
         "(BC)": "(DE)", "(DE)": "(BC)"}


def _rename_node(nd: Node, env: Env) -> Node:
    new = Node("ins", "")
    new.op = nd.op
    new.ops = [_SWAP.get(o.upper().replace(" ", ""), o) for o in nd.ops]
    new.vr = new.vw = 0
    _retext(new)
    _fill_effect(new, env)
    return new


def sink_pops(nodes, succ, M: int, live, other_free: bool, env: Env):
    """A cleanup ``pop rr`` whose value nobody reads: when it writes a byte
    of the local, it pops into the other pair (when that pair is free and
    dead there) or the stack pointer moves by two ``inc SP``."""
    out = list(nodes)
    changed = False
    i = 0
    res: list[Node] = []
    for i, nd in enumerate(nodes):
        if (nd.kind == "ins" and nd.op == "pop" and nd.ops
                and nd.ops[0].upper() in ("BC", "DE") and (nd.wr & M)
                and not (nd.vw & M)):
            pair = nd.ops[0].upper()
            lo = live_out_of(succ, live, i)
            if not (lo & PAIR_MASK[pair]):
                alt = OTHER_PAIR[pair]
                if other_free and not (lo & PAIR_MASK[alt]):
                    res.append(make_ins("pop", alt, env=env))
                else:
                    res.append(make_ins("inc", "SP", env=env))
                    res.append(make_ins("inc", "SP", env=env))
                changed = True
                continue
        res.append(nd)
    return res if changed else nodes


def fix_foreign(nodes: list[Node], var: Var, taken: int, env: Env,
                wrap_id: list[int]) -> list[Node]:
    """Make the instructions that touch the register of ``var`` safe.
    ``taken`` is the mask of the bytes that other locals occupy."""
    M = var.mask()
    pm = PAIR_MASK[var.reg]
    other = OTHER_PAIR[var.reg]
    om = PAIR_MASK[other]
    other_free = not (taken & om)
    # ---- 1. cleanup pops
    succ = build_cfg(nodes)
    live = liveness(nodes, succ, env.exit_live)
    nodes = sink_pops(nodes, succ, M, live, other_free, env)
    succ = build_cfg(nodes)
    live = liveness(nodes, succ, env.exit_live)
    vlive = var_liveness(nodes, succ, M)
    n = len(nodes)

    def foreign(nd):
        return (nd.kind == "ins" and ((nd.rd | nd.wr) & M & ~(nd.vr | nd.vw)))

    def dirty(i):
        nd = nodes[i]
        fw = nd.wr & M & ~nd.vw
        return bool(fw) and bool(live_out_of(succ, vlive, i) & fw)

    if not any(foreign(nd) and dirty(i) for i, nd in enumerate(nodes)):
        return nodes
    # ---- 2. webs of scratch values (over both bytes of the pair)
    reach = Reach(nodes, succ)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    isf = [bool(foreign(nd)) or (nd.kind == "ins" and (nd.rd | nd.wr) & pm
                                 and not (nd.vr | nd.vw) & pm)
           for nd in nodes]
    entry_read = [False] * n
    for i, nd in enumerate(nodes):
        if nd.kind != "ins" or not isf[i]:
            continue
        for bit in (B, C, D, E):
            if not (bit & pm and nd.rd & bit and not nd.vr & bit):
                continue
            for k in reach.defs_at(i, bit):
                di = reach.defs[k][0]
                if di < 0:
                    entry_read[i] = True
                elif isf[di]:
                    union(i, di)
    webs: dict[int, list[int]] = {}
    for i in range(n):
        if isf[i]:
            webs.setdefault(find(i), []).append(i)
    # ---- 3. rename the webs that can move to the other pair
    if other_free:
        renamed = set()
        for root, idxs in sorted(webs.items(), key=lambda kv: min(kv[1])):
            if not any(dirty(i) for i in idxs if nodes[i].wr & M):
                continue
            if any(entry_read[i] for i in idxs):
                continue
            lo, hi = min(idxs), max(idxs)
            if any(not _renameable(nodes[i], var.reg) for i in idxs):
                continue
            if any(nodes[i].flow == "call" for i in idxs):
                continue
            # straight-line: no label, no jump in [lo, hi]
            if any(nodes[i].kind == "label" or
                   (nodes[i].kind == "ins" and nodes[i].flow != "next")
                   for i in range(lo, hi + 1)):
                continue
            if live[lo] & om:
                continue
            inweb = set(idxs)
            if any(i not in inweb and nodes[i].kind == "ins" and
                   ((nodes[i].rd | nodes[i].wr) & om)
                   for i in range(lo, hi + 1)):
                continue
            if any(nodes[i].vr | nodes[i].vw for i in idxs):
                continue
            for i in idxs:
                nodes[i] = _rename_node(nodes[i], env)
            renamed.add(root)
        if renamed:
            succ = build_cfg(nodes)
            live = liveness(nodes, succ, env.exit_live)
            vlive = var_liveness(nodes, succ, M)
            reach = Reach(nodes, succ)
            isf = [bool(foreign(nd)) or (nd.kind == "ins" and (nd.rd | nd.wr) & pm
                                         and not (nd.vr | nd.vw) & pm)
                   for nd in nodes]
            parent[:] = list(range(n))
            for i, nd in enumerate(nodes):
                if nd.kind != "ins" or not isf[i]:
                    continue
                for bit in (B, C, D, E):
                    if not (bit & pm and nd.rd & bit and not nd.vr & bit):
                        continue
                    for k in reach.defs_at(i, bit):
                        di = reach.defs[k][0]
                        if di >= 0 and isf[di]:
                            union(i, di)
            webs = {}
            for i in range(n):
                if isf[i]:
                    webs.setdefault(find(i), []).append(i)
    # ---- 4. wrap what is left
    intervals: list[list[int]] = []
    for root, idxs in webs.items():
        if not any(nodes[i].wr & M & ~nodes[i].vw and dirty(i) for i in idxs):
            continue
        lo, hi = min(idxs), max(idxs)
        for i in idxs:
            if nodes[i].flow == "call":
                s, e = _call_group(nodes, i)
                lo, hi = min(lo, s), max(hi, e)
        intervals.append([lo, hi])
    # a call inside an interval extends it to its group
    changed = True
    while changed:
        changed = False
        for iv in intervals:
            for i in range(iv[0], iv[1] + 1):
                if nodes[i].kind == "ins" and nodes[i].flow == "call" and (nodes[i].wr & M):
                    s, e = _call_group(nodes, i)
                    if s < iv[0] or e > iv[1]:
                        iv[0], iv[1] = min(iv[0], s), max(iv[1], e)
                        changed = True
    intervals.sort()
    merged: list[list[int]] = []
    for iv in intervals:
        if merged and iv[0] <= merged[-1][1] + 0:
            merged[-1][1] = max(merged[-1][1], iv[1])
        else:
            merged.append(list(iv))
    inserts: list[tuple[int, int, Node, Node]] = []
    for lo, hi in merged:
        # first clobber
        c1 = None
        for i in range(lo, hi + 1):
            nd = nodes[i]
            if nd.kind == "ins" and (nd.wr & M & ~nd.vw):
                c1 = i
                break
        if c1 is None:
            continue
        for i in range(lo, hi + 1):
            nd = nodes[i]
            if nd.vw & M and nd.kind == "ins":
                raise Reject("write of the local inside a wrap")
            if nd.kind == "marker" and nd.cpm & M and not nd.vr & M:
                # a store marker: the value of the local changes
                raise Reject("store of the local inside a wrap")
            if i >= c1 and (nd.vr & M) and nd.kind == "ins":
                raise Reject("read of the local after a clobber")
            if i >= c1 and nd.kind == "marker" and (nd.vr & M):
                raise Reject("read of the local after a clobber")
        if not _region_ok(nodes, succ, lo, hi) or not _stack_ok(nodes, lo, hi):
            raise Reject("wrap region")
        if var.size == 1:
            ob = PAIR_MASK[var.reg] & ~M
            if live_out_of(succ, live, hi) & ob:
                raise Reject("other byte of the pair is live")
        wrap_id[0] += 1
        tag = f"; RL{wrap_id[0]}"
        pair = var.reg
        push = make_ins("push", pair, vr=M, env=env, tag=tag)
        pop = make_ins("pop", pair, vw=M, env=env, tag=tag)
        inserts.append((lo, hi, push, pop))
    for lo, hi, push, pop in sorted(inserts, key=lambda t: -t[0]):
        nodes.insert(hi + 1, pop)
        nodes.insert(lo, push)
    return nodes


# ---------------------------------------------------------------------------
# Rules for the copies that the rewrite leaves behind
# ---------------------------------------------------------------------------

def _plain(nd: Node) -> bool:
    return nd.kind == "ins" and nd.flow == "next"


def _ld_rr(nd: Node):
    """(dst, src) of ``ld r,r'`` (upper case), else None."""
    if _plain(nd) and nd.op == "ld" and len(nd.ops) == 2:
        d, s_ = nd.ops[0].upper(), nd.ops[1].upper()
        if d in _R8 and s_ in _R8:
            return d, s_
    return None


def _no_reg_token(text: str, regs: tuple) -> bool:
    return not re.search(r"(?<![A-Za-z0-9_@$.?])(" + "|".join(regs) + r")(?![A-Za-z0-9_@$.?])",
                         text, re.IGNORECASE)


def peephole(nodes: list[Node], config: list[Var], env: Env) -> list[Node]:
    """Rules for the moves between a register local and HL or the other
    pair.  Each rule needs the register that the old code used as a
    scratch to be dead afterwards."""
    for _ in range(3):
        succ = build_cfg(nodes)
        live = liveness(nodes, succ, env.exit_live)
        out: list[Node] = []
        i = 0
        n = len(nodes)
        changed = False
        while i < n:
            r = _try_rules(nodes, succ, live, config, env, i)
            if r is None:
                out.append(nodes[i])
                i += 1
                continue
            used, new = r
            out.extend(new)
            i += used
            changed = True
        nodes = out
        if not changed:
            break
    return nodes


def _window(nodes, i, k):
    """k consecutive plain-or-marker-free instruction nodes from i, or None."""
    if i + k > len(nodes):
        return None
    w = nodes[i:i + k]
    if any(x.kind != "ins" for x in w):
        return None
    return w


def _try_rules(nodes, succ, live, config, env, i):
    for v in config:
        if v.size != 2:
            continue
        R = v.reg
        rhi, rlo = HI_NAME[R], LO_NAME[R]
        mask = PAIR_MASK[R]
        # ---- R3: inc / dec of the local through HL
        w = _window(nodes, i, 5)
        if w:
            c0, c1, c2, c3, c4 = w
            p0, p1 = _ld_rr(c0), _ld_rr(c1)
            if (p0 and p1 and {p0, p1} == {("H", rhi), ("L", rlo)}
                    and _plain(c2) and c2.op in ("inc", "dec")
                    and [o.upper() for o in c2.ops] == ["HL"]):
                q3, q4 = _ld_rr(c3), _ld_rr(c4)
                if (q3 and q4 and {q3, q4} == {(rhi, "H"), (rlo, "L")}
                        and not (live_out_of(succ, live, i + 4) & (H | L))):
                    return 5, [make_ins(c2.op, R, vr=mask, vw=mask, env=env)]
        # ---- R1, R2: the local as the operand of an add or a subtract
        w = _window(nodes, i, 4)
        if w:
            c0, c1, c2, c3 = w
            p0, p1 = _ld_rr(c0), _ld_rr(c1)
            # ld H,Rh / ld L,Rl / ld rr,X / add HL,rr
            if (p0 and p1 and {p0, p1} == {("H", rhi), ("L", rlo)}
                    and _plain(c2) and c2.op == "ld" and len(c2.ops) == 2
                    and c2.ops[0].upper() in ("BC", "DE")
                    and c2.ops[0].upper() != R
                    and _plain(c3) and c3.op in ("add", "adc")
                    and [o.upper() for o in c3.ops] == ["HL", c2.ops[0].upper()]):
                rr = c2.ops[0].upper()
                if (_no_reg_token(c2.ops[1], ("H", "L", "HL", rr, "DE", "BC", "SP", "IX"))
                        and not (live_out_of(succ, live, i + 3) & PAIR_MASK[rr])):
                    return 4, [make_ins("ld", "HL", c2.ops[1], env=env),
                               make_ins(c3.op, "HL", R, vr=mask, env=env)]
            # ld rr,X / ld H,Rh / ld L,Rl / add HL,rr
            q0, q1 = _ld_rr(c1), _ld_rr(c2)
            if (q0 and q1 and {q0, q1} == {("H", rhi), ("L", rlo)}
                    and _plain(c0) and c0.op == "ld" and len(c0.ops) == 2
                    and c0.ops[0].upper() in ("BC", "DE")
                    and c0.ops[0].upper() != R
                    and _plain(c3) and c3.op in ("add", "adc")
                    and [o.upper() for o in c3.ops] == ["HL", c0.ops[0].upper()]):
                rr = c0.ops[0].upper()
                if (_no_reg_token(c0.ops[1], ("H", "L", "HL", rr, "DE", "BC", "SP", "IX"))
                        and not (live_out_of(succ, live, i + 3) & PAIR_MASK[rr])):
                    return 4, [make_ins("ld", "HL", c0.ops[1], env=env),
                               make_ins(c3.op, "HL", R, vr=mask, env=env)]
        # ---- R2: ld rr_hi,Rh / ld rr_lo,Rl / add|adc|sbc HL,rr
        w = _window(nodes, i, 3)
        if w:
            c0, c1, c2 = w
            p0, p1 = _ld_rr(c0), _ld_rr(c1)
            if p0 and p1:
                for rr in ("BC", "DE"):
                    if rr == R:
                        continue
                    if ({p0, p1} == {(HI_NAME[rr], rhi), (LO_NAME[rr], rlo)}
                            and _plain(c2) and c2.op in ("add", "adc", "sbc")
                            and [o.upper() for o in c2.ops] == ["HL", rr]
                            and not (live_out_of(succ, live, i + 2) & PAIR_MASK[rr])):
                        return 3, [make_ins(c2.op, "HL", R, vr=mask, env=env)]
    return None


# ---------------------------------------------------------------------------
# The checker
# ---------------------------------------------------------------------------

def check(nodes: list[Node], resident: int) -> bool:
    """True when, for the bytes in ``resident``, every read as a local
    sees only writes as a local, and every other read sees only other
    writes.  The value at the entry counts as both."""
    succ = build_cfg(nodes)
    reach = Reach(nodes, succ)
    for i, nd in enumerate(nodes):
        if nd.kind not in ("ins", "marker"):
            continue
        for bit in (B, C, D, E):
            if not bit & resident:
                continue
            if nd.vr & bit:
                for k in reach.defs_at(i, bit):
                    di = reach.defs[k][0]
                    if di < 0:
                        continue
                    d = nodes[di]
                    if (d.wr & bit) and not (d.vw & bit):
                        return False
            frd = ((nd.rd & ~nd.vr) | nd.xr) & bit
            if frd:
                for k in reach.defs_at(i, bit):
                    di = reach.defs[k][0]
                    if di < 0:
                        continue
                    d = nodes[di]
                    if (d.vw & bit) and not (d.wr & bit & ~d.vw):
                        return False
                    if (d.vw & bit) and not (d.cpm & bit):
                        return False
    return True


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def _is8(o: str) -> bool:
    return o.upper() in _R8


def _tstates(nd: Node) -> int:
    op = nd.op
    ops = [o.upper().replace(" ", "") for o in nd.ops]
    n = len(ops)

    def mem(o):
        return o.startswith("(") and o.endswith(")")

    def ixm(o):
        return o.startswith("(IX") or o.startswith("(IY")

    if op == "ld" and n == 2:
        d, s = ops
        if d in ("A", "B", "C", "D", "E", "H", "L") and s in ("A", "B", "C", "D", "E", "H", "L"):
            return 4
        if d in ("BC", "DE", "HL") and not mem(s) and s not in ("BC", "DE", "HL"):
            return 10
        if d == "SP" and s == "HL":
            return 6
        if ixm(d) or ixm(s):
            return 19
        if d in ("(HL)", "(BC)", "(DE)") or s in ("(HL)", "(BC)", "(DE)"):
            return 10 if (s not in ("A", "B", "C", "D", "E", "H", "L", "(HL)", "(BC)", "(DE)") and d == "(HL)") else 7
        if d == "HL" and mem(s):
            return 16
        if s == "HL" and mem(d):
            return 16
        if d in ("BC", "DE", "SP") and mem(s):
            return 20
        if s in ("BC", "DE", "SP") and mem(d):
            return 20
        if d == "A" and mem(s):
            return 13
        if s == "A" and mem(d):
            return 13
        if d in ("A", "B", "C", "D", "E", "H", "L"):
            return 7
        return 10
    if op == "push":
        return 15 if ops[0] in ("IX", "IY") else 11
    if op == "pop":
        return 14 if ops[0] in ("IX", "IY") else 10
    if op in ("inc", "dec"):
        o = ops[0]
        if o in ("BC", "DE", "HL", "SP"):
            return 6
        if ixm(o):
            return 23
        if o == "(HL)":
            return 11
        return 4
    if op in ("add", "adc", "sbc") and n == 2 and ops[0] in ("HL", "IX", "IY"):
        return 11 if (op == "add" and ops[0] == "HL") else 15
    if op in _ALU:
        s = ops[-1]
        if ixm(s):
            return 19
        if s == "(HL)":
            return 7
        if s in ("A", "B", "C", "D", "E", "H", "L"):
            return 4
        return 7
    if op in ("jp",):
        return 10
    if op in ("jr",):
        return 10 if n == 1 else 9
    if op == "djnz":
        return 10
    if op == "call":
        return 17
    if op == "ret":
        return 10 if n == 0 else 8
    if op == "ex":
        return 4 if ops == ["DE", "HL"] else 19
    if op in ("bit",):
        return 20 if ixm(ops[-1]) else (12 if ops[-1] == "(HL)" else 8)
    if op in ("set", "res") or op in _SHIFT:
        return 23 if ixm(ops[-1]) else (15 if ops[-1] == "(HL)" else 8)
    if op in ("rlca", "rrca", "rla", "rra", "cpl", "ccf", "scf", "daa", "nop", "di", "ei"):
        return 4
    if op in ("neg",):
        return 8
    if op in ("ldir", "lddr"):
        return 21
    if op == "rst":
        return 11
    return 8


def loop_weights(nodes, succ) -> list[float]:
    n = len(nodes)
    diff = [0] * (n + 2)
    for i in range(n):
        for t in succ[i]:
            if t <= i and t < n:
                diff[t] += 1
                diff[i + 1] -= 1
    out = []
    d = 0
    for i in range(n):
        d += diff[i]
        out.append(8.0 ** min(d, 3))
    return out


def cost(nodes, succ=None, est=None) -> tuple[float, int]:
    """(weighted T-states, bytes) of the function."""
    if succ is None:
        succ = build_cfg(nodes)
    w = loop_weights(nodes, succ)
    t = 0.0
    nb = 0
    for i, nd in enumerate(nodes):
        if nd.kind != "ins":
            continue
        c = _tstates(nd)
        if nd.flow == "call" and est is not None and nd.target:
            pass
        t += w[i] * c
        nb += nd.size
    return t, nb


def _weighted_cost(nodes, env: Env) -> float:
    """Cost with the wraps priced at what they cost after ``prune`` (a wrap
    around calls that write nothing of the pair is free)."""
    succ = build_cfg(nodes)
    w = loop_weights(nodes, succ)
    t = 0.0
    nb = 0
    free = set()
    # a wrap whose interior writes nothing of the pair, with the callee
    # estimates, is removed later
    stack: dict[str, tuple[int, str]] = {}
    for i, nd in enumerate(nodes):
        tag = _wrap_tag(nd)
        if tag is None:
            continue
        if nd.op == "push":
            stack[tag] = (i, nd.ops[0].upper())
        elif tag in stack:
            s, pair = stack.pop(tag)
            pm = PAIR_MASK[pair]
            ok = True
            for j in range(s + 1, i):
                x = nodes[j]
                if x.kind != "ins":
                    continue
                wr = x.wr
                if x.flow == "call" and x.target:
                    wr = env.callee_writes_est(x.target)
                if wr & pm and not _wrap_tag(x):
                    ok = False
                    break
            if ok:
                free.add(s)
                free.add(i)
    for i, nd in enumerate(nodes):
        if nd.kind != "ins" or i in free:
            continue
        t += w[i] * _tstates(nd)
        nb += nd.size
    return t + 6.0 * nb


_WRAP = re.compile(r";\s*RL(\d+)\s*$")


def _wrap_tag(nd: Node):
    if nd.kind != "ins" or nd.op not in ("push", "pop"):
        return None
    m = _WRAP.search(nd.text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# One function
# ---------------------------------------------------------------------------

@dataclass
class Result:
    lines: list[str]
    assigned: list[Var]
    prologue: list[str]
    gain: float


def _entry_moves(assigned: list[Var], env: Env):
    """Moves of register parameters into their registers, for a frame
    function (the prologue pushed them already).  Returns the nodes."""
    moves = []          # (dst letter, src letter)
    for v in assigned:
        if not v.is_param or v.param_loc is None:
            continue
        loc = v.param_loc
        dstmap = v.bytemap()
        if loc == "A":
            srcs = ["A"]
        elif loc == "E":
            srcs = ["E"]
        elif loc == "HL":
            srcs = ["L", "H"]
        else:
            srcs = ["E", "D"]
        for j, s in enumerate(srcs):
            if j < len(dstmap) and dstmap[j] is not None and dstmap[j] != s:
                moves.append((dstmap[j], s))
    out = []
    pending = list(moves)
    while pending:
        srcs = {s for _, s in pending}
        for m in pending:
            if m[0] not in srcs:
                pending.remove(m)
                dbit = _BIT_OF_LETTER[m[0]]
                out.append(make_ins("ld", m[0], m[1], vw=dbit, env=env))
                break
        else:
            raise Reject("cycle in the parameter moves")
    for nd in out:
        nd.pro = True
    return out


def try_config(base_nodes: list[Node], config: list[Var], env: Env,
               frame: bool, wrap_id: list[int]):
    """Build the text for one assignment of locals to registers.  Returns
    (nodes, prologue nodes) or raises Reject."""
    nodes = [copy_node(nd) for nd in base_nodes]
    nodes = rewrite_accesses(nodes, config, env)
    pro: list[Node] = []
    if frame:
        pro = _entry_moves(config, env)
        nodes = pro + nodes
    resident = 0
    for v in config:
        resident |= v.mask()
    for v in config:
        taken = 0
        for u in config:
            if u is not v:
                taken |= PAIR_MASK[u.reg]
        nodes = fix_foreign(nodes, v, taken, env, wrap_id)
    nodes = peephole(nodes, config, env)
    if not check(nodes, resident):
        raise Reject("check")
    return nodes, pro


def copy_node(nd: Node) -> Node:
    n = Node(nd.kind, nd.text)
    n.op = nd.op
    n.ops = list(nd.ops)
    n.label = nd.label
    n.rd, n.wr = nd.rd, nd.wr
    n.flow, n.target, n.size, n.cond = nd.flow, nd.target, nd.size, nd.cond
    n.vr, n.vw, n.cpm, n.xr = nd.vr, nd.vw, nd.cpm, nd.xr
    n.pro = nd.pro
    return n


def _slot_weight(nodes, succ, v: Var) -> float:
    w = loop_weights(nodes, succ)
    tot = 0.0
    for i, nd in enumerate(nodes):
        if nd.kind != "ins":
            continue
        for o in nd.ops:
            s = _slot_of(o)
            if s and s[0] == v.kind and v.base <= s[1] < v.base + v.nbytes:
                tot += w[i]
    return tot


def allocate(lines: list[str], vars_: list[Var], env: Env, frame: bool,
             wrap_id: list[int], max_vars: int = 2) -> Optional[Result]:
    """Choose registers for the locals in ``vars_`` and return the new text
    of the function body, or None when nothing pays."""
    if _effect is None or not vars_:
        return None
    try:
        nodes = parse(lines, env)
        succ = build_cfg(nodes)
    except Bail as ex:
        _dbg("bail", ex)
        return None
    # a jump to a label outside the function is a tail call: it reads the
    # argument registers, so the exit keeps the wide mask.  The jump to
    # the return label (@name_ret, emitted after the region) is a return.
    labels = {nd.label for nd in nodes if nd.kind == "label"}
    if any(nd.kind == "ins" and nd.flow in ("jump", "branch")
           and (nd.target or "") not in labels
           and not (nd.target or "").startswith(("$", "@")) for nd in nodes):
        env = dataclasses.replace(env, exit_live=RET_LIVE)
    tighten_swaps(nodes, succ, env.exit_live)
    for v in vars_:
        v.weight = _slot_weight(nodes, succ, v)
    cands = sorted([v for v in vars_ if v.weight >= 2.0],
                   key=lambda v: -v.weight * (2.0 if v.hint else 1.0))[:4]
    _dbg("candidates", [(v.name, v.weight) for v in vars_])
    if not cands:
        return None
    base = _weighted_cost(nodes, env)
    best = (0.0, None, None)
    singles = []
    for v in cands:
        for reg in ("BC", "DE"):
            v.reg = reg
            try:
                new, pro = try_config(nodes, [v], env, frame, wrap_id)
            except (Reject, Bail) as ex:
                _dbg("reject", v.name, reg, ex)
                continue
            finally:
                v.reg = None
            gain = base - _weighted_cost(new, env) - 0.0
            if v.hint and gain > 0:
                gain *= 1.25     # 'register' is a priority hint
            _dbg("single", v.name, reg, "gain", gain)
            if _DUMP == v.name + ":" + reg:
                import sys
                print("\n".join(render(new)), file=sys.stderr)
            singles.append((gain, v, reg, new, pro))
            if gain > best[0] + 3.0:
                best = (gain, [(v, reg)], (new, pro))
    singles.sort(key=lambda t: -t[0])
    if max_vars >= 2 and len(cands) >= 2:
        tried = set()
        top = [t for t in singles if t[0] > 0][:6]
        for a in range(len(top)):
            for b in range(len(top)):
                _, v1, r1 = top[a][:3]
                _, v2, r2 = top[b][:3]
                if v1 is v2 or r1 == r2 or (id(v1), id(v2)) in tried:
                    continue
                tried.add((id(v1), id(v2)))
                v1.reg, v2.reg = r1, r2
                try:
                    new, pro = try_config(nodes, [v1, v2], env, frame, wrap_id)
                except (Reject, Bail) as ex:
                    _dbg("reject", v1.name, v2.name, ex)
                    continue
                finally:
                    v1.reg = v2.reg = None
                gain = base - _weighted_cost(new, env)
                if gain > 0:
                    gain *= 1.0 + 0.25 * (v1.hint + v2.hint) / 2
                _dbg("pair", v1.name, r1, v2.name, r2, "gain", gain)
                if gain > best[0] + 3.0:
                    best = (gain, [(v1, r1), (v2, r2)], (new, pro))
    if best[1] is None:
        return None
    gain, assign, (new, pro) = best
    for v, reg in assign:
        v.reg = reg
    lines_out = render(new)
    prologue = [n.text for n in pro]
    return Result(lines_out, [v for v, _ in assign], prologue, gain)


# ---------------------------------------------------------------------------
# What the runtime routines read and write
# ---------------------------------------------------------------------------

_SKIP_DIRECTIVES = frozenset((
    "cseg", "dseg", "aseg", "common", "end", ".z80", ".8080", "public",
    "extrn", "ext", "extern", "global", "name", "title", "page", "list",
    ".list", ".xlist", "nolist", ".radix", "radix", "org", "equ", "defl",
    ".printx", "subttl", "entry", ".phase", ".dephase"))


def parse_lenient(lines: list[str], env: Env) -> list[Node]:
    """Like ``parse`` for library text and a whole program: a line the
    pass does not know becomes an instruction that reads and writes
    everything and ends the flow."""
    nodes: list[Node] = []
    for line in lines:
        s = _strip_comment(line).strip() if line else ""
        if not s:
            nodes.append(Node("other", line))
            continue
        if line[0] not in " \t":
            m = _LABEL.match(s)
            if m:
                nd = Node("label", line)
                nd.label = m.group(1)
                nodes.append(nd)
                rest = s[m.end():].strip()
                if not rest:
                    continue
                s = rest
                line = "\t" + rest
            else:
                nodes.append(_unknown(line))
                continue
        parts = s.split(None, 1)
        op = parts[0].lower()
        if op in _SKIP_DIRECTIVES:
            nd = Node("other", line)
            nd.op = op
            nd.ops = _split_ops(parts[1]) if len(parts) > 1 else []
            nodes.append(nd)
            continue
        nd = Node("ins", line)
        nd.op = op
        nd.ops = _split_ops(parts[1]) if len(parts) > 1 else []
        if op in _DATA_OPS:
            nd.kind = "data"
            nodes.append(nd)
            continue
        try:
            _fill_effect(nd, env)
        except Bail:
            nodes.append(_unknown(line))
            continue
        nodes.append(nd)
    return nodes


def _unknown(line: str) -> Node:
    nd = Node("ins", line)
    nd.op = "?"
    nd.flow = "stop"
    nd.rd = nd.wr = ALL7
    return nd


def _lenient_succ(nodes, i, labels, entries):
    """Successors of node i for a whole program; None for 'unknown'
    (a computed jump or a size the pass cannot follow).  A jump or call
    to a label that is not in the text gives the string of the name."""
    nd = nodes[i]
    n = len(nodes)
    if nd.kind != "ins":
        return [i + 1], None
    f = nd.flow
    if f in ("next", "call"):
        return [i + 1], (nd.target if f == "call" else None)
    if f in ("jump", "branch"):
        tgt = nd.target or ""
        if tgt.startswith("$"):
            m = re.match(r"^\$\s*\+\s*(\d+)$", tgt)
            t = _dollar_target(nodes, i, int(m.group(1))) if m else None
            if t is None:
                return None, None
            return ([t, i + 1] if f == "branch" else [t]), None
        if tgt in labels and tgt not in entries:
            return ([labels[tgt], i + 1] if f == "branch" else [labels[tgt]]), None
        # another routine: a tail jump
        return ([i + 1] if f == "branch" else []), tgt
    if f == "return":
        return ([i + 1] if nd.cond else []), None
    return None, None


class _Summaries:
    """May-write masks of the routines of a text, from the text."""

    def __init__(self, nodes):
        self.nodes = nodes
        self.labels = {nd.label: i for i, nd in enumerate(nodes)
                       if nd.kind == "label"}
        self.memo: dict[str, int] = {}
        self.base: dict[str, tuple[int, set]] = {}
        ent = set()
        for nd in nodes:
            if nd.kind == "other" and nd.op == "public":
                ent.update(nd.ops)
            elif nd.kind == "ins" and nd.flow == "call" and nd.target:
                ent.add(nd.target)
        self.entries = {e for e in ent if e in self.labels}

    def _scan(self, label):
        """(direct writes, set of other routines) of the routine."""
        nodes, labels = self.nodes, self.labels
        start = labels[label]
        seen = set()
        stack = [start]
        w = 0
        deps = set()
        while stack:
            i = stack.pop()
            if i in seen or i >= len(nodes):
                continue
            seen.add(i)
            nd = nodes[i]
            if nd.kind == "label" and i != start and nd.label in self.entries:
                # falls into another routine
                deps.add(nd.label)
                continue
            if nd.kind == "data":
                return ALL7, set()
            if nd.kind == "ins":
                if nd.flow != "call":
                    w |= nd.wr
            succ, ext = _lenient_succ(nodes, i, labels, self.entries)
            if succ is None:
                return ALL7, set()
            if ext is not None:
                if ext in labels:
                    deps.add(ext)
                else:
                    return ALL7, set()
            stack.extend(succ)
        return w, deps

    def writes(self, label: str, _busy=None) -> int:
        if label in self.memo:
            return self.memo[label]
        if label not in self.labels:
            return ALL7
        busy = _busy if _busy is not None else set()
        if label in busy:
            return 0            # a cycle adds nothing of its own
        busy.add(label)
        if label not in self.base:
            self.base[label] = self._scan(label)
        w, deps = self.base[label]
        cyc = False
        for d in deps:
            if d in busy:
                cyc = True
                continue
            w |= self.writes(d, busy)
        busy.discard(label)
        if w == ALL7 or not cyc or not busy:
            self.memo[label] = w
        return w


_HELPERS = None


def _helper_table():
    """label -> (reads, writes) of the routines of lib/rt."""
    global _HELPERS
    if _HELPERS is not None:
        return _HELPERS
    from pathlib import Path
    table: dict[str, tuple[int, int]] = {}
    root = Path(__file__).parent / "lib" / "rt"
    files = []
    if _effect is not None and root.is_dir():
        for path in sorted(root.glob("*.mac")):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            files.append(text.split("\n"))
    nodes: list[Node] = []
    for lines in files:
        nodes.extend(parse_lenient(lines, Env(lambda t: 0, lambda t: 0)))
        nodes.append(Node("other", ""))
    sums = _Summaries(nodes)
    labels = sums.labels
    # reads: backward liveness over the reachable part of each routine
    for lab in sums.entries:
        w = sums.writes(lab)
        table[lab.lower()] = (_routine_reads(sums, lab), w)
    _HELPERS = table
    return table


def _routine_reads(sums: _Summaries, label: str, _busy=None) -> int:
    memo = getattr(sums, "rmemo", None)
    if memo is None:
        memo = sums.rmemo = {}
    if label in memo:
        return memo[label]
    busy = _busy if _busy is not None else set()
    if label in busy or label not in sums.labels:
        return ALL7
    busy.add(label)
    nodes, labels = sums.nodes, sums.labels
    start = labels[label]
    reach = []
    seen = set()
    stack = [start]
    succs = {}
    ext_reads = {}
    ok = True
    while stack:
        i = stack.pop()
        if i in seen or i >= len(nodes):
            continue
        seen.add(i)
        nd = nodes[i]
        if nd.kind == "label" and i != start and nd.label in sums.entries:
            ext_reads[i] = _routine_reads(sums, nd.label, busy)
            succs[i] = []
            continue
        if nd.kind == "data":
            ok = False
            break
        s, ext = _lenient_succ(nodes, i, labels, sums.entries)
        if s is None:
            ok = False
            break
        extra = 0
        if ext is not None:
            extra = _routine_reads(sums, ext, busy)
        ext_reads[i] = extra
        succs[i] = s
        stack.extend(s)
    busy.discard(label)
    if not ok:
        memo[label] = ALL7
        return ALL7
    live = {i: 0 for i in succs}
    changed = True
    order = sorted(succs, reverse=True)
    while changed:
        changed = False
        for i in order:
            nd = nodes[i]
            out = 0
            for t in succs[i]:
                out |= live.get(t, 0)
            if nd.kind == "ins":
                rd = nd.rd | ext_reads.get(i, 0)
                kill = nd.wr if nd.flow != "call" else 0
                new = (out & ~kill) | rd
                if nd.flow == "call" and nd.target:
                    new = out | ext_reads.get(i, 0)
            else:
                new = out | ext_reads.get(i, 0)
            if new != live[i]:
                live[i] = new
                changed = True
    r = live[start]
    memo[label] = r
    return r


def helper_reads(label: str) -> int:
    t = _helper_table().get(label.lower())
    return t[0] if t else ALL7


def helper_writes(label: str) -> int:
    t = _helper_table().get(label.lower())
    return t[1] if t else ALL7


def function_writes(lines: list[str], env: Env, own_label: str) -> int:
    """May-write mask of one finished function (calls through ``env``)."""
    try:
        nodes = parse(lines, env)
    except Bail:
        return ALL7
    w = 0
    for nd in nodes:
        if nd.kind != "ins":
            continue
        if nd.flow == "call":
            if nd.target and nd.target != own_label:
                w |= env.callee_writes_est(nd.target)
        else:
            w |= nd.wr
    return w


# ---------------------------------------------------------------------------
# Removing the wraps that protect nothing
# ---------------------------------------------------------------------------

_TAG_ANY = re.compile(r"\s*;\s*RL(\d+)\s*$")


def strip_tags(code: str) -> str:
    return re.sub(r"[ \t]*; RL\d+[ \t]*(?=\n|$)", "", code)


def prune(code: str) -> str:
    """Remove each ``push rr`` / ``pop rr`` wrap (``; RLn``) when nothing
    between the two writes rr.  The text is the final program: the
    routines it calls are in it or in lib/rt."""
    if "; RL" not in code or _effect is None:
        return code
    lines = code.split("\n")
    pairs: dict[str, list[int]] = {}
    for i, ln in enumerate(lines):
        m = _TAG_ANY.search(ln)
        if m:
            pairs.setdefault(m.group(1), []).append(i)
    todo = [(k, v) for k, v in pairs.items() if len(v) == 2]
    if not todo:
        return strip_tags(code)
    env = Env(lambda t: ALL7, lambda t: ALL7)
    nodes = parse_lenient(lines, env)
    sums = _Summaries(nodes)
    helpers = _helper_table()

    def callee_w(t: Optional[str]) -> int:
        if not t:
            return ALL7
        if t in sums.labels:
            return sums.writes(t)
        h = helpers.get(t.lower())
        return h[1] if h else ALL7

    removed: set[int] = set()
    todo.sort(key=lambda kv: kv[1][1] - kv[1][0])
    for _, (p, q) in todo:
        a, b = nodes[p], nodes[q]
        if not (a.kind == "ins" and a.op == "push" and b.kind == "ins"
                and b.op == "pop" and a.ops == b.ops and a.ops
                and a.ops[0].upper() in PAIR_MASK):
            continue
        pm = PAIR_MASK[a.ops[0].upper()]
        ok = True
        depth = 0
        for j in range(p + 1, q):
            if j in removed:
                continue
            nd = nodes[j]
            if nd.kind in ("other", "marker"):
                continue
            if nd.kind != "ins":
                ok = False
                break
            if nd.flow == "call":
                if callee_w(nd.target) & pm:
                    ok = False
                    break
                if nd.target in _STACK_RUNTIME:
                    ok = False
                    break
            elif nd.flow in ("jump", "branch"):
                if not (nd.target or "").startswith("$"):
                    ok = False
                    break
            elif nd.flow in ("return", "stop"):
                ok = False
                break
            if nd.wr & pm and nd.flow != "call":
                ok = False
                break
            if nd.op == "push":
                depth += 1
            elif nd.op == "pop":
                depth -= 1
                if depth < 0:
                    ok = False
                    break
            elif _is_sp_use(nd):
                ok = False
                break
        if ok and depth == 0:
            removed.add(p)
            removed.add(q)
    out = [ln for i, ln in enumerate(lines) if i not in removed]
    return strip_tags("\n".join(out))
