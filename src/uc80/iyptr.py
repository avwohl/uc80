"""A pointer local that lives in IY.  See docs/register_locals.md.

A function that reads and writes fields through one pointer (``p->a``,
``p->next``, ``*s``) rebuilds the address for each access:
``ld HL,(p) / inc HL / inc HL / ld E,(HL)``.  With the pointer in IY the
access is one instruction, ``ld E,(IY+2)`` (19 T-states).  The pass works
on the assembly text of one function, like ``reglocals``.

Stage 1 (``convert``) changes the accesses of the pointer slot:

- ``ld HL,(p)`` followed by ``inc HL`` / ``ld DE,k / add HL,DE`` and an
  access through ``(HL)`` becomes the access through ``(IY+k)``, when HL
  is dead afterwards (and DE, when a ``ld DE,k`` goes away).
- Any other read of the slot becomes ``push IY / pop HL``.  A store of the
  pointer becomes ``push HL / pop IY``.  ``p += k`` becomes ``inc IY``.

Stage 2 (``wrap_calls``) runs after the pass for BC and DE.  A call may
write IY, so a call with IY live after it gets ``push IY`` before the
call group and ``pop IY`` after it (the comment ``; RYn``).  ``prune_iy``
removes each wrap in the final program when the callee never writes IY.
A runtime helper never changes IY.  An unknown callee (a library routine,
an assembly file, a call through a pointer) is assumed to change IY.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from . import reglocals as rl
from .reglocals import (Node, Env, Reject, Bail, Var, make_ins, parse,
                        build_cfg, liveness, tighten_swaps, render,
                        _slot_of, _AUTO_IMM, _call_group, _region_ok,
                        _stack_ok, A, B, C, D, E, H, L)

_PAIRS = {"HL": ("L", "H"), "DE": ("E", "D"), "BC": ("C", "B")}
_BYTE_REGS = ("A", "B", "C", "D", "E")
_ALU = ("add", "adc", "sub", "sbc", "and", "or", "xor", "cp")
_SHIFT = ("rlc", "rrc", "rl", "rr", "sla", "sra", "srl", "sll")
_NUM = re.compile(r"^[-+]?\d+$")
_BITOF = {"B": B, "C": C, "D": D, "E": E}
MAX_GROUP = 40
N_CANDS = int(rl._knob("UC80_IY_CANDS", 3))   # pointers tried in IY
IY_PRUNE = rl._knob("UC80_IY_PRUNE", 1.0)    # 0: keep every wrap
MARGIN = rl._knob("UC80_IY_MARGIN", 3.0)    # the least gain of IY over BC and DE

# the IY wraps carry this tag in the comment
_RY = re.compile(r";\s*RY(\d+)\s*$")


def _iy_bits(nd: Node) -> tuple[bool, bool]:
    """(reads IY, writes IY) of one instruction, from the effect table."""
    if nd.kind != "ins" or rl._effect is None:
        return False, False
    if not any("iy" in o.lower() for o in nd.ops):
        return False, False
    eff = rl._effect(nd.op, ",".join(nd.ops))
    rd = bool(eff.reads & {"iyl", "iyh"})
    wr = bool(eff.writes & {"iyl", "iyh"})
    return rd, wr


# ---------------------------------------------------------------------------
# Stage 1: the accesses of the slot
# ---------------------------------------------------------------------------

def _find_accesses(nodes: list[Node], var: Var) -> dict:
    """start index -> (end index (exclusive), 'load' | 'store', pair)."""
    acc: dict = {}
    skip: set = set()
    lo, hi = var.base, var.base + 2
    for i, nd in enumerate(nodes):
        if nd.kind != "ins" or i in skip:
            continue
        for o in nd.ops:
            m = _AUTO_IMM.match(o)
            if m and var.kind == "a" and lo <= int(m.group(1) or 0) < hi:
                raise Reject("address of the pointer slot")
        hit = [(k, _slot_of(o)) for k, o in enumerate(nd.ops)
               if _slot_of(o) and _slot_of(o)[0] == var.kind
               and lo <= _slot_of(o)[1] < hi]
        if not hit:
            continue
        if len(hit) != 1 or nd.op != "ld" or len(nd.ops) != 2:
            raise Reject("odd access to the pointer slot")
        k, (kind, addr) = hit[0]
        other = nd.ops[1 - k].upper()
        if var.kind == "a":
            if addr != lo or other not in _PAIRS:
                raise Reject("odd word access")
            acc[i] = (i + 1, "load" if k == 1 else "store", other)
            continue
        # a frame slot: two byte moves
        pair = next((p for p, (lw, hw) in _PAIRS.items() if other in (lw, hw)),
                    None)
        if pair is None or i + 1 >= len(nodes):
            raise Reject("odd frame access")
        nx = nodes[i + 1]
        if nx.kind != "ins" or nx.op != "ld" or len(nx.ops) != 2:
            raise Reject("odd frame access")
        k2 = 1 if _slot_of(nx.ops[1]) else 0 if _slot_of(nx.ops[0]) else -1
        if k2 != k:
            raise Reject("odd frame access")
        s2 = _slot_of(nx.ops[k2])
        o2 = nx.ops[1 - k2].upper()
        if not s2 or s2[0] != "x":
            raise Reject("odd frame access")
        lw, hw = _PAIRS[pair]
        ok = ((addr == lo and other == lw and s2[1] == lo + 1 and o2 == hw)
              or (addr == lo + 1 and other == hw and s2[1] == lo and o2 == lw))
        if not ok:
            raise Reject("odd frame access")
        acc[i] = (i + 2, "load" if k == 1 else "store", pair)
        skip.add(i + 1)
    return acc


def _plive(nodes, succ, acc) -> list[bool]:
    """Is the pointer live at the entry of each node?"""
    n = len(nodes)
    gen = [False] * n
    kill = [False] * n
    for i, (end, kind, pair) in acc.items():
        if kind == "load":
            gen[i] = True
        else:
            kill[min(end, n) - 1] = True
    live = [False] * (n + 1)
    changed = True
    while changed:
        changed = False
        for i in range(n - 1, -1, -1):
            out = any(live[s] for s in succ[i])
            new = gen[i] or (out and not kill[i])
            if new != live[i]:
                live[i] = new
                changed = True
    return live


def _imm(o: str) -> Optional[int]:
    if _NUM.match(o):
        v = int(o)
        if v > 32767:
            v -= 65536
        return v
    return None


def _group(nodes, live, acc, j: int, env: Env):
    """The code after ``ld HL,(p)`` that ends at node j (exclusive start).
    Returns (new nodes, index of the next node) or None."""
    n = len(nodes)
    k = 0
    dirty = 0
    out: list[Node] = []
    accesses = 0
    pos = j
    terminal = False
    adjust_only = True
    steps = 0

    def mk(op, *ops):
        return make_ins(op, *ops, env=env)

    while pos < n and steps < MAX_GROUP:
        steps += 1
        nd = nodes[pos]
        if nd.kind == "other":
            out.append(nd)
            pos += 1
            continue
        if nd.kind != "ins" or pos in acc:
            break
        u = [o.upper() for o in nd.ops]
        op = nd.op
        if op == "inc" and u == ["HL"]:
            k += 1
            pos += 1
            continue
        if op == "dec" and u == ["HL"]:
            k -= 1
            pos += 1
            continue
        if (op == "ld" and len(u) == 2 and u[0] in ("DE", "BC")
                and _imm(nd.ops[1]) is not None
                and pos + 1 < n and nodes[pos + 1].kind == "ins"
                and nodes[pos + 1].op == "add"
                and [o.upper() for o in nodes[pos + 1].ops] == ["HL", u[0]]):
            k += _imm(nd.ops[1])
            dirty |= (D | E) if u[0] == "DE" else (B | C)
            pos += 2
            continue
        if (op in ("jr", "jp") and len(u) == 2 and u[0] == "NZ"
                and pos + 3 < n and nodes[pos + 3].kind == "label"
                and nodes[pos + 3].label == nd.target
                and nodes[pos + 1].kind == "ins" and nodes[pos + 1].op == "inc"
                and [o.upper() for o in nodes[pos + 1].ops] == ["HL"]
                and nodes[pos + 2].kind == "ins" and nodes[pos + 2].op == "inc"
                and [o.upper() for o in nodes[pos + 2].ops] == ["(HL)"]):
            # a word in memory: inc (HL) / jr nz,L / inc HL / inc (HL) / L:
            prev = [x for x in out if x.kind == "ins"]
            if (prev and prev[-1].op == "inc" and prev[-1].ops
                    and prev[-1].ops[0].upper().startswith("(IY")
                    and -128 <= k + 1 <= 127):
                out.append(nd)
                out.append(mk("inc", f"(IY{k + 1:+d})"))
                accesses += 1
                pos += 3
            break
        if "(HL)" not in u:
            # an instruction that leaves HL alone may sit between
            if (nd.flow == "next" and not ((nd.rd | nd.wr) & (H | L))
                    and not (nd.rd & dirty) and nd.op not in ("push", "pop")
                    and not any(x == "SP" or "(SP)" in x for x in u)
                    and not any("(" in x and ("(HL)" == x) for x in u)):
                dirty &= ~nd.wr
                out.append(nd)
                pos += 1
                continue
            break
        # an access through (HL)
        if not -128 <= k <= 126:
            break
        disp = f"(IY{k:+d})"
        regs_used = 0
        for o in u:
            if o in _BITOF:
                regs_used |= _BITOF[o]
        new = None
        if op == "ld" and len(u) == 2:
            d, s = u
            if s == "(HL)" and d in _BYTE_REGS:
                new = mk("ld", nd.ops[0], disp)
                dirty &= ~_BITOF.get(d, 0)
            elif s == "(HL)" and d in ("L", "H"):
                # the last use: HL is then (garbage, value)
                dead = H if d == "L" else L
                nxt = live[pos + 1] if pos + 1 <= n else 0
                if nxt & dead:
                    break
                new = mk("ld", nd.ops[0], disp)
                terminal = True
            elif d == "(HL)" and s in _BYTE_REGS:
                if dirty & _BITOF.get(s, 0):
                    break
                new = mk("ld", disp, nd.ops[1])
            elif d == "(HL)" and s not in ("H", "L", "HL") and not (
                    s.startswith("(") or s in rl._R16):
                new = mk("ld", disp, nd.ops[1])
        elif op in ("inc", "dec") and u == ["(HL)"]:
            new = mk(op, disp)
        elif op in _ALU and u[-1] == "(HL)":
            new = mk(op, *nd.ops[:-1], disp)
        elif op == "bit" and u[-1] == "(HL)":
            new = mk(op, nd.ops[0], disp)
        elif op in ("set", "res") and u[-1] == "(HL)":
            new = mk(op, nd.ops[0], disp)
        elif op in _SHIFT and u == ["(HL)"]:
            new = mk(op, disp)
        if new is None:
            break
        if (dirty & regs_used) and new.op != "ld":
            break
        out.append(new)
        accesses += 1
        adjust_only = False
        pos += 1
        if terminal:
            break
    if accesses == 0:
        return None
    nxt = live[pos] if pos <= n else 0
    if terminal:
        if nxt & dirty:
            return None
        return out, pos
    # fold the swap after a word load: ld E,x / ld D,y / ex DE,HL
    if (pos < n and nodes[pos].kind == "ins" and nodes[pos].op == "ex"
            and [o.upper() for o in nodes[pos].ops] == ["DE", "HL"]
            and pos + 1 <= n and not (live[pos + 1] & (D | E))):
        ins = [x for x in out if x.kind == "ins"]
        if (len(ins) >= 2 and ins[-2].op == "ld" and ins[-1].op == "ld"
                and ins[-2].ops[0].upper() == "E" and ins[-1].ops[0].upper() == "D"
                and ins[-2].ops[1].upper().startswith("(IY")
                and ins[-1].ops[1].upper().startswith("(IY")
                and not (dirty & ~(D | E))):
            a, b = ins[-2], ins[-1]
            ia, ib = out.index(a), out.index(b)
            out[ia] = mk("ld", "L", a.ops[1])
            out[ib] = mk("ld", "H", b.ops[1])
            return out, pos + 1
    if nxt & (H | L):
        return None
    if nxt & dirty:
        return None
    return out, pos


def convert(nodes, succ, live, var: Var, env: Env, frame: bool):
    """Stage 1.  Returns the new nodes, or raises Reject."""
    acc = _find_accesses(nodes, var)
    if not acc:
        raise Reject("no access")
    n = len(nodes)
    out: list[Node] = []
    i = 0

    def mk(op, *ops):
        return make_ins(op, *ops, env=env)

    while i < n:
        a = acc.get(i)
        if a is None:
            out.append(nodes[i])
            i += 1
            continue
        end, kind, pair = a
        if kind == "store":
            out.append(mk("push", pair))
            out.append(mk("pop", "IY"))
            i = end
            continue
        if pair == "HL":
            post = _postinc(nodes, acc, end)
            if post is not None:
                cnt, after = post
                g = _group(nodes, live, acc, after, env)
                if g is not None:
                    out.extend(g[0])
                    op = "inc" if cnt > 0 else "dec"
                    out.extend(mk(op, "IY") for _ in range(abs(cnt)))
                    i = g[1]
                    continue
            g = _group(nodes, live, acc, end, env)
            if g is not None:
                out.extend(g[0])
                i = g[1]
                continue
            adj = _adjust(nodes, live, acc, end, env)
            if adj is not None:
                out.extend(adj[0])
                i = adj[1]
                continue
        out.append(mk("push", "IY"))
        out.append(mk("pop", pair))
        i = end
    return out


def _postinc(nodes, acc, j: int):
    """``push HL / inc HL.. / ld (p),HL / pop HL`` after ``ld HL,(p)``: the
    value of ``p++``.  Returns (count, index after the pop) or None."""
    n = len(nodes)
    if not (j < n and nodes[j].kind == "ins" and nodes[j].op == "push"
            and [o.upper() for o in nodes[j].ops] == ["HL"]):
        return None
    pos = j + 1
    cnt = 0
    while pos < n and nodes[pos].kind == "ins":
        u = [o.upper() for o in nodes[pos].ops]
        if nodes[pos].op == "inc" and u == ["HL"]:
            cnt += 1
        elif nodes[pos].op == "dec" and u == ["HL"]:
            cnt -= 1
        else:
            break
        pos += 1
    a = acc.get(pos)
    if a is None or a[1] != "store" or a[2] != "HL" or cnt == 0 or abs(cnt) > 4:
        return None
    pos = a[0]
    if not (pos < n and nodes[pos].kind == "ins" and nodes[pos].op == "pop"
            and [o.upper() for o in nodes[pos].ops] == ["HL"]):
        return None
    return cnt, pos + 1


def _adjust(nodes, live, acc, j: int, env: Env):
    """``ld HL,(p) / inc HL.. / ld (p),HL`` (``p++``, ``p += k``)."""
    n = len(nodes)
    k = 0
    pos = j
    de_imm = None
    while pos < n:
        nd = nodes[pos]
        if nd.kind != "ins":
            break
        u = [o.upper() for o in nd.ops]
        if nd.op == "inc" and u == ["HL"]:
            k += 1
        elif nd.op == "dec" and u == ["HL"]:
            k -= 1
        elif (nd.op == "ld" and len(u) == 2 and u[0] in ("DE", "BC")
              and _imm(nd.ops[1]) is not None and pos + 1 < n
              and nodes[pos + 1].kind == "ins" and nodes[pos + 1].op == "add"
              and [o.upper() for o in nodes[pos + 1].ops] == ["HL", u[0]]):
            k += _imm(nd.ops[1])
            de_imm = u[0]
            pos += 2
            continue
        else:
            break
        pos += 1
    a = acc.get(pos)
    if a is None or a[1] != "store" or a[2] != "HL" or k == 0:
        return None
    end = a[0]
    if live[end] & (H | L):
        return None
    mk = lambda op, *ops: make_ins(op, *ops, env=env)
    if de_imm is None and abs(k) <= 4:
        return [mk("inc" if k > 0 else "dec", "IY") for _ in range(abs(k))], end
    rr = de_imm or "DE"
    if de_imm is None and live[end] & (D | E):
        return None
    return [mk("ld", rr, str(k)), mk("add", "IY", rr)], end


# ---------------------------------------------------------------------------
# Stage 2: wrap the calls
# ---------------------------------------------------------------------------

def _iy_live(nodes, succ):
    n = len(nodes)
    bits = [_iy_bits(nd) for nd in nodes]
    live = [False] * (n + 1)
    changed = True
    while changed:
        changed = False
        for i in range(n - 1, -1, -1):
            rd, wr = bits[i]
            out = any(live[s] for s in succ[i])
            # a read-modify-write (inc IY) reads first
            new = rd or (out and not wr)
            if new != live[i]:
                live[i] = new
                changed = True
    return live


def _fit(nodes, succ, s: int, e: int):
    """Widen [s, e] until the stack is balanced and no jump crosses it."""
    n = len(nodes)
    best = None
    for tot in range(0, 9):
        for ds in range(0, tot + 1):
            de = tot - ds
            lo, hi = s - ds, e + de
            if lo < 0 or hi >= n:
                continue
            if _stack_ok(nodes, lo, hi) and _region_ok(nodes, succ, lo, hi):
                return lo, hi
    return best


def wrap_calls(nodes: list[Node], env: Env, wrap_id: list[int],
               clean: Callable[[str], bool]) -> list[Node]:
    """Stage 2: ``push IY`` / ``pop IY`` around the calls that may change
    IY while the pointer is live."""
    if not any(_iy_bits(nd)[0] or _iy_bits(nd)[1] for nd in nodes):
        return nodes
    succ = build_cfg(nodes)
    live = _iy_live(nodes, succ)
    n = len(nodes)
    ivs = []
    for i, nd in enumerate(nodes):
        if nd.kind != "ins" or nd.flow != "call":
            continue
        out = any(live[s] for s in succ[i])
        if not out:
            continue
        if nd.target and clean(nd.target):
            continue
        s, e = _call_group(nodes, i)
        fit = _fit(nodes, succ, s, e)
        if fit is None:
            raise Reject("IY wrap region")
        ivs.append(list(fit))
    if not ivs:
        return nodes
    # Each region is balanced, so two regions are nested or apart.  A
    # nested pair stays two wraps: the pointer is read after the inner
    # call, inside the outer region.
    regions = sorted({(lo, hi) for lo, hi in ivs})
    before: dict[int, list[tuple[int, Node]]] = {}
    after: dict[int, list[tuple[int, Node]]] = {}
    for lo, hi in regions:
        wrap_id[0] += 1
        tag = f"; RY{wrap_id[0]}"
        push = make_ins("push", "IY", env=env, tag=tag)
        pop = make_ins("pop", "IY", env=env, tag=tag)
        before.setdefault(lo, []).append((-hi, push))   # outer first
        after.setdefault(hi, []).append((-lo, pop))     # inner first
    res: list[Node] = []
    for i, nd in enumerate(nodes):
        for _, x in sorted(before.get(i, []), key=lambda t: t[0]):
            res.append(x)
        res.append(nd)
        for _, x in sorted(after.get(i, []), key=lambda t: t[0]):
            res.append(x)
    return res


# ---------------------------------------------------------------------------
# The whole function
# ---------------------------------------------------------------------------

def _wide_env(nodes, env: Env) -> Env:
    import dataclasses
    labels = {nd.label for nd in nodes if nd.kind == "label"}
    if any(nd.kind == "ins" and nd.flow in ("jump", "branch")
           and (nd.target or "") not in labels
           and not (nd.target or "").startswith(("$", "@")) for nd in nodes):
        return dataclasses.replace(env, exit_live=rl.RET_LIVE)
    return env


def candidates(vars_, nodes, succ) -> list[Var]:
    out = []
    for v in vars_:
        if not v.is_ptr or v.size != 2:
            continue
        v.weight = rl._slot_weight(nodes, succ, v)
        if v.weight >= 3.0:
            out.append(v)
    out.sort(key=lambda v: -v.weight)
    return out[:N_CANDS]


def apply(lines: list[str], var: Var, rest: list[Var], env: Env, frame: bool,
          wrap_id: list[int], clean: Callable[[str], bool]):
    """Put ``var`` in IY, let ``reglocals.allocate`` place ``rest``, wrap
    the calls.  Returns (lines, prologue lines, assigned vars) or raises
    Reject / Bail."""
    nodes = parse(lines, env)
    succ = build_cfg(nodes)
    wenv = _wide_env(nodes, env)
    tighten_swaps(nodes, succ, wenv.exit_live)
    live = liveness(nodes, succ, wenv.exit_live)
    new = convert(nodes, succ, live, var, env, frame)
    text = render(new)
    # a frame function: the pointer parameter is in HL or DE at the entry,
    # before the moves of the other parameters
    pro: list[str] = []
    if frame and var.is_param and var.param_loc in ("HL", "DE"):
        pro = [f"\tpush\t{var.param_loc}", "\tpop\tIY"]
    assigned: list[Var] = []
    if rest:
        res = rl.allocate(text, rest, env, frame, wrap_id)
        if res is not None:
            text = res.lines
            pro = pro + res.prologue
            assigned = res.assigned
    fin = parse(text, env)
    fin = wrap_calls(fin, env, wrap_id, clean)
    return render(fin), pro, assigned


def cost_of(lines: list[str], pro: list[str], env: Env) -> float:
    nodes = parse(list(pro) + list(lines), env)
    return rl._weighted_cost(nodes, env)


def best(region: list[str], vars_: list[Var], env: Env, frame: bool,
         wrap_id: list[int], base_lines: list[str], base_pro: list[str],
         clean: Callable[[str], bool], margin: float = MARGIN):
    """Try each pointer candidate in IY.  ``base_lines``/``base_pro`` are
    the result without IY (or the region itself).  Returns
    (lines, pro, assigned, var, gain) or None."""
    try:
        nodes = parse(region, env)
        succ = build_cfg(nodes)
        cands = candidates(vars_, nodes, succ)
        base = cost_of(base_lines, base_pro, env)
    except (Bail, Reject) as ex:
        rl._dbg("iy bail", ex)
        return None
    top = None
    for v in cands:
        rest = [u for u in vars_ if u is not v]
        try:
            lines, pro, assigned = apply(region, v, rest, env, frame,
                                         wrap_id, clean)
            c = cost_of(lines, pro, env)
        except (Bail, Reject) as ex:
            rl._dbg("iy reject", v.name, ex)
            continue
        rl._dbg("iy", v.name, "cost", c, "base", base)
        if c < base - margin and (top is None or c < top[0]):
            top = (c, lines, pro, assigned, v)
    if top is None:
        return None
    c, lines, pro, assigned, v = top
    return lines, pro, assigned, v, base - c


# ---------------------------------------------------------------------------
# What the routines write: the clean callees, and pruning the wraps
# ---------------------------------------------------------------------------

IYW = 128
_NOT_CLEAN_HELPERS = {"__callhl"}


class _IYSums(rl._Summaries):
    FULL = rl.ALL7 | IYW


def _mark(nodes: list[Node], contract: bool) -> None:
    """Put IYW into the write mask of each node that changes IY (and of
    each node the pass does not know).  A runtime routine keeps the IY of
    its caller (``contract``), so its own pushes do not count."""
    for nd in nodes:
        if nd.kind == "ins" and nd.op == "?":
            nd.wr |= IYW
        elif not contract and _iy_bits(nd)[1]:
            nd.wr |= IYW


_RT_TABLE: Optional[dict] = None


def _rt_iy() -> dict:
    """label (lower case) -> True when the routine of lib/rt may change
    IY (it calls something outside lib/rt, or the pass cannot follow it)."""
    global _RT_TABLE
    if _RT_TABLE is not None:
        return _RT_TABLE
    from pathlib import Path
    table: dict[str, bool] = {}
    root = Path(__file__).parent / "lib" / "rt"
    nodes: list[Node] = []
    if rl._effect is not None and root.is_dir():
        for path in sorted(root.glob("*.mac")):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            nodes.extend(rl.parse_lenient(text.split("\n"),
                                          Env(lambda t: 0, lambda t: 0)))
            nodes.append(Node("other", ""))
        _mark(nodes, True)
        sums = _IYSums(nodes)
        for lab in sums.entries:
            table[lab.lower()] = bool(sums.writes(lab) & IYW)
    for nm in _NOT_CLEAN_HELPERS:
        table[nm] = True
    _RT_TABLE = table
    return table


def helper_clean(label: str) -> bool:
    """A runtime helper that never changes IY."""
    return _rt_iy().get(label.lower(), True) is False


def function_clean(lines: list[str], env: Env, own: str,
                   clean: Callable[[str], bool]) -> bool:
    """The finished function never touches IY, and calls only clean code."""
    try:
        nodes = parse(lines, env)
    except Bail:
        return False
    for nd in nodes:
        if nd.kind != "ins":
            continue
        if any("iy" in o.lower() for o in nd.ops):
            return False
        if nd.flow == "call" and nd.target and nd.target != own \
                and not clean(nd.target):
            return False
        if nd.flow in ("jump", "branch") and nd.target and not (
                nd.target.startswith(("$", "@"))) and nd.target != own:
            labels = {x.label for x in nodes if x.kind == "label"}
            if nd.target not in labels and not clean(nd.target):
                return False
    return True


def prune_iy(code: str) -> str:
    """Remove each ``push IY`` / ``pop IY`` wrap (``; RYn``) whose calls
    never change IY.  The text is the final program."""
    if "; RY" not in code or rl._effect is None:
        return code
    if not IY_PRUNE:
        return re.sub(r"[ \t]*; RY\d+[ \t]*(?=\n|$)", "", code)
    lines = code.split("\n")
    pairs: dict[str, list[int]] = {}
    for i, ln in enumerate(lines):
        m = _RY.search(ln)
        if m:
            pairs.setdefault(m.group(1), []).append(i)
    todo = sorted((v for v in pairs.values() if len(v) == 2),
                  key=lambda v: v[1] - v[0])
    strip = lambda txt: re.sub(r"[ \t]*; RY\d+[ \t]*(?=\n|$)", "", txt)
    if not todo:
        return strip(code)
    env = Env(lambda t: rl.ALL7, lambda t: rl.ALL7)
    nodes = rl.parse_lenient(lines, env)
    _mark(nodes, False)
    sums = _IYSums(nodes)
    table = _rt_iy()

    def callee_writes(t: Optional[str]) -> bool:
        if not t:
            return True
        # a runtime helper keeps IY (rt_div32 saves it): the table, not the
        # embedded text, tells
        if t.startswith("__") and t.lower() in table:
            return table[t.lower()]
        if t in sums.labels:
            return bool(sums.writes(t) & IYW)
        return table.get(t.lower(), True)

    removed: set[int] = set()
    for p, q in todo:
        ok = True
        for j in range(p + 1, q):
            nd = nodes[j]
            if nd.kind != "ins":
                continue
            if nd.flow == "call":
                if callee_writes(nd.target):
                    ok = False
                    break
            elif nd.flow in ("jump", "branch", "return", "stop"):
                if not (nd.target or "").startswith("$"):
                    ok = False
                    break
            elif _iy_bits(nd)[1] and j not in removed:
                ok = False
                break
        if ok:
            removed.add(p)
            removed.add(q)
    out = [ln for i, ln in enumerate(lines) if i not in removed]
    return strip("\n".join(out))
