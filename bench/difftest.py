#!/usr/bin/env python3
"""Differential tester: random C programs, built with uc80 and with SDCC.

    python3 bench/difftest.py --count 100                 # all focus areas
    python3 bench/difftest.py --count 300 --focus shifts,long -j 8
    python3 bench/difftest.py --count 500 --keep-failures /tmp/df --reduce-failures
    python3 bench/difftest.py --reduce /tmp/df/s17-shifts/prog.c
    python3 bench/difftest.py --print 17 --focus calls    # show one program

Each program is random, but free of undefined behaviour: globals, locals,
arrays, structs (with bit-fields), pointers, helper functions (some called
through tables of function pointers), all integer types of 8, 16 and 32
bits, every C integer operator, compound assignment, ++/--, if, loops and
switch.  A checksum takes the value of each variable after it changes, and
main prints the checksum after each top-level statement.

The generator builds a syntax tree, and a Python model of 16-bit-int C
runs that tree.  The model gives a third answer, and it also checks at run
time that no statement has undefined behaviour (signed overflow, a shift
count out of range, a division by zero, INT_MIN / -1, an index out of
range).  A program that has undefined behaviour is made again with the
next attempt number.  Thus each result names the compiler that is wrong:

  PASS         uc80, SDCC and the model agree
  UC80-WRONG   SDCC agrees with the model, uc80 does not
  SDCC-WRONG   uc80 agrees with the model, SDCC does not
  MODEL-DIFF   uc80 and SDCC agree, the model does not: a generator bug,
               or the same bug in both compilers
  ALL-DIFF     three different outputs
  <C>-BUILD-FAIL, <C>-HANG

uc80 has a signed plain char; SDCC gets --fsigned-char to match.  The
reducer (--reduce, or --reduce-failures after a run) deletes statements,
declarations and sub-expressions while the result stays the same.  A file
that this script wrote has its seed in the first line, so the reducer can
make the syntax tree again and use the model.  For another file, the
reducer deletes lines while the uc80 and SDCC outputs stay different.
docs/bench_harness.md describes the tool.
"""

from __future__ import annotations

import argparse
import copy
import os
import random
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compilers import BENCH, Sdcc, Uc80  # noqa: E402
from run import normalize, run_ticks, ticks_path  # noqa: E402

GEN_VERSION = 2
FOCI = ["all", "char-arith", "const-ops", "conditions", "assign",
        "bitfields", "shifts", "mul-div-const", "long", "switch", "calls",
        "incdec", "memory"]


# ---------------------------------------------------------------- types

class CType:
    """An integer type.  bf: a bit-field of `bits` bits."""
    __slots__ = ("name", "bits", "signed", "bf")

    def __init__(self, name, bits, signed, bf=False):
        self.name, self.bits, self.signed, self.bf = name, bits, signed, bf

    @property
    def lo(self):
        return -(1 << (self.bits - 1)) if self.signed else 0

    @property
    def hi(self):
        return (1 << (self.bits - (1 if self.signed else 0))) - 1

    def __repr__(self):
        return f"{self.name}:{self.bits}" if self.bf else self.name

    def __deepcopy__(self, memo):
        return self              # code compares types with `is`


CH = CType("char", 8, True)            # plain char: signed in uc80
SC = CType("signed char", 8, True)
UC = CType("unsigned char", 8, False)
I = CType("int", 16, True)
U = CType("unsigned int", 16, False)
L = CType("long", 32, True)
UL = CType("unsigned long", 32, False)
SCALARS = [CH, SC, UC, I, U, L, UL]
CHARS = (CH, SC, UC)


def bitfield(bits, signed):
    return CType("signed int" if signed else "unsigned int", bits, signed,
                 True)


def wrap(v, t):
    m = v & ((1 << t.bits) - 1)
    if t.signed and m >> (t.bits - 1):
        m -= 1 << t.bits
    return m


def promote(t):
    if t.bf:
        return I if (t.signed or t.bits < 16) else U
    return I if t.bits < 16 else t


def arith(a, b):
    """The type of the usual arithmetic conversions (int is 16 bits)."""
    a, b = promote(a), promote(b)
    if a is UL or b is UL:
        return UL
    if a is L or b is L:
        return L          # long holds every unsigned int value
    if a is U or b is U:
        return U
    return I


def unsigned_of(t):
    return U if t is I else UL if t is L else t


def conv_range(e, t):
    if t.lo <= e.lo and e.hi <= t.hi:
        return e.lo, e.hi
    return t.lo, t.hi


def lit_type(t):
    """The type of a literal that holds every value of t."""
    if t is I or t is U or t is L or t is UL:
        return t
    return U if t.bf and not t.signed and t.bits == 16 else I


def cconst(v, t, style=None):
    """C text for the constant v of type t (int, unsigned, long, ulong)."""
    suf = {id(I): "", id(U): "u", id(L): "L", id(UL): "UL"}[id(t)]
    if style == "char" and t is I and 32 <= v < 127 and chr(v) not in "'\\":
        return f"'{chr(v)}'"
    if v < 0:
        if v == t.lo:
            return f"(-{-(v + 1)}{suf}-1)"
        return f"(-{-v}{suf})"
    if style == "hex":
        return f"0x{v:X}{suf}"
    return f"{v}{suf}"


class UB(Exception):
    """The model found undefined behaviour."""


class ModelError(Exception):
    """The tree cannot run (a reduction made it invalid)."""


class Hang(Exception):
    pass


class Brk(Exception):
    pass


class Cont(Exception):
    pass


class Ret(Exception):
    def __init__(self, v):
        self.v = v


CMPS = ("<", "<=", ">", ">=", "==", "!=")


def binop(op, va, ta, vb, tb):
    """C semantics of `a op b`; returns (type, value) or raises UB."""
    if op in ("<<", ">>"):
        t = promote(ta)
        if vb < 0 or vb >= t.bits:
            raise UB(f"shift count {vb}")
        if op == ">>":
            return t, va >> vb    # arithmetic shift: both compilers
        r = va << vb
        if t.signed:
            if va < 0 or r > t.hi:
                raise UB(f"signed {va} << {vb}")
            return t, r
        return t, wrap(r, t)
    ct = arith(ta, tb)
    a, b = wrap(va, ct), wrap(vb, ct)
    if op in CMPS:
        return I, int({"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
                       "==": a == b, "!=": a != b}[op])
    if op == "+":
        r = a + b
    elif op == "-":
        r = a - b
    elif op == "*":
        r = a * b
    elif op in ("/", "%"):
        if b == 0:
            raise UB("division by zero")
        q = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            q = -q
        if ct.signed and not ct.lo <= q <= ct.hi:
            raise UB(f"{a} {op} {b}")
        r = q if op == "/" else a - b * q
    elif op == "&":
        r = a & b
    elif op == "|":
        r = a | b
    elif op == "^":
        r = a ^ b
    else:
        raise ModelError(op)
    if ct.signed and not ct.lo <= r <= ct.hi:
        raise UB(f"signed overflow {a} {op} {b}")
    return ct, wrap(r, ct)


# ---------------------------------------------------------------- storage

class Cell:
    __slots__ = ("t", "v")

    def __init__(self, t, v):
        self.t = t
        self.v = wrap(v, t) if t is not None else v

    def set(self, v):
        self.v = wrap(v, self.t)


class Env:
    def __init__(self, limit=3_000_000):
        self.g = {}
        self.frame = {}
        self.out = []
        self.ck = 0
        self.steps = 0
        self.limit = limit

    def store(self, var):
        try:
            return self.frame[var] if var in self.frame else self.g[var]
        except KeyError:
            raise ModelError(f"no storage for {var.name}")

    def step(self):
        self.steps += 1
        if self.steps > self.limit:
            raise Hang()


# ---------------------------------------------------------------- expressions

class E:
    nonzero = False
    notmin = False
    KIDS: tuple = ()

    def slots(self):
        """(container, key) of each rvalue child, for the reducer."""
        return [(self, k) for k in self.KIDS]


def getslot(slot):
    c, k = slot
    return c[k] if isinstance(c, list) else getattr(c, k)


def setslot(slot, v):
    c, k = slot
    if isinstance(c, list):
        c[k] = v
    else:
        setattr(c, k, v)


class Const(E):
    def __init__(self, v, t=None, style=None):
        self.t = t or I
        self.v = wrap(v, self.t)
        self.lo = self.hi = self.v
        self.nonzero = self.v != 0
        self.notmin = self.v != self.t.lo
        self.style = style

    def c(self):
        return cconst(self.v, self.t, self.style)

    def ev(self, env):
        return self.v


class LV(E):
    """An lvalue: its range is the full range of its type."""

    def init_range(self):
        self.lo, self.hi = self.t.lo, self.t.hi

    def ev(self, env):
        return self.cell(env).v


class VarRef(LV):
    def __init__(self, var):
        self.var, self.t = var, var.t
        self.init_range()

    def c(self):
        return self.var.name

    def cell(self, env):
        return env.store(self.var)


class Index(LV):
    KIDS = ("idx",)

    def __init__(self, var, idx):
        self.var, self.idx, self.t = var, idx, var.t
        self.init_range()

    def c(self):
        return f"{self.var.name}[{self.idx.c()}]"

    def cell(self, env):
        i = self.idx.ev(env)
        if not 0 <= i < self.var.n:
            raise UB(f"index {i} of {self.var.name}")
        return env.store(self.var)[i]


class Member(LV):
    def __init__(self, var, field):
        self.var, self.field = var, field
        self.t = dict(var.st.fields)[field]
        self.init_range()

    def c(self):
        op = "->" if self.var.kind == "sptr" else "."
        return f"{self.var.name}{op}{self.field}"

    def cell(self, env):
        s = env.store(self.var)
        if self.var.kind == "sptr":
            s = s.v
        return s[self.field]


class Deref(LV):
    def __init__(self, var):
        self.var, self.t = var, var.t
        self.init_range()

    def c(self):
        return f"(*{self.var.name})"

    def cell(self, env):
        return env.store(self.var).v


class Unary(E):
    KIDS = ("a",)

    # The type follows the operand, also after the reducer swaps it.
    t = property(lambda self: I if self.op == "!" else promote(self.a.t),
                 lambda self, v: None)

    def __init__(self, op, a):
        self.op, self.a = op, a
        if op == "!":
            self.t, self.lo, self.hi, self.safe = I, 0, 1, True
            return
        t = self.t = promote(a.t)
        lo, hi = conv_range(a, t)
        self.safe = True
        if op == "-":
            if t.signed:
                self.safe = lo > t.lo or a.notmin
                self.lo, self.hi = max(-hi, t.lo), min(-lo, t.hi)
            elif lo == hi == 0:
                self.lo = self.hi = 0
            elif lo > 0:
                self.lo, self.hi = (1 << t.bits) - hi, (1 << t.bits) - lo
            else:
                self.lo, self.hi = t.lo, t.hi
        elif op == "~":
            self.lo, self.hi = (~hi, ~lo) if t.signed else (t.hi - hi,
                                                            t.hi - lo)
        else:
            self.lo, self.hi = lo, hi

    def c(self):
        return f"({self.op}{self.a.c()})"

    def ev(self, env):
        v = self.a.ev(env)
        if self.op == "!":
            return int(v == 0)
        t = promote(self.a.t)
        if self.op == "-":
            r = -v
            if t.signed and r > t.hi:
                raise UB(f"-{v}")
            return wrap(r, t)
        if self.op == "~":
            return wrap(~v, t)
        return v


def bin_type(op, a, b):
    if op in CMPS:
        return I
    if op in ("<<", ">>"):
        return promote(a.t)
    return arith(a.t, b.t)


class Binary(E):
    KIDS = ("a", "b")
    t = property(lambda self: bin_type(self.op, self.a, self.b),
                 lambda self, v: None)

    def __init__(self, op, a, b):
        self.op, self.a, self.b = op, a, b
        self.t, self.lo, self.hi, self.safe = self.analyze()

    def analyze(self):
        op, a, b = self.op, self.a, self.b
        if op in CMPS:
            return I, 0, 1, True
        if op in ("<<", ">>"):
            t = promote(a.t)
            al, ah = conv_range(a, t)
            bl, bh = b.lo, b.hi
            if not (bl >= 0 and bh <= t.bits - 1):
                return t, t.lo, t.hi, False
            if op == ">>":
                return t, min(al >> bl, al >> bh), max(ah >> bl, ah >> bh), True
            fits = al >= 0 and (ah << bh) <= t.hi
            if t.signed:
                return (t, al << bl, ah << bh, True) if fits else (t, t.lo, t.hi,
                                                                  False)
            return (t, al << bl, ah << bh, True) if fits else (t, t.lo, t.hi,
                                                              True)
        t = arith(a.t, b.t)
        al, ah = conv_range(a, t)
        bl, bh = conv_range(b, t)
        full = (t, t.lo, t.hi)
        if op in "+-*":
            if op == "+":
                lo, hi = al + bl, ah + bh
            elif op == "-":
                lo, hi = al - bh, ah - bl
            else:
                ps = [al * bl, al * bh, ah * bl, ah * bh]
                lo, hi = min(ps), max(ps)
            if t.lo <= lo and hi <= t.hi:
                return t, lo, hi, True
            return full + (not t.signed,)
        if op in "/%":
            nz = b.nonzero or not bl <= 0 <= bh
            safe = nz
            if t.signed and al <= t.lo and not a.notmin and bl <= -1 <= bh:
                safe = False
            if op == "/":
                if al >= 0 and bl >= 1:
                    return t, al // bh, ah // bl, safe
                if not t.signed:
                    return t, 0, ah, safe
                m = max(abs(al), abs(ah))
                return t, max(-m, t.lo), min(m, t.hi), safe
            if not t.signed:
                return t, 0, min(ah, max(bh - 1, 0)), safe
            d = max(abs(bl), abs(bh)) - 1
            if al >= 0:
                return t, 0, min(ah, d), safe
            if ah <= 0:
                return t, max(al, -d), 0, safe
            return t, max(al, -d), min(ah, d), safe
        if op == "&":
            if al >= 0 and bl >= 0:
                return t, 0, min(ah, bh), True
            if al >= 0:
                return t, 0, ah, True
            if bl >= 0:
                return t, 0, bh, True
            return full + (True,)
        if op in "|^":
            if al >= 0 and bl >= 0:
                return t, 0, (1 << max(ah, bh).bit_length()) - 1, True
            return full + (True,)
        raise ValueError(op)

    def c(self):
        return f"({self.a.c()} {self.op} {self.b.c()})"

    def ev(self, env):
        return binop(self.op, self.a.ev(env), self.a.t, self.b.ev(env),
                     self.b.t)[1]


class Logic(E):
    KIDS = ("a", "b")

    def __init__(self, op, a, b):
        self.op, self.a, self.b = op, a, b
        self.t, self.lo, self.hi = I, 0, 1

    def c(self):
        return f"({self.a.c()} {self.op} {self.b.c()})"

    def ev(self, env):
        a = self.a.ev(env) != 0
        if self.op == "&&":
            return int(a and self.b.ev(env) != 0)
        return int(a or self.b.ev(env) != 0)


class Cond(E):
    KIDS = ("k", "a", "b")
    t = property(lambda self: arith(self.a.t, self.b.t), lambda self, v: None)

    def __init__(self, k, a, b):
        self.k, self.a, self.b = k, a, b
        self.t = arith(a.t, b.t)
        r = conv_range(a, self.t) + conv_range(b, self.t)
        self.lo, self.hi = min(r), max(r)

    def c(self):
        return f"({self.k.c()} ? {self.a.c()} : {self.b.c()})"

    def ev(self, env):
        e = self.a if self.k.ev(env) else self.b
        return wrap(e.ev(env), self.t)


class Cast(E):
    KIDS = ("a",)

    def __init__(self, t, a):
        self.t, self.a = t, a
        self.lo, self.hi = conv_range(a, t)

    def c(self):
        return f"(({self.t.name}){self.a.c()})"

    def ev(self, env):
        return wrap(self.a.ev(env), self.t)


class Call(E):
    def __init__(self, fn, args):
        self.fn, self.args, self.t = fn, args, fn.ret
        self.lo, self.hi = self.t.lo, self.t.hi

    def slots(self):
        return [(self.args, i) for i in range(len(self.args))]

    def c(self):
        return f"{self.fn.name}({', '.join(a.c() for a in self.args)})"

    def ev(self, env):
        return self.fn.invoke(env, [a.ev(env) for a in self.args])


class TabCall(E):
    def __init__(self, tab, idx, args):
        self.tab, self.idx, self.args = tab, idx, args
        self.t = tab.fns[0].ret
        self.lo, self.hi = self.t.lo, self.t.hi

    def slots(self):
        return [(self, "idx")] + [(self.args, i) for i in range(len(self.args))]

    def c(self):
        return (f"{self.tab.name}[{self.idx.c()}]"
                f"({', '.join(a.c() for a in self.args)})")

    def ev(self, env):
        i = self.idx.ev(env)
        if not 0 <= i < len(self.tab.fns):
            raise UB(f"index {i} of {self.tab.name}")
        return self.tab.fns[i].invoke(env, [a.ev(env) for a in self.args])


def walk_expr(slot):
    """Yield the slot and the slots of all sub-expressions below it."""
    yield slot
    e = getslot(slot)
    for s in e.slots():
        yield from walk_expr(s)


# ---------------------------------------------------------------- statements

IND = "    "


class S:
    EXPRS: tuple = ()

    def blocks(self):
        return []

    def slots(self):
        return [(self, k) for k in self.EXPRS]


class Assign(S):
    EXPRS = ("rhs",)

    def __init__(self, lv, rhs):
        self.lv, self.rhs = lv, rhs

    def lines(self, ind):
        return [f"{ind}{self.lv.c()} = {self.rhs.c()};"]

    def ex(self, env):
        env.step()
        v = self.rhs.ev(env)
        self.lv.cell(env).set(v)


class Chain(S):
    EXPRS = ("rhs",)

    def __init__(self, lv1, lv2, rhs):
        self.lv1, self.lv2, self.rhs = lv1, lv2, rhs

    def lines(self, ind):
        return [f"{ind}{self.lv1.c()} = {self.lv2.c()} = {self.rhs.c()};"]

    def ex(self, env):
        env.step()
        c2 = self.lv2.cell(env)
        c2.set(self.rhs.ev(env))
        self.lv1.cell(env).set(c2.v)


class CAssign(S):
    EXPRS = ("rhs",)

    def __init__(self, lv, op, rhs):
        self.lv, self.op, self.rhs = lv, op, rhs

    def lines(self, ind):
        return [f"{ind}{self.lv.c()} {self.op}= {self.rhs.c()};"]

    def ex(self, env):
        env.step()
        c = self.lv.cell(env)
        _, v = binop(self.op, c.v, self.lv.t, self.rhs.ev(env), self.rhs.t)
        c.set(v)


class IncDec(S):
    def __init__(self, lv, op, post, dest=None):
        self.lv, self.op, self.post, self.dest = lv, op, post, dest

    def lines(self, ind):
        e = (f"{self.lv.c()}{self.op}" if self.post
             else f"{self.op}{self.lv.c()}")
        if self.dest is not None:
            return [f"{ind}{self.dest.c()} = {e};"]
        return [f"{ind}{e};"]

    def ex(self, env):
        env.step()
        c = self.lv.cell(env)
        old = c.v
        new = old + (1 if self.op == "++" else -1)
        t = self.lv.t
        if not t.bf and t.bits >= 16 and t.signed and not t.lo <= new <= t.hi:
            raise UB(f"{old}{self.op}")
        c.set(new)
        if self.dest is not None:
            self.dest.cell(env).set(old if self.post else c.v)


class Ck(S):
    EXPRS = ("e",)

    def __init__(self, e):
        self.e = e

    def lines(self, ind):
        return [f"{ind}ck({self.e.c()});"]

    def ex(self, env):
        env.step()
        v = wrap(self.e.ev(env), UL)
        env.ck = ((((env.ck << 5) | (env.ck >> 27)) & 0xFFFFFFFF) ^ v)


class PCk(S):
    def lines(self, ind):
        return [f"{ind}ph(ck_);"]

    def ex(self, env):
        env.step()
        env.out.append(f"{env.ck:08x}\n")


def run_block(stmts, env):
    for s in stmts:
        s.ex(env)


def block_lines(stmts, ind):
    out = []
    for s in stmts:
        out += s.lines(ind)
    return out


class If(S):
    EXPRS = ("k",)

    def __init__(self, k, then, els=None):
        self.k, self.then, self.els = k, then, els

    def blocks(self):
        return [self.then] + ([self.els] if self.els is not None else [])

    def lines(self, ind):
        out = [f"{ind}if ({self.k.c()}) {{"] + block_lines(self.then,
                                                          ind + IND)
        if self.els:
            out += [f"{ind}}} else {{"] + block_lines(self.els, ind + IND)
        return out + [f"{ind}}}"]

    def ex(self, env):
        env.step()
        if self.k.ev(env):
            run_block(self.then, env)
        elif self.els:
            run_block(self.els, env)


class Loop(S):
    """A counted loop on a loop variable that the body does not write."""

    def __init__(self, kind, var, n, body):
        self.kind, self.var, self.n, self.body = kind, var, n, body

    def blocks(self):
        return [self.body]

    def lines(self, ind):
        v, n, k = self.var.name, cconst(self.n, I), self.kind
        inner = block_lines(self.body, ind + IND)
        if k == "up":
            return [f"{ind}for ({v} = 0; {v} < {n}; {v}++) {{"] + inner + [
                f"{ind}}}"]
        if k == "down":
            return [f"{ind}for ({v} = {n}; {v} > 0; {v}--) {{"] + inner + [
                f"{ind}}}"]
        if k == "step2":
            return [f"{ind}for ({v} = 0; {v} != {n}; {v} += 2) {{"] + inner + [
                f"{ind}}}"]
        if k == "postdec":
            return [f"{ind}for ({v} = {n}; {v}-- > 0; ) {{"] + inner + [
                f"{ind}}}"]
        if k == "while":
            return ([f"{ind}{v} = {n};", f"{ind}while ({v}) {{"] + inner
                    + [f"{ind}{IND}{v}--;", f"{ind}}}"])
        return ([f"{ind}{v} = {n};", f"{ind}do {{"] + inner
                + [f"{ind}}} while (--{v});"])

    def _body(self, env):
        """Run the body once; True means a break."""
        try:
            run_block(self.body, env)
        except Brk:
            return True
        except Cont:
            if self.kind == "while":
                raise ModelError("continue in a while loop")
        return False

    def ex(self, env):
        env.step()
        c = env.store(self.var)
        k, n = self.kind, self.n
        if k == "up":
            c.set(0)
            while c.v < n:
                env.step()
                if self._body(env):
                    break
                c.set(c.v + 1)
        elif k == "down":
            c.set(n)
            while c.v > 0:
                env.step()
                if self._body(env):
                    break
                c.set(c.v - 1)
        elif k == "step2":
            c.set(0)
            while c.v != n:
                env.step()
                if self._body(env):
                    break
                c.set(c.v + 2)
        elif k == "postdec":
            c.set(n)
            while True:
                env.step()
                old = c.v
                c.set(old - 1)
                if not old > 0 or self._body(env):
                    break
        elif k == "while":
            c.set(n)
            while c.v != 0:
                env.step()
                if self._body(env):
                    break
                c.set(c.v - 1)
        else:
            c.set(n)
            while True:
                env.step()
                if self._body(env):
                    break
                c.set(c.v - 1)
                if c.v == 0:
                    break


class Seg:
    def __init__(self, labels, body, brk):
        self.labels, self.body, self.brk = labels, body, brk


class Switch(S):
    EXPRS = ("e",)

    def __init__(self, e, segs):
        self.e, self.segs = e, segs

    def blocks(self):
        return [s.body for s in self.segs]

    def lines(self, ind):
        pt = promote(self.e.t)
        out = [f"{ind}switch ({self.e.c()}) {{"]
        for s in self.segs:
            for lab in s.labels:
                out.append(f"{ind}default:" if lab is None
                           else f"{ind}case {cconst(wrap(lab, pt), pt)}:")
            out += block_lines(s.body, ind + IND)
            if s.brk:
                out.append(f"{ind}{IND}break;")
        return out + [f"{ind}}}"]

    def ex(self, env):
        env.step()
        v = wrap(self.e.ev(env), promote(self.e.t))
        start = None
        for i, s in enumerate(self.segs):
            if any(lab is not None and wrap(lab, promote(self.e.t)) == v
                   for lab in s.labels):
                start = i
                break
        if start is None:
            for i, s in enumerate(self.segs):
                if None in s.labels:
                    start = i
        if start is None:
            return
        for s in self.segs[start:]:
            try:
                run_block(s.body, env)
            except Brk:
                return
            if s.brk:
                return


class CallP(S):
    def __init__(self, fn, args):
        self.fn, self.args = fn, args

    def slots(self):
        return [(self.args, i) for i in range(len(self.args))]

    def lines(self, ind):
        return [f"{ind}{self.fn.name}({', '.join(a.c() for a in self.args)});"]

    def ex(self, env):
        env.step()
        self.fn.invoke(env, [a.ev(env) for a in self.args])


class PtrSet(S):
    def __init__(self, ptr, target):
        self.ptr, self.target = ptr, target    # target: LV, or a struct Var

    def lines(self, ind):
        t = self.target.name if isinstance(self.target, Var) else \
            self.target.c()
        return [f"{ind}{self.ptr.name} = &{t};"]

    def ex(self, env):
        env.step()
        if isinstance(self.target, Var):
            env.store(self.ptr).v = env.store(self.target)
        else:
            env.store(self.ptr).v = self.target.cell(env)


class SCopy(S):
    def __init__(self, dst, src):
        self.dst, self.src = dst, src

    def lines(self, ind):
        return [f"{ind}{self.dst.name} = {self.src.name};"]

    def ex(self, env):
        env.step()
        d, s = env.store(self.dst), env.store(self.src)
        for f in d:
            d[f].v = s[f].v


class Break(S):
    def lines(self, ind):
        return [f"{ind}break;"]

    def ex(self, env):
        raise Brk()


class Continue(S):
    def lines(self, ind):
        return [f"{ind}continue;"]

    def ex(self, env):
        raise Cont()


class Return(S):
    EXPRS = ("e",)

    def __init__(self, e, t):
        self.e, self.t = e, t

    def lines(self, ind):
        return [f"{ind}return {self.e.c()};"]

    def ex(self, env):
        env.step()
        raise Ret(wrap(self.e.ev(env), self.t))


# ---------------------------------------------------------------- declarations

class StructT:
    def __init__(self, name, fields):
        self.name, self.fields = name, fields

    def lines(self):
        out = [f"struct {self.name} {{"]
        for f, t in self.fields:
            out.append(f"{IND}{t.name} {f} : {t.bits};" if t.bf
                       else f"{IND}{t.name} {f};")
        return out + ["};"]


class Var:
    """kind: scalar, array, struct, ptr (to t), sptr (to struct st), table."""

    def __init__(self, name, kind, t=None, n=0, st=None, init=None,
                 static=False):
        self.name, self.kind, self.t, self.n, self.st = name, kind, t, n, st
        self.init, self.static = init, static
        self.fns = []

    def decl(self):
        st = "static " if self.static else ""
        k = self.kind
        if k == "scalar":
            return f"{st}{self.t.name} {self.name} = {self.init.c()};"
        if k == "array":
            vals = ", ".join(cconst(v, lit_type(self.t)) for v in self.init)
            return f"{st}{self.t.name} {self.name}[{self.n}] = {{{vals}}};"
        if k == "struct":
            vals = ", ".join(cconst(v, lit_type(t))
                             for v, (_, t) in zip(self.init, self.st.fields))
            return f"{st}struct {self.st.name} {self.name} = {{{vals}}};"
        if k == "ptr":
            return f"{st}{self.t.name} *{self.name} = &{self.init.c()};"
        if k == "sptr":
            return (f"{st}struct {self.st.name} *{self.name} = "
                    f"&{self.init.name};")
        if k == "table":
            f0 = self.fns[0]
            ps = ", ".join(p.t.name for p in f0.params) or "void"
            return (f"static {f0.ret.name} (*const {self.name}[{len(self.fns)}])"
                    f"({ps}) = {{{', '.join(f.name for f in self.fns)}}};")
        raise ValueError(k)

    def make(self, env):
        k = self.kind
        if k == "scalar":
            return Cell(self.t, self.init.ev(env))
        if k == "array":
            return [Cell(self.t, v) for v in self.init]
        if k == "struct":
            return {f: Cell(t, v) for (f, t), v in zip(self.st.fields,
                                                      self.init)}
        if k == "ptr":
            return Cell(None, self.init.cell(env))
        if k == "sptr":
            return Cell(None, env.store(self.init))
        return None


class Func:
    def __init__(self, name, ret, params, pure):
        self.name, self.ret, self.params, self.pure = name, ret, params, pure
        self.locals = []
        self.body = []
        self.tables_after = []

    def lines(self):
        ret = self.ret.name if self.ret else "void"
        ps = ", ".join(f"{p.t.name} {p.name}" for p in self.params) or "void"
        st = "" if self.name == "main" else "static "
        out = [f"{st}{ret} {self.name}({ps})", "{"]
        out += [IND + v.decl() for v in self.locals]
        out += block_lines(self.body, IND)
        return out + ["}"]

    def invoke(self, env, vals):
        frame = {p: Cell(p.t, v) for p, v in zip(self.params, vals)}
        saved, env.frame = env.frame, frame
        try:
            for v in self.locals:
                frame[v] = v.make(env)
            run_block(self.body, env)
        except Ret as r:
            return r.v
        except (Brk, Cont):
            raise ModelError("break outside a loop")
        finally:
            env.frame = saved
        if self.ret is not None:
            raise UB(f"{self.name} ends without return")
        return None


class Prog:
    def __init__(self, seed, focus, attempt):
        self.seed, self.focus, self.attempt = seed, focus, attempt
        self.structs = []
        self.globals = []
        self.funcs = []           # pure helpers and procs, callees first
        self.main = None

    def header(self):
        return (f"/* difftest seed={self.seed} focus={self.focus} "
                f"attempt={self.attempt} gen={GEN_VERSION} */")

    def text(self):
        out = [self.header(), PRELUDE]
        for s in self.structs:
            out += s.lines() + [""]
        out += [g.decl() for g in self.globals if g.kind != "table"] + [""]
        for f in self.funcs:
            out += f.lines()
            out += [t.decl() for t in f.tables_after
                    if t in self.globals] + [""]
        out += self.main.lines()
        return "\n".join(out) + "\n"

    def run_model(self, limit=3_000_000):
        env = Env(limit)
        for g in self.globals:
            if g.kind != "table":
                env.g[g] = g.make(env)
        self.main.invoke(env, [])
        return "".join(env.out)

    def all_funcs(self):
        return self.funcs + [self.main]


PRELUDE = r"""#include <stdio.h>

static unsigned long ck_;

static void ck(unsigned long v)
{
    ck_ = ((ck_ << 5) | (ck_ >> 27)) ^ v;
}

static void ph(unsigned long v)
{
    signed char i;
    unsigned char d;
    for (i = 28; i >= 0; i -= 4) {
        d = (unsigned char)(v >> i) & 15;
        putchar(d < 10 ? d + '0' : d + ('a' - 10));
    }
    putchar('\n');
}
"""


# ---------------------------------------------------------------- generator

INTERESTING = {
    id(I): [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 15, 16, 31, 32, 63, 64, 100, 127,
            128, 255, 256, 1000, 4096, 0x3FFF, 0x4000, 0x7FFE, 0x7FFF, -1, -2,
            -3, -7, -8, -16, -127, -128, -129, -255, -256, -1000, -32767,
            -32768],
    id(U): [0, 1, 2, 3, 7, 8, 15, 16, 255, 256, 0x7FFF, 0x8000, 0x8001,
            0xFF00, 0xFFFE, 0xFFFF],
    id(L): [0, 1, 2, 10, 255, 256, 65535, 65536, 0x7FFF, 0x8000, 0x10000,
            0x12345, 0x7FFFFFFF, -1, -2, -256, -65536, -32768, -32769,
            -0x80000000],
    id(UL): [0, 1, 255, 256, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000,
             0xFFFFFFFF, 0x12345678],
}
MDC = [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 16, 25, 32, 60, 64, 100, 127, 128,
       255, 256, 1000, 1024, 10000, -1, -2, -3, -4, -8, -10, -16, -128, -256]


def focus_cfg(focus):
    c = dict(
        types={CH: 1, SC: 1, UC: 1.5, I: 2, U: 2, L: 1, UL: 1},
        expr={"bin": 8, "un": 1, "cmp": 2, "logic": 1, "cond": 1, "cast": 1.5,
              "call": 1, "tab": 0.5},
        binop={"+": 3, "-": 3, "*": 2, "/": 1, "%": 1, "&": 2, "|": 2,
               "^": 1.5, "<<": 1.5, ">>": 1.5},
        leaf={"var": 6, "const": 3, "index": 1, "member": 1, "deref": 0.7},
        stmt={"assign": 6, "cassign": 3, "chain": 0.5, "incdec": 1.5, "if": 2,
              "loop": 1.5, "switch": 1, "call": 1.2, "ptr": 0.6, "scopy": 0.2,
              "brk": 0.5},
        const_p=0.3, depth=3, n_main=(15, 30), n_pure=(2, 4), n_proc=(1, 3),
        n_params=(1, 3), n_glob=(6, 12), n_arr=(1, 2), n_struct=(1, 1),
        n_ptr=(1, 2), bf_p=0.5, mdc=False, shiftc=False)
    if focus == "char-arith":
        c["types"] = {CH: 3, SC: 3, UC: 4, I: 1, U: 1, L: 0.3, UL: 0.3}
        c["expr"]["cast"] = 3
    elif focus == "const-ops":
        c["const_p"] = 0.8
    elif focus == "conditions":
        c["expr"].update(cmp=6, logic=3, cond=3, un=2)
        c["stmt"].update({"if": 6, "loop": 3, "brk": 1.5})
    elif focus == "assign":
        c["stmt"].update(assign=6, cassign=8, chain=3, incdec=2)
        c["leaf"].update(index=2, member=2, deref=2)
    elif focus == "bitfields":
        c["bf_p"] = 1.0
        c["n_struct"] = (2, 3)
        c["leaf"].update(member=6)
        c["stmt"].update(cassign=5, incdec=2)
    elif focus == "shifts":
        c["binop"].update({"<<": 8, ">>": 8})
        c["shiftc"] = True
    elif focus == "mul-div-const":
        c["binop"] = {"+": 1, "-": 1, "*": 6, "/": 6, "%": 5, "&": 0.5,
                      "|": 0.5, "^": 0.5, "<<": 0.5, ">>": 0.5}
        c["const_p"] = 0.85
        c["mdc"] = True
    elif focus == "long":
        c["types"] = {CH: 0.5, SC: 0.5, UC: 0.5, I: 1, U: 1, L: 4, UL: 4}
    elif focus == "switch":
        c["stmt"].update(switch=8)
    elif focus == "calls":
        c["expr"].update(call=5, tab=3)
        c["stmt"].update(call=5)
        c.update(n_pure=(4, 7), n_proc=(2, 4), n_params=(0, 4))
    elif focus == "incdec":
        c["stmt"].update(incdec=8, loop=3)
    elif focus == "memory":
        c["leaf"].update(index=4, member=3, deref=3)
        c["stmt"].update(ptr=3, scopy=1)
        c.update(n_arr=(2, 4), n_struct=(1, 2), n_ptr=(2, 4))
    return c


def distinct(a, b):
    """True when the lvalues a and b cannot be the same object."""
    if a is None or b is None:
        return False
    for x in (a, b):
        if isinstance(x, Deref) or (isinstance(x, Member)
                                    and x.var.kind == "sptr"):
            return False
    return a.var is not b.var


class Ctx:
    """What a statement or an expression can read, write and call."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def sub(self, **kw):
        c = copy.copy(self)
        c.__dict__.update(kw)
        return c


class Gen:
    def __init__(self, seed, focus, attempt=0):
        self.r = random.Random(f"{seed}/{focus}/{attempt}/{GEN_VERSION}")
        self.focus = focus
        self.cfg = focus_cfg(focus)
        self.count = 0
        self.prog = Prog(seed, focus, attempt)

    # helpers
    def name(self, p):
        self.count += 1
        return f"{p}{self.count}"

    def pick(self, weights):
        ks = [k for k, w in weights.items() if w > 0]
        return self.r.choices(ks, [weights[k] for k in ks])[0]

    def typ(self):
        return self.pick(self.cfg["types"])

    def rint(self, rng):
        return self.r.randint(*rng)

    def value(self, t):
        """A random value of type t (a bit-field or a scalar type)."""
        r = self.r.random()
        if r < 0.5 and not t.bf:
            pool = [v for v in INTERESTING[id(promote(t) if t.bits < 16 else t)]
                    if t.lo <= v <= t.hi]
            return self.r.choice(pool)
        if r < 0.75:
            return self.r.randint(max(t.lo, -20), min(t.hi, 20))
        return self.r.randint(t.lo, t.hi)

    def const(self, t=None, style=None):
        if t is None:
            t = self.pick({I: 5, U: 2, L: 1, UL: 1})
        if style is None:
            s = self.r.random()
            style = "hex" if s < 0.15 else "char" if s < 0.2 else None
        return Const(self.value(t), t, style)

    # ---- expressions
    def leaf(self, ctx, d=0):
        w = dict(self.cfg["leaf"])
        if not ctx.reads["scalar"]:
            w["var"] = 0
        if not ctx.reads["array"]:
            w["index"] = 0
        if not (ctx.reads["struct"] or ctx.reads["sptr"]):
            w["member"] = 0
        if not ctx.reads["ptr"]:
            w["deref"] = 0
        k = self.pick(w)
        if k == "var":
            return VarRef(self.r.choice(ctx.reads["scalar"]))
        if k == "const":
            return self.const()
        if k == "index":
            a = self.r.choice(ctx.reads["array"])
            return Index(a, self.index(ctx, a.n, d))
        if k == "member":
            v = self.r.choice(ctx.reads["struct"] + ctx.reads["sptr"])
            return Member(v, self.r.choice(v.st.fields)[0])
        return Deref(self.r.choice(ctx.reads["ptr"]))

    def index(self, ctx, n, d=0):
        if d <= 0 or self.r.random() < 0.4:
            return Const(self.r.randrange(n))
        return Binary("&", self.expr(ctx, d - 1), Const(n - 1))

    def shift_count(self, ctx, w, d):
        if self.cfg["shiftc"] or self.r.random() < 0.6:
            pool = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 14, 15, 16, 17, 23, 24,
                    31]
            return Const(self.r.choice([p for p in pool if p < w]),
                         self.r.choice([I, I, I, U, L]))
        e = self.expr(ctx, max(d - 1, 0))
        return Binary("&", e, Const(w - 1))

    def expr(self, ctx, d):
        if d <= 0 or self.r.random() < 0.2:
            return self.leaf(ctx, d)
        w = dict(self.cfg["expr"])
        if not ctx.pures:
            w["call"] = 0
        if not ctx.tabs:
            w["tab"] = 0
        k = self.pick(w)
        r = self.r
        if k == "bin":
            op = self.pick(self.cfg["binop"])
            a = self.expr(ctx, d - 1)
            if op in ("<<", ">>"):
                b = self.shift_count(ctx, promote(a.t).bits, d)
            elif r.random() < self.cfg["const_p"]:
                if self.cfg["mdc"] and op in "*/%":
                    t = arith(a.t, I)
                    v = r.choice([m for m in MDC if t.lo <= m <= t.hi])
                    b = Const(v, t if t in (I, U, L, UL) else I)
                else:
                    b = self.const()
                if r.random() < 0.2 and op not in "/%":
                    a, b = b, a
            else:
                b = self.expr(ctx, d - 1)
            return self.mk_bin(op, a, b)
        if k == "un":
            op = self.pick({"-": 3, "~": 3, "!": 2, "+": 0.5})
            return self.mk_un(op, self.expr(ctx, d - 1))
        if k == "cmp":
            op = r.choice(CMPS)
            a = self.expr(ctx, d - 1)
            b = self.const() if r.random() < 0.5 else self.expr(ctx, d - 1)
            return Binary(op, a, b)
        if k == "logic":
            return Logic(r.choice(["&&", "||"]), self.expr(ctx, d - 1),
                         self.expr(ctx, d - 1))
        if k == "cond":
            return Cond(self.cond(ctx, d - 1), self.expr(ctx, d - 1),
                        self.expr(ctx, d - 1))
        if k == "cast":
            return Cast(self.typ(), self.expr(ctx, d - 1))
        if k == "call":
            f = r.choice(ctx.pures)
            return Call(f, [self.expr(ctx, d - 1) for _ in f.params])
        tab = r.choice(ctx.tabs)
        f = tab.fns[0]
        return TabCall(tab, self.index(ctx, len(tab.fns), d - 1),
                       [self.expr(ctx, d - 1) for _ in f.params])

    def cond(self, ctx, d):
        r = self.r.random()
        if r < 0.6:
            a = self.expr(ctx, max(d, 0))
            b = self.const() if self.r.random() < 0.6 else self.expr(ctx, d)
            return Binary(self.r.choice(CMPS), a, b)
        if r < 0.75:
            return Logic(self.r.choice(["&&", "||"]), self.cond(ctx, d - 1),
                         self.cond(ctx, d - 1))
        if r < 0.85:
            return Unary("!", self.expr(ctx, d))
        return self.expr(ctx, d)

    def mk_un(self, op, a):
        n = Unary(op, a)
        if n.safe:
            return n
        t = promote(a.t)
        return Cast(t, Unary(op, Cast(unsigned_of(t), a)))

    def mk_bin(self, op, a, b):
        """a op b, with guards that make it free of undefined behaviour."""
        if op in ("<<", ">>"):
            pt = promote(a.t)
            if not (b.lo >= 0 and b.hi <= pt.bits - 1):
                b = Binary("&", b, Const(pt.bits - 1))
            n = Binary(op, a, b)
            if not n.safe:
                n = Cast(pt, Binary(op, Cast(unsigned_of(pt), a), b))
            return n
        if op in "/%":
            ct = arith(a.t, b.t)
            bl, bh = conv_range(b, ct)
            if not b.nonzero and bl <= 0 <= bh:
                b = Binary("|", b, Const(1))
                b.nonzero = True
            al, _ = conv_range(a, ct)
            bl, bh = conv_range(b, ct)
            if ct.signed and al <= ct.lo and not a.notmin and bl <= -1 <= bh:
                if self.r.random() < 0.5:
                    a = Binary("|", a, Const(1))
                    a.notmin = True
                else:
                    b = Binary("|", Cast(UC, b), Const(1))
                    b.nonzero = True
            return Binary(op, a, b)
        n = Binary(op, a, b)
        if n.safe:
            return n
        ct = arith(a.t, b.t)
        ut = unsigned_of(ct)
        return Cast(ct, Binary(op, Cast(ut, a), Cast(ut, b)))

    # ---- lvalues
    def lvalues(self, ctx, ok=None):
        """(weight, type, maker) for each lvalue that ctx can write."""
        out = []
        lw = self.cfg["leaf"]
        for v in ctx.writes["scalar"]:
            if v not in ctx.locked:
                out.append((lw["var"] / max(1, len(ctx.writes["scalar"])) * 4,
                            v.t, lambda v=v: VarRef(v)))
        for a in ctx.writes["array"]:
            out.append((lw["index"], a.t,
                        lambda a=a: Index(a, self.index(ctx, a.n, 2))))
        for s in ctx.writes["struct"] + ctx.writes["sptr"]:
            for f, t in s.st.fields:
                out.append((lw["member"] / len(s.st.fields) * 2, t,
                            lambda s=s, f=f: Member(s, f)))
        for p in ctx.writes["ptr"]:
            out.append((lw["deref"], p.t, lambda p=p: Deref(p)))
        if ok:
            out = [o for o in out if ok(o[1])]
        return out

    def lvalue(self, ctx, ok=None):
        c = self.lvalues(ctx, ok)
        if not c:
            return None
        return self.r.choices(c, [w for w, _, _ in c])[0][2]()

    # ---- statements
    def ck(self, ctx, lv):
        return [] if ctx.pure else [Ck(lv)]

    def stmt(self, ctx):
        w = dict(self.cfg["stmt"])
        if ctx.depth >= 2:
            w["if"] = w["loop"] = w["switch"] = 0
        if not ctx.loopvars:
            w["loop"] = 0
        if not ctx.procs:
            w["call"] = 0 if not ctx.pures else w["call"] * 0.3
        if not ctx.in_loop:
            w["brk"] = 0
        if ctx.pure or not ctx.writes["ptr"] and not ctx.writes["sptr"]:
            w["ptr"] = 0
        if ctx.pure or len(ctx.writes["struct"]) < 2:
            w["scopy"] = 0
        for _ in range(20):
            k = self.pick(w)
            s = getattr(self, "s_" + k)(ctx)
            if s:
                return s
        return []

    def rhs(self, ctx):
        return self.expr(ctx, self.r.randint(1, self.cfg["depth"]))

    def s_assign(self, ctx):
        lv = self.lvalue(ctx)
        if lv is None:
            return None
        return [Assign(lv, self.rhs(ctx))] + self.ck(ctx, lv)

    def s_chain(self, ctx):
        lv1 = self.lvalue(ctx)
        lv2 = self.lvalue(ctx)
        if not distinct(lv1, lv2):
            return None
        return [Chain(lv1, lv2, self.rhs(ctx))] + self.ck(ctx, lv1) + \
            self.ck(ctx, lv2)

    def s_cassign(self, ctx):
        lv = self.lvalue(ctx)
        if lv is None:
            return None
        op = self.pick(self.cfg["binop"])
        if op in ("<<", ">>"):
            rhs = self.shift_count(ctx, promote(lv.t).bits, 2)
        elif self.r.random() < self.cfg["const_p"]:
            if self.cfg["mdc"] and op in "*/%":
                t = arith(lv.t, I)
                rhs = Const(self.r.choice([m for m in MDC
                                           if t.lo <= m <= t.hi]),
                            t if t in (I, U, L, UL) else I)
            else:
                rhs = self.const()
        else:
            rhs = self.rhs(ctx)
        n = self.mk_bin(op, lv, rhs)
        if op in "+-*" and not (isinstance(n, Binary) and n.a is lv):
            for t in (SC, UC):
                n2 = Binary(op, lv, Cast(t, rhs))
                if n2.safe:
                    n = n2
                    break
        if isinstance(n, Binary) and n.op == op and n.a is lv:
            return [CAssign(lv, op, n.b)] + self.ck(ctx, lv)
        return [Assign(lv, n)] + self.ck(ctx, lv)

    def s_incdec(self, ctx):
        lv = self.lvalue(ctx, lambda t: not t.signed or t in CHARS)
        if lv is None:
            return None
        op = self.r.choice(["++", "--"])
        post = self.r.random() < 0.5
        dest = None
        if self.r.random() < 0.5:
            dest = self.lvalue(ctx)
            if not distinct(dest, lv):
                dest = None
        out = [IncDec(lv, op, post, dest)] + self.ck(ctx, lv)
        if dest is not None:
            out += self.ck(ctx, dest)
        return out

    def block(self, ctx, n):
        out = []
        for _ in range(n):
            out += self.stmt(ctx)
        return out

    def s_if(self, ctx):
        c = ctx.sub(depth=ctx.depth + 1)
        then = self.block(c, self.r.randint(1, 3))
        els = self.block(c, self.r.randint(1, 2)) if self.r.random() < 0.5 \
            else None
        return [If(self.cond(ctx, 2), then, els)]

    def s_loop(self, ctx):
        var = self.r.choice(ctx.loopvars)
        kind = self.pick({"up": 3, "down": 2, "step2": 1, "postdec": 1.5,
                          "while": 1.5, "do": 1})
        n = self.r.randint(1, 6)
        if kind == "step2":
            n = 2 * self.r.randint(1, 3)
        c = ctx.sub(depth=ctx.depth + 1, in_loop=True,
                    cont_ok=kind != "while",
                    locked=ctx.locked | {var},
                    loopvars=[v for v in ctx.loopvars if v is not var],
                    writes=dict(ctx.writes, scalar=[
                        v for v in ctx.writes["scalar"] if v is not var]))
        body = self.block(c, self.r.randint(1, 3))
        out = [Loop(kind, var, n, body)]
        if not ctx.pure:
            out.append(Ck(VarRef(var)))
        return out

    def s_brk(self, ctx):
        s = Continue() if ctx.cont_ok and self.r.random() < 0.4 else Break()
        return [If(self.cond(ctx, 1), [s])]

    def s_switch(self, ctx):
        r = self.r
        e = self.expr(ctx, r.randint(0, 2))
        pt = promote(e.t)
        lo, hi = conv_range(e, pt)
        if r.random() < 0.5 or max(lo, -100000) > min(hi, 100000):
            m = r.choice([3, 7, 15])
            e = Binary("&", e, Const(m))
            pool = list(range(m + 1)) + [m + 1, m + 5]
        else:
            lo, hi = conv_range(e, pt)
            # SDCC 4.6 takes minutes for a long switch whose case values
            # span more than about 2^31, so keep the span small.
            lo, hi = max(lo, -100000), min(hi, 100000)
            pool = [v for v in INTERESTING[id(pt)] if lo <= v <= hi]
            pool += [r.randint(lo, hi) for _ in range(4)]
            if max(lo, -10) <= min(hi, 10):
                pool += [r.randint(max(lo, -10), min(hi, 10))
                         for _ in range(4)]
        pt = promote(e.t)
        vals = list(dict.fromkeys(wrap(v, pt) for v in pool))
        r.shuffle(vals)
        nseg = r.randint(2, 6)
        segs = []
        c = ctx.sub(depth=ctx.depth + 1, in_loop=True, cont_ok=False)
        c.in_loop = True       # break leaves the switch
        for i in range(nseg):
            labs = [vals.pop() for _ in range(r.randint(1, 2)) if vals]
            if not labs:
                break
            segs.append(Seg(labs, self.block(c, r.randint(0, 2)),
                            r.random() < 0.75))
        if r.random() < 0.6 and segs:
            r.choice(segs).labels.append(None)
        if segs and not segs[-1].body:
            segs[-1].brk = True      # no label at the end of the block
        return [Switch(e, segs)]

    def s_call(self, ctx):
        if ctx.procs and self.r.random() < 0.8:
            f = self.r.choice(ctx.procs)
            return [CallP(f, [self.expr(ctx, 2) for _ in f.params])]
        if not ctx.pures:
            return None
        lv = self.lvalue(ctx)
        if lv is None:
            return None
        f = self.r.choice(ctx.pures)
        return [Assign(lv, Call(f, [self.expr(ctx, 2) for _ in f.params]))] \
            + self.ck(ctx, lv)

    def s_ptr(self, ctx):
        r = self.r
        cands = ctx.writes["ptr"] + ctx.writes["sptr"]
        if not cands:
            return None
        p = r.choice(cands)
        if p.kind == "sptr":
            ss = [s for s in ctx.gstructs if s.st is p.st]
            return [PtrSet(p, r.choice(ss)), Ck(Member(p, p.st.fields[0][0]))]
        tg = self.ptr_targets(p.t, ctx, dyn=True)
        return [PtrSet(p, r.choice(tg)), Ck(Deref(p))]

    def s_scopy(self, ctx):
        ss = ctx.writes["struct"]
        a = self.r.choice(ss)
        same = [s for s in ss if s.st is a.st and s is not a]
        if not same:
            return None
        b = self.r.choice(same)
        return [SCopy(a, b)] + [Ck(Member(a, f)) for f, _ in a.st.fields]

    def ptr_targets(self, t, ctx, dyn=False):
        out = [VarRef(v) for v in ctx.gscalars if v.t is t]
        for a in ctx.garrays:
            if a.t is t:
                for _ in range(2):
                    idx = (self.index(ctx, a.n, 2) if dyn
                           else Const(self.r.randrange(a.n)))
                    out.append(Index(a, idx))
        return out

    # ---- program
    def struct_type(self, i):
        fs = []
        for _ in range(self.r.randint(1, 3)):
            fs.append((self.name("m"), self.typ()))
        if self.r.random() < self.cfg["bf_p"]:
            for _ in range(self.r.randint(2, 5)):
                s = self.r.random() < 0.4
                w = self.r.randint(1, 15) if self.r.random() < 0.9 else 16
                fs.append((self.name("b"), bitfield(w, s)))
            self.r.shuffle(fs)
        return StructT(f"S{i}", fs)

    def generate(self):
        r, cfg, P = self.r, self.cfg, self.prog
        P.structs = [self.struct_type(i) for i in range(self.rint(cfg["n_struct"]))]
        gs, ga, gst, gp, gsp = [], [], [], [], []
        for _ in range(self.rint(cfg["n_glob"])):
            t = self.typ()
            ct = t if t in (I, U, L, UL) else I
            init = Const(self.value(t), ct) if t not in CHARS else \
                Const(self.value(t), I)
            gs.append(Var(self.name("g"), "scalar", t, init=init,
                          static=r.random() < 0.5))
        for _ in range(self.rint(cfg["n_arr"])):
            t = self.typ()
            n = r.choice([2, 4, 8])
            ga.append(Var(self.name("ga"), "array", t, n=n,
                          init=[self.value(t) for _ in range(n)],
                          static=r.random() < 0.5))
        for st in P.structs:
            for _ in range(r.randint(1, 2)):
                gst.append(Var(self.name("gs"), "struct", st=st,
                               init=[self.value(t) for _, t in st.fields],
                               static=r.random() < 0.5))
            if r.random() < 0.6:
                gsp.append(Var(self.name("gsp"), "sptr", st=st,
                               init=r.choice([s for s in gst if s.st is st])))
        base = Ctx(gscalars=gs, garrays=ga, gstructs=gst)
        for _ in range(self.rint(cfg["n_ptr"])):
            t = r.choice([v.t for v in gs + ga])
            tg = self.ptr_targets(t, base)
            gp.append(Var(self.name("gp"), "ptr", t, init=r.choice(tg),
                          static=r.random() < 0.5))
        P.globals = gs + ga + gst + gsp + gp
        greads = {"scalar": gs, "array": ga, "struct": gst, "sptr": gsp,
                  "ptr": gp}

        # pure helpers, with shared signatures for the tables
        npure = self.rint(cfg["n_pure"])
        pool = [(self.typ(), [self.typ() for _ in range(self.rint(cfg["n_params"]))])
                for _ in range(max(1, (npure + 1) // 2))]
        sigs = [r.choice(pool) for _ in range(npure)]
        pures = []
        for i, (ret, pts) in enumerate(sigs):
            ps = [Var(self.name("p"), "scalar", t) for t in pts]
            f = Func(self.name("f"), ret, ps, True)
            tabs = [g for g in P.globals if g.kind == "table"]
            self.body(f, greads, writes_g=False, pures=list(pures),
                      procs=[], tabs=tabs)
            pures.append(f)
            P.funcs.append(f)
            same = [g for g in pures if (g.ret, [p.t for p in g.params]) ==
                    (ret, pts)]
            if len(same) >= 2 and r.random() < 0.7:
                n = 2 if len(same) == 2 else 4
                tab = Var(self.name("tb"), "table")
                tab.fns = [r.choice(same) for _ in range(n)]
                f.tables_after.append(tab)
                P.globals.append(tab)
        tabs = [g for g in P.globals if g.kind == "table"]
        procs = []
        for j in range(self.rint(cfg["n_proc"])):
            ps = [Var(self.name("q"), "scalar", self.typ())
                  for _ in range(r.randint(0, 2))]
            f = Func(self.name("pr"), None, ps, False)
            self.body(f, greads, writes_g=True, pures=pures,
                      procs=list(procs), tabs=tabs)
            procs.append(f)
            P.funcs.append(f)
        m = Func("main", I, [], False)
        self.body(m, greads, writes_g=True, pures=pures, procs=procs,
                  tabs=tabs, main=True)
        P.main = m
        return P

    def body(self, f, greads, writes_g, pures, procs, tabs, main=False):
        r, cfg = self.r, self.cfg
        reads = {k: list(v) for k, v in greads.items()}
        reads["scalar"] += f.params
        empty = {k: [] for k in greads}
        writes = {k: list(v) for k, v in (greads if writes_g else empty).items()}
        writes["scalar"] += f.params
        ctx = Ctx(pure=f.pure, reads=reads, writes=writes, pures=pures,
                  procs=procs, tabs=tabs, locked=frozenset(), loopvars=[],
                  in_loop=False, cont_ok=False, depth=0,
                  gscalars=greads["scalar"], garrays=greads["array"],
                  gstructs=greads["struct"])
        nloc = r.randint(3, 7) if main else r.randint(0, 3)
        for _ in range(nloc):
            t = self.typ()
            v = Var(self.name("l"), "scalar", t,
                    init=self.expr(ctx, r.randint(0, 2)))
            f.locals.append(v)
            reads["scalar"].append(v)
            writes["scalar"].append(v)
        if main and r.random() < 0.5:
            t = self.typ()
            n = r.choice([2, 4, 8])
            a = Var(self.name("la"), "array", t, n=n,
                    init=[self.value(t) for _ in range(n)])
            f.locals.append(a)
            reads["array"].append(a)
            writes["array"].append(a)
        if main and self.prog.structs and r.random() < 0.5:
            st = r.choice(self.prog.structs)
            s = Var(self.name("ls"), "struct", st=st,
                    init=[self.value(t) for _, t in st.fields])
            f.locals.append(s)
            reads["struct"].append(s)
            writes["struct"].append(s)
        nlv = r.randint(1, 3) if (main or not f.pure) else r.randint(0, 1)
        for _ in range(nlv):
            t = r.choice(SCALARS)
            v = Var(self.name("i"), "scalar", t, init=Const(0))
            f.locals.append(v)
            ctx.loopvars.append(v)
            reads["scalar"].append(v)
        if main:
            n = self.rint(cfg["n_main"])
            for _ in range(n):
                f.body += self.stmt(ctx) + [PCk()]
            dump = []
            for v in self.prog.globals + f.locals:
                if v.kind == "scalar":
                    dump.append(Ck(VarRef(v)))
                elif v.kind == "array":
                    dump += [Ck(Index(v, Const(i))) for i in range(v.n)]
                elif v.kind == "struct":
                    dump += [Ck(Member(v, fl)) for fl, _ in v.st.fields]
                elif v.kind == "ptr":
                    dump.append(Ck(Deref(v)))
            f.body += dump + [PCk(), Return(Const(0), I)]
            return
        n = r.randint(1, 4) if f.pure else r.randint(1, 5)
        f.body += self.block(ctx, n)
        if f.ret is not None:
            f.body.append(Return(self.expr(ctx, cfg["depth"]), f.ret))


def generate(seed, focus, max_attempts=50):
    """A program and its model output.  Tries the next attempt on UB."""
    last = None
    for attempt in range(max_attempts):
        p = Gen(seed, focus, attempt).generate()
        try:
            out = p.run_model()
        except (UB, Hang, ModelError) as e:
            last = e
            continue
        return p, out
    raise RuntimeError(f"seed {seed}: no UB-free program in {max_attempts} "
                       f"attempts ({last})")


# ---------------------------------------------------------------- build and run

class Res:
    def __init__(self, status, out="", log="", cycles=None, image=None):
        self.status, self.out, self.log = status, out, log
        self.cycles, self.image = cycles, image


def compilers(opts):
    return {"uc80": Uc80(),
            "sdcc": Sdcc(extra_flags=tuple(opts.sdcc_flags.split()),
                         allocs=opts.sdcc_allocs,
                         timeout=int(opts.build_timeout))}


def build_run(comp, src: Path, work: Path, opts, mode, timeout=None) -> Res:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    b = comp.build([src.resolve()], [], [], work, mode)
    if not b.ok:
        if "timeout after" in b.log:
            return Res("build-timeout", log=b.log)
        return Res("build-fail", log=b.log)
    out, cyc = run_ticks(b.com, timeout=timeout or opts.timeout)
    if cyc is None:
        return Res("hang", out=out, log=b.log)
    return Res("ok", normalize(out), b.log, cyc, b.image)


def classify(u: Res, s: Res, model: str) -> str:
    if u.status != "ok":
        return f"UC80-{u.status.upper()}"
    if s.status != "ok":
        return f"SDCC-{s.status.upper()}"
    uo, so = u.out == model, s.out == model
    if uo and so:
        return "PASS"
    if so:
        return "UC80-WRONG"
    if uo:
        return "SDCC-WRONG"
    if u.out == s.out:
        return "MODEL-DIFF"
    return "ALL-DIFF"


def first_diff(a: str, b: str):
    al, bl = a.splitlines(), b.splitlines()
    for i in range(max(len(al), len(bl))):
        if i >= len(al) or i >= len(bl) or al[i] != bl[i]:
            return i + 1
    return None


def run_one(seed, focus, opts):
    """Worker: make, build and run one program.  Returns a dict."""
    t0 = time.time()
    try:
        prog, model = generate(seed, focus)
    except RuntimeError as e:
        return dict(seed=seed, focus=focus, status="GEN-FAIL", note=str(e))
    model = normalize(model)
    work = Path(opts.work) / f"s{seed}-{focus}"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "prog.c"
    src.write_text(prog.text())
    comps = compilers(opts)
    u = build_run(comps["uc80"], src, work / "uc80", opts, "size")
    s = build_run(comps["sdcc"], src, work / "sdcc", opts, opts.sdcc_mode)
    st = classify(u, s, model)
    res = dict(seed=seed, focus=focus, status=st, secs=time.time() - t0,
               uc80_cycles=u.cycles, sdcc_cycles=s.cycles,
               uc80_image=u.image, sdcc_image=s.image,
               lines=len(model.splitlines()))
    if st != "PASS":
        if u.status == "ok" and s.status == "ok":
            res["line"] = first_diff(u.out, s.out if st != "UC80-WRONG"
                                     else model)
        if opts.keep_failures:
            keep = Path(opts.keep_failures) / f"s{seed}-{focus}"
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, keep / "prog.c")
            (keep / "model.out").write_text(model)
            for n, rr in (("uc80", u), ("sdcc", s)):
                (keep / f"{n}.out").write_text(rr.out)
                (keep / f"{n}.log").write_text(rr.log)
            res["kept"] = str(keep)
    if not opts.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return res


# ---------------------------------------------------------------- reducer

class Checker:
    """Is a candidate still interesting?  Keeps the class of the failure."""

    def __init__(self, kind, opts, work: Path, log_first=None):
        self.kind, self.opts, self.work = kind, opts, work
        self.comps = compilers(opts)
        self.calls = 0
        self.cache = {}
        self.log_first = log_first   # first error line of a build failure

    def build(self, name, text):
        self.work.mkdir(parents=True, exist_ok=True)
        src = self.work / "cand.c"
        src.write_text(text)
        mode = "size" if name == "uc80" else self.opts.sdcc_mode
        # A normal program runs in well under a second, so a short limit
        # keeps the reduction of a hang fast.
        return build_run(self.comps[name], src, self.work / name, self.opts,
                         mode, getattr(self.opts, "reduce_timeout", 10))

    def model_of(self, prog):
        try:
            return normalize(prog.run_model(limit=500_000))
        except (UB, Hang, ModelError, RecursionError):
            return None

    def __call__(self, prog=None, text=None):
        if prog is not None and text is None:
            text = prog.text()
        if text in self.cache:
            return self.cache[text]
        self.calls += 1
        model = None
        if prog is not None:
            model = self.model_of(prog)
            if model is None:
                self.cache[text] = False
                return False
        r = self.cache[text] = self.decide(text, model)
        return r

    def decide(self, text, model):
        k = self.kind
        if k == "LINES":
            u = self.build("uc80", text)
            if u.status != "ok":
                return False
            s = self.build("sdcc", text)
            return s.status == "ok" and s.out != u.out
        strict = getattr(self.opts, "reduce_strict", False)
        if k in ("UC80-BUILD-FAIL",):
            u = self.build("uc80", text)
            if u.status != "build-fail":
                return False
            if self.log_first and self.log_first not in u.log:
                return False
            return not strict or self.build("sdcc", text).status == "ok"
        if k == "UC80-HANG":
            u = self.build("uc80", text)
            if u.status != "hang":
                return False
            return not strict or self.build("sdcc", text).status == "ok"
        if k in ("SDCC-BUILD-FAIL", "SDCC-HANG"):
            s = self.build("sdcc", text)
            want = "build-fail" if k == "SDCC-BUILD-FAIL" else "hang"
            if s.status != want:
                return False
            if self.log_first and self.log_first not in s.log:
                return False
            return self.build("uc80", text).status == "ok"
        if k == "SDCC-WRONG":
            s = self.build("sdcc", text)
            if s.status != "ok" or s.out == model:
                return False
            u = self.build("uc80", text)
            return u.status == "ok" and u.out == model
        u = self.build("uc80", text)
        if u.status != "ok":
            return False
        if k in ("UC80-WRONG", "ALL-DIFF"):
            # The model is the reference.  Without --reduce-strict, SDCC
            # builds only once, for the report on the reduced program.
            if u.out == model:
                return False
            if not strict:
                return True
            s = self.build("sdcc", text)
            if k == "UC80-WRONG":
                return s.status == "ok" and s.out == model
            return s.status == "ok" and s.out != u.out
        if k == "MODEL-DIFF":
            if u.out == model:
                return False
            s = self.build("sdcc", text)
            return s.status == "ok" and s.out == u.out
        raise ValueError(k)


def stmt_lists(prog):
    """Every list of statements in the program, outer lists first."""
    out = []

    def rec(lst):
        out.append(lst)
        for s in lst:
            for b in s.blocks():
                rec(b)
    for f in prog.all_funcs():
        rec(f.body)
    return out


def expr_slots(prog):
    out = []
    for f in prog.all_funcs():
        for v in f.locals:
            if v.kind == "scalar":
                out += list(walk_expr((v, "init")))
        for lst in stmt_lists_of(f.body):
            for s in lst:
                for sl in s.slots():
                    out += list(walk_expr(sl))
    return out


def stmt_lists_of(body):
    out = [body]
    for s in body:
        for b in s.blocks():
            out += stmt_lists_of(b)
    return out


def names_used(text, name):
    return len(re.findall(rf"\b{re.escape(name)}\b", text))


def drop_unused(prog):
    """Remove declarations that nothing names.  True when one went."""
    changed, any_change = True, False
    while changed:
        changed = False
        text = prog.text()
        for g in list(prog.globals):
            if names_used(text, g.name) == 1:
                prog.globals.remove(g)
                changed = True
        for f in list(prog.funcs):
            if names_used(text, f.name) == 1:
                j = prog.funcs.index(f)
                tabs = [t for t in f.tables_after if t in prog.globals]
                if tabs:
                    if j == 0:
                        continue
                    prog.funcs[j - 1].tables_after += tabs
                prog.funcs.remove(f)
                changed = True
        for f in prog.all_funcs():
            for v in list(f.locals):
                if names_used(text, v.name) == 1:
                    f.locals.remove(v)
                    changed = True
        used = [s for s in prog.structs if names_used(text, s.name) > 1]
        if len(used) != len(prog.structs):
            prog.structs = used
            changed = True
        any_change |= changed
    return any_change


def reduce_ast(prog, check, budget, say):
    """Delete statements, flatten blocks, simplify expressions."""
    cur = [prog.text()]

    def ok(p):
        if check.calls >= budget:
            return False
        t = p.text()
        if t == cur[0]:
            return False         # a change in a detached part of the tree
        if check(prog=p, text=t):
            cur[0] = t
            return True
        return False

    def live(lst):
        return any(lst is l for l in stmt_lists(prog))

    progress = True
    while progress and check.calls < budget:
        progress = False
        # 1. delete chunks of statements, big chunks first
        for lst in stmt_lists(prog):
            if not live(lst):
                continue
            size = max(1, len(lst) // 2)
            while size >= 1 and check.calls < budget:
                i = 0
                while i < len(lst) and check.calls < budget:
                    chunk = lst[i:i + size]
                    if any(isinstance(s, Return) for s in chunk):
                        i += size
                        continue
                    del lst[i:i + size]
                    if ok(prog):
                        progress = True
                        say(f"  -{len(chunk)} stmt  ({check.calls} tries)")
                    else:
                        lst[i:i] = chunk
                        i += size
                size //= 2
        # 2. replace a compound statement with one of its blocks
        for lst in stmt_lists(prog):
            if not live(lst):
                continue
            i = 0
            while i < len(lst) and check.calls < budget:
                s = lst[i]
                for b in s.blocks():
                    lst[i:i + 1] = b
                    if ok(prog):
                        progress = True
                        say(f"  flatten  ({check.calls} tries)")
                        break
                    lst[i:i + len(b)] = [s]
                else:
                    i += 1
        # 3. replace an expression with a child or a constant
        i = 0
        slots = expr_slots(prog)
        while i < len(slots) and check.calls < budget:
            slot = slots[i]
            i += 1
            e = getslot(slot)
            if isinstance(e, (Const, VarRef)):
                continue
            for c in [getslot(s) for s in e.slots()] + [Const(0), Const(1)]:
                setslot(slot, c)
                if ok(prog):
                    progress = True
                    say(f"  simplify  ({check.calls} tries)")
                    slots = expr_slots(prog)
                    i -= 1
                    break
                setslot(slot, e)
        # 4. drop unused declarations
        trial = copy.deepcopy(prog)
        if drop_unused(trial) and ok(trial):
            prog = trial
            progress = True
            say(f"  drop unused  ({check.calls} tries)")
    return prog


def reduce_lines(text, check, budget, say):
    lines = text.splitlines()
    body0 = 0          # keep the prelude (ck and ph) of a generated file
    for i, l in enumerate(lines):
        if l.startswith("static void ph("):
            body0 = next(j for j in range(i, len(lines))
                         if lines[j] == "}") + 1

    def ok(ls):
        if check.calls >= budget:
            return False
        return check(text="\n".join(ls) + "\n")

    def blocks(ls):
        out = []
        for i in range(body0, len(ls)):
            l = ls[i].strip()
            if l.startswith("return") or not l or l in ("{", "}"):
                continue
            if l.endswith("{"):
                d = 0
                for j in range(i, len(ls)):
                    d += ls[j].count("{") - ls[j].count("}")
                    if d == 0:
                        break
                out.append((i, j + 1))
            elif l.endswith(";") or l.endswith(":"):
                out.append((i, i + 1))
        return out

    progress = True
    while progress and check.calls < budget:
        progress = False
        for a, b in sorted(blocks(lines), key=lambda x: x[0] - x[1]):
            if b > len(lines):
                continue
            cand = lines[:a] + lines[b:]
            if ok(cand):
                lines = cand
                progress = True
                say(f"  -{b - a} lines  ({check.calls} tries)")
                break
    return "\n".join(lines) + "\n"


def parse_header(text):
    m = re.match(r"/\* difftest seed=(\d+) focus=(\S+) attempt=(\d+) "
                 r"gen=(\d+) \*/", text)
    if not m or int(m.group(4)) != GEN_VERSION:
        return None
    return int(m.group(1)), m.group(2), int(m.group(3))


def reduce_file(path: Path, opts, kind=None, say=print):
    """Reduce one program; write <stem>.reduced.c next to it."""
    text = path.read_text()
    work = Path(opts.work) / f"reduce-{path.parent.name}-{os.getpid()}"
    hdr = parse_header(text)
    prog = None
    if hdr:
        p = Gen(*hdr).generate()
        if p.text() == text:
            prog = p
    comps = compilers(opts)
    src = work / "orig.c"
    work.mkdir(parents=True, exist_ok=True)
    src.write_text(text)
    u = build_run(comps["uc80"], src, work / "uc80", opts, "size")
    s = build_run(comps["sdcc"], src, work / "sdcc", opts, opts.sdcc_mode)
    if prog is not None:
        model = normalize(prog.run_model())
        kind = kind or classify(u, s, model)
    else:
        kind = "LINES" if u.status == s.status == "ok" and u.out != s.out \
            else None
    if kind in (None, "PASS"):
        say(f"{path}: nothing to reduce (uc80 {u.status}, sdcc {s.status}, "
            f"class {kind})")
        return None
    first = None
    if kind.endswith("BUILD-FAIL"):
        log = u.log if kind.startswith("UC80") else s.log
        errs = [l for l in log.splitlines() if "rror" in l]
        if errs:
            first = re.sub(r"^.*?:\d+:(\d+:)?\s*", "", errs[0]).strip()[:60]
    check = Checker(kind, opts, work, first)
    say(f"{path}: {kind}, {'syntax tree' if prog else 'line'} reducer")
    if prog is not None:
        prog = reduce_ast(prog, check, opts.reduce_budget, say)
        out = prog.text()
    else:
        out = reduce_lines(text, check, opts.reduce_budget, say)
    dst = path.with_name(path.stem + ".reduced.c")
    dst.write_text(out)
    # outputs of the reduced program, for the report
    src.write_text(out)
    u = build_run(comps["uc80"], src, work / "uc80", opts, "size")
    s = build_run(comps["sdcc"], src, work / "sdcc", opts, opts.sdcc_mode)
    notes = [f"class: {kind}", f"uc80: {u.status}", u.out,
             f"sdcc: {s.status}", s.out]
    if prog is not None:
        notes += ["model:", normalize(prog.run_model())]
    dst.with_suffix(".txt").write_text("\n".join(notes))
    if not opts.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    say(f"{dst}: {len(out.splitlines())} lines after {check.calls} tries")
    return dst


def _reduce_job(path, opts):
    lines = []
    try:
        dst = reduce_file(path, opts, say=lines.append)
    except Exception as e:          # report and go on with the others
        return path, None, lines + [f"reducer error: {e!r}"]
    return path, dst, lines


def reduce_many(paths, opts):
    """Reduce each file; with one file, show the progress as it goes."""
    if len(paths) == 1:
        reduce_file(paths[0], opts)
        return
    with ProcessPoolExecutor(opts.jobs) as ex:
        futs = [ex.submit(_reduce_job, p, opts) for p in paths]
        for fut in as_completed(futs):
            path, dst, lines = fut.result()
            print(lines[0] if lines else path)
            print(lines[-1] if dst else "\n".join(lines[-2:]), flush=True)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="focus areas: " + ", ".join(FOCI))
    ap.add_argument("--seed", type=int, default=1,
                    help="first seed (default 1); program i has seed+i")
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--focus", default="rotate",
                    help="a comma list of focus areas, 'rotate' (each "
                    "program takes the next area) or 'all'")
    ap.add_argument("-j", "--jobs", type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--keep-failures", metavar="DIR")
    ap.add_argument("--reduce-failures", action="store_true",
                    help="after the run, reduce each kept failure")
    ap.add_argument("--reduce", metavar="FILE", nargs="+",
                    help="reduce these programs (-j at a time), then stop")
    ap.add_argument("--reduce-strict", action="store_true",
                    help="while it reduces a uc80 failure, also build with "
                    "SDCC and keep the SDCC result the same")
    ap.add_argument("--reduce-budget", type=int, default=800,
                    help="most candidate builds for one reduction")
    ap.add_argument("--print", type=int, metavar="SEED",
                    help="print the program of SEED (with --focus) and stop")
    ap.add_argument("--model", action="store_true",
                    help="with --print: print the model output instead")
    ap.add_argument("--work", default=str(BENCH / "_work" / "difftest"))
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--timeout", type=float, default=30,
                    help="seconds for one run under z88dk-ticks")
    ap.add_argument("--reduce-timeout", type=float, default=10,
                    help="seconds for one run while the reducer works")
    ap.add_argument("--build-timeout", type=float, default=120,
                    help="seconds for one SDCC compile")
    ap.add_argument("--sdcc-allocs", type=int, default=3000,
                    help="SDCC --max-allocs-per-node (default 3000)")
    ap.add_argument("--sdcc-mode", choices=["size", "speed"], default="size")
    ap.add_argument("--sdcc-flags", default="--fsigned-char --nogenconstprop",
                    help="more SDCC flags (default: %(default)s); SDCC 4.6 "
                    "generalized constant propagation has bugs")
    ap.add_argument("-v", "--verbose", action="store_true")
    opts = ap.parse_args()

    if opts.focus == "rotate":
        foci = FOCI
    else:
        foci = opts.focus.split(",")
        for f in foci:
            if f not in FOCI:
                ap.error(f"unknown focus {f!r}; use one of {', '.join(FOCI)}")

    if opts.print is not None:
        prog, model = generate(opts.print, foci[0])
        sys.stdout.write(model if opts.model else prog.text())
        return 0
    if not ticks_path().exists():
        raise SystemExit(f"z88dk-ticks not found at {ticks_path()}")
    if opts.reduce:
        reduce_many([Path(f) for f in opts.reduce], opts)
        return 0
    if opts.reduce_failures and not opts.keep_failures:
        ap.error("--reduce-failures needs --keep-failures DIR")

    jobs = [(opts.seed + i, foci[i % len(foci)]) for i in range(opts.count)]
    results = []
    t0 = time.time()
    with ProcessPoolExecutor(opts.jobs) as ex:
        futs = {ex.submit(run_one, s, f, opts): (s, f) for s, f in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
            except Exception as e:      # a crash in the tester itself
                s, f = futs[fut]
                res = dict(seed=s, focus=f, status="TESTER-ERROR",
                           note=repr(e))
            results.append(res)
            if res["status"] != "PASS" or opts.verbose:
                extra = f"\tline {res['line']}" if res.get("line") else ""
                note = f"\t{res['note']}" if res.get("note") else ""
                print(f"{res['seed']}\t{res['focus']}\t{res['status']}"
                      f"{extra}{note}", flush=True)
            elif n % 50 == 0:
                print(f"... {n}/{len(jobs)} done, "
                      f"{time.time() - t0:.0f}s", flush=True)
    results.sort(key=lambda r: r["seed"])
    tally = {}
    for r in results:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    print(f"\n{len(results)} programs in {time.time() - t0:.0f}s")
    for k in sorted(tally):
        print(f"{k}: {tally[k]}")
    per = {}
    for r in results:
        d = per.setdefault(r["focus"], {})
        d[r["status"]] = d.get(r["status"], 0) + 1
    for f in foci:
        if f in per:
            print(f"  {f}: " + ", ".join(f"{k} {v}" for k, v in
                                          sorted(per[f].items())))
    bad = [r for r in results if r["status"] != "PASS"]
    if opts.reduce_failures:
        # SDCC failures stay in the keep directory, but only a failure
        # that involves uc80 gets the (slow) reduction.
        reduce_many([Path(r["kept"]) / "prog.c" for r in bad
                     if r.get("kept") and not r["status"].startswith("SDCC-")],
                    opts)
    mism = sum(1 for r in bad if r["status"] in ("UC80-WRONG", "ALL-DIFF")
               or r["status"].startswith("UC80-"))
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main())
