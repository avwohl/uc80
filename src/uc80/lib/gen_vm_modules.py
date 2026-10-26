#!/usr/bin/env python3
"""Compile the math programs in vmsrc/ to library modules in lc/.

Each vmsrc/lc_NAME.vc becomes lc/lc_NAME.mac.  A program is a function in a
small subset of C; this script turns it into a byte program for the float
interpreter of lc/lc_vm.mac (docs/libm_vm.md describes the machine).  The
generated .mac files are checked in, so a normal library build needs only
um80 and ulib80.  Run this script after you edit a file in vmsrc/:

    python3 gen_vm_modules.py            # all modules
    python3 gen_vm_modules.py lc_sin     # one module

Language of a .vc file (all values are floats):

    static const float NAME[] = { 1.0f, 2.0f };      a table of constants
    /* ALIAS: sin = sinf sinl */                     more C entry points
    float sin(float x) { ... }       a function that C code can call
    sub   NAME(float a, float b)     a program that only other programs call
    sub2  NAME(float a)              a program with two results (sub3: three)
    static sub NAME(...)             a program private to the module

    extern vm  float F(float);           a program of another module
    extern vm  float2 F(float, float);   the same, with two results
    extern rtn float R(float);           an assembly routine, DEHL = the
                                         argument and the result
    extern rtn float R(float, float);    DEHL = a, (__tmp32) = b
    extern rtn float2 R(float);          the results in DEHL and (__tmp32)
    extern rtn float R(float) = LABEL;   the routine has another label

Statements:

    float x, y = a * b;       locals (at the top level of the body only)
    x = y + 1.0f;             assignment
    (s, e) = twosum(a, b);    two results: twosum, twoprod, or a call
    if (a < b && !(x > y)) ... else ...
    return poly(x, NAME) + trunc(y) + fabs(z);
    return2(a, b);            in a sub2 program (return3 in a sub3)

Expressions: + - * /, unary -, the constants inf and nan, and the builtins
fabs trunc cls sgn expo poly(x, TABLE) tbl(TABLE, i).  A call of a program
has the arguments in order.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "vmsrc")
OUT = os.environ.get("GEN_VM_OUT") or os.path.join(HERE, "lc")
STACK_BYTES = 256

# opcodes below 20H: a table index; 80H and up: a jump
LOW = ["RET", "RET2", "RET3", "ADD", "SUB", "MUL", "NEG", "ABS", "DROP",
       "POLY", "TRUNC", "CALL", "CALLU", "CALLB", "CALLU2",
       "SGN", "EXPO", "TBL", "PICKX", "PUTX", "LDCX", "JMPL"]
HIGH = ["JLT", "JLE", "JGT", "JGE", "JEQ", "JNE", "JMP"]
HANDLER = {"RET": "ret", "ADD": "add", "SUB": "sub", "MUL": "mul",
           "NEG": "neg", "ABS": "abs", "DROP": "drop",
           "POLY": "poly", "TRUNC": "trunc", "RET2": "ret2", "RET3": "ret3", "CALL": "call",
           "CALLU": "callu", "CALLB": "callb", "CALLU2": "callu2",
           "SGN": "sgn", "EXPO": "expo",
           "TBL": "tbl_op", "PICKX": "pickx", "PUTX": "putx",
           "LDCX": "ldcx", "JMPL": "jmpl", "JLT": "jlt", "JLE": "jle",
           "JGT": "jgt", "JGE": "jge", "JEQ": "jeq", "JNE": "jne",
           "JMP": "jmp"}
OP = {n: i for i, n in enumerate(LOW)}
OP.update({n: 0x80 + i for i, n in enumerate(HIGH)})
PICK, PUT, LDC = 0x20, 0x40, 0x60
# The constants that most programs use are in the interpreter: LDC 24 to
# LDC 31 push them.  A program has no copy of them.
GLOBALS = [0x00000000, 0x3F800000, 0x437F0000, 0x7F800000, 0x7FC00000,
           0x3F000000, 0x42E40000, 0x40000000]
GLOBAL_BASE = 24
INVERSE = {"JLT": "JGE", "JLE": "JGT", "JGT": "JLE", "JGE": "JLT",
           "JEQ": "JNE", "JNE": "JEQ"}
RELOP = {"<": "JLT", "<=": "JLE", ">": "JGT", ">=": "JGE", "==": "JEQ",
         "!=": "JNE"}


class Error(Exception):
    pass


# ---------------------------------------------------------------- lexer
TOKEN = re.compile(r"""
    (?P<ws>\s+|/\*.*?\*/|//[^\n]*)
  | (?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?[fF]?)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<op><=|>=|==|!=|&&|\|\||[-+*/=<>!(){}\[\],;])
""", re.X | re.S)


def lex(text):
    pos, out = 0, []
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise Error("bad character %r near %r" % (text[pos], text[pos:pos + 20]))
        pos = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group()))
    out.append(("eof", ""))
    return out


def f32bits(v):
    """The bits of the float for V, as the C compiler of uc80 makes them."""
    return struct.unpack("<I", struct.pack("<f", v))[0]


def number(tok):
    if tok.lower().startswith("0x"):
        return struct.unpack("<f", struct.pack("<I", int(tok, 16)))[0]
    return float(tok.rstrip("fF"))


# --------------------------------------------------------------- parser
class Parser:
    def __init__(self, text):
        self.t = lex(text)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k]

    def take(self, val=None):
        tok = self.t[self.i]
        if val is not None and tok[1] != val:
            raise Error("expected %r, found %r (token %d)" % (val, tok[1], self.i))
        self.i += 1
        return tok

    def accept(self, val):
        if self.t[self.i][1] == val:
            self.i += 1
            return True
        return False

    def ident(self):
        tok = self.take()
        if tok[0] != "id":
            raise Error("expected a name, found %r" % tok[1])
        return tok[1]

    # expressions: ("num", bits) ("var", n) ("neg", e) ("bin", op, a, b)
    # ("call", name, [args]) ("poly", e, table) ("tbl", table, e)
    def expr(self):
        e = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            e = ("bin", op, e, self.term())
        return e

    def term(self):
        e = self.unary()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            e = ("bin", op, e, self.unary())
        return e

    def unary(self):
        if self.accept("-"):
            e = self.unary()
            if e[0] == "num":
                return ("num", e[1] ^ 0x80000000)
            return ("neg", e)
        return self.primary()

    def primary(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            return ("num", f32bits(number(val)))
        if kind == "id":
            self.take()
            if val == "inf":
                return ("num", 0x7F800000)
            if val == "nan":
                return ("num", 0x7FC00000)
            if self.accept("("):
                if val == "poly":
                    x = self.expr()
                    self.take(",")
                    table = self.ident()
                    self.take(")")
                    return ("poly", x, table)
                if val == "tbl":
                    table = self.ident()
                    self.take(",")
                    x = self.expr()
                    self.take(")")
                    return ("tbl", table, x)
                args = []
                if self.peek()[1] != ")":
                    while True:
                        args.append(self.expr())
                        if not self.accept(","):
                            break
                self.take(")")
                return ("call", val, args)
            return ("var", val)
        if self.accept("("):
            e = self.expr()
            self.take(")")
            return e
        raise Error("bad expression near %r" % val)

    # conditions: ("cmp", op, a, b) ("not", c) ("and", a, b) ("or", a, b)
    def cond(self):
        c = self.cand()
        while self.accept("||"):
            c = ("or", c, self.cand())
        return c

    def cand(self):
        c = self.cnot()
        while self.accept("&&"):
            c = ("and", c, self.cnot())
        return c

    def cnot(self):
        if self.accept("!"):
            return ("not", self.cnot())
        if self.peek()[1] == "(":
            save = self.i
            try:
                self.take("(")
                c = self.cond()
                self.take(")")
                return c
            except Error:
                self.i = save
        a = self.expr()
        op = self.take()[1]
        if op not in RELOP:
            raise Error("bad comparison operator %r" % op)
        return ("cmp", op, a, self.expr())

    # statements
    def stmt(self):
        kind, val = self.peek()
        if val == "{":
            self.take()
            body = []
            while not self.accept("}"):
                body.append(self.stmt())
            return ("block", body)
        if val == "if":
            self.take()
            self.take("(")
            c = self.cond()
            self.take(")")
            a = self.stmt()
            b = self.stmt() if self.accept("else") else None
            return ("if", c, a, b)
        if val == "return":
            self.take()
            e = self.expr()
            self.take(";")
            return ("return", e)
        if val in ("return2", "return3"):
            self.take()
            self.take("(")
            es = [self.expr()]
            while self.accept(","):
                es.append(self.expr())
            self.take(")")
            self.take(";")
            if len(es) != int(val[-1]):
                raise Error("%s needs %s values" % (val, val[-1]))
            return ("returnn", es)
        if val == "(":
            self.take()
            names = [self.ident()]
            while self.accept(","):
                names.append(self.ident())
            self.take(")")
            self.take("=")
            e = self.expr()
            if e[0] != "call":
                raise Error("several results need a call")
            self.take(";")
            return ("multi", names, e)
        if kind == "id":
            name = self.ident()
            self.take("=")
            e = self.expr()
            self.take(";")
            return ("assign", name, e)
        raise Error("bad statement near %r (token %d)" % (val, self.i))

    def params(self):
        self.take("(")
        names = []
        if self.peek()[1] != ")":
            while True:
                self.take("float")
                names.append(self.ident())
                if not self.accept(","):
                    break
        self.take(")")
        return names

    def func(self):
        private = self.accept("static")
        kind = self.take()[1]
        if kind not in ("float", "sub", "sub2", "sub3"):
            raise Error("bad function kind %r" % kind)
        name = self.ident()
        names = self.params()
        self.take("{")
        body = []
        while not self.accept("}"):
            if self.peek()[1] == "float":
                self.take()
                while True:
                    n = self.ident()
                    init = self.expr() if self.accept("=") else None
                    body.append(("decl", n, init))
                    if not self.accept(","):
                        break
                self.take(";")
            else:
                body.append(self.stmt())
        return {"name": name, "kind": kind, "args": names, "body": body,
                "private": private,
                "results": int(kind[-1]) if kind[-1] in "23" else 1,
                "entry": kind == "float"}

    def extern(self):
        self.take("extern")
        how = self.ident()
        if how not in ("vm", "rtn"):
            raise Error("extern vm or rtn")
        ret = self.take()[1]
        if ret not in ("float", "float2", "float3"):
            raise Error("bad extern result")
        name = self.ident()
        self.take("(")
        n = 0
        if self.peek()[1] != ")":
            while True:
                self.take("float")
                if self.peek()[0] == "id":
                    self.take()
                n += 1
                if not self.accept(","):
                    break
        self.take(")")
        label = None
        if self.accept("="):
            label = self.ident()
        self.take(";")
        return {"how": how, "name": name, "nargs": n, "label": label,
                "results": int(ret[-1]) if ret[-1] in "23" else 1}


def parse_file(text):
    """Returns (tables, functions, externs, aliases)."""
    text = re.sub(r'#include\s+"([^"]+)"',
                  lambda m: open(os.path.join(SRC, m.group(1))).read(), text)
    aliases = {}
    for m in re.finditer(r"/\*\s*ALIAS:\s*(\w+)\s*=\s*([\w ]+?)\s*\*/", text):
        aliases[m.group(1)] = m.group(2).split()
    p = Parser(text)
    tables, funcs, externs = {}, [], {}
    while p.peek()[0] != "eof":
        if p.peek()[1] == "extern":
            e = p.extern()
            externs[e["name"]] = e
        elif p.peek()[1] == "static" and p.peek(1)[1] == "const":
            p.take()
            p.take("const")
            p.take("float")
            name = p.ident()
            p.take("[")
            p.take("]")
            p.take("=")
            p.take("{")
            vals = []
            while True:
                neg = p.accept("-")
                tok = p.take()
                if tok[0] != "num":
                    raise Error("a table holds numbers")
                bits = f32bits(number(tok[1]))
                vals.append(bits ^ 0x80000000 if neg else bits)
                if not p.accept(","):
                    break
                if p.peek()[1] == "}":
                    break
            p.take("}")
            p.take(";")
            tables[name] = vals
        else:
            funcs.append(p.func())
    return tables, funcs, externs, aliases


# -------------------------------------------------------------- codegen
BUILTIN1 = {"fabs": "ABS", "trunc": "TRUNC", "sgn": "SGN", "expo": "EXPO"}


class Gen:
    def __init__(self, fn, tables, funcs, externs, suffix):
        self.fn = fn
        self.tables = tables
        self.funcs = {f["name"]: f for f in funcs}
        self.externs = externs
        self.suffix = suffix
        self.code = []              # ints and tuples
        self.consts = {}            # bits -> uses
        self.labels = 0
        self.vars = {}
        self.refs = set()           # external labels the code uses
        self.calls = set()          # programs it calls
        self.maxh = 0               # the greatest height of the stack
        self._h = 0                 # the height of the stack
        for n in fn["args"]:
            self.vars[n] = self.h
            self.h += 1

    @property
    def h(self):
        return self._h

    @h.setter
    def h(self, v):
        self._h = v
        self.maxh = max(self.maxh, v)

    def emit(self, *b):
        self.code.extend(b)

    def newlabel(self):
        self.labels += 1
        return "L%d" % self.labels

    def place(self, label):
        self.code.append(("label", label))

    def jump(self, op, label):
        self.code.append(("jump", op, label))

    def const(self, bits):
        self.consts[bits] = self.consts.get(bits, 0) + 1
        self.code.append(("ldc", bits))

    def pick(self, slot):
        n = self.h - 1 - slot
        if n > 47:
            raise Error("%s: the stack is too deep" % self.fn["name"])
        self.emit(PICK + n) if n < 32 else self.emit(OP["PICKX"], n)
        self.h += 1

    def put(self, slot):
        n = self.h - 2 - slot
        if n < 0 or n > 47:
            raise Error("%s: bad PUT %d" % (self.fn["name"], n))
        self.emit(PUT + n) if n < 32 else self.emit(OP["PUTX"], n)
        self.h -= 1

    def call_target(self, name, nargs, want):
        """Emit a call; returns the number of results."""
        name = {"twosum": "m_2sum", "twoprod": "m_2prod"}.get(name, name)
        if name in self.funcs:
            f = self.funcs[name]
            if len(f["args"]) != nargs:
                raise Error("%s: %s takes %d arguments" % (self.fn["name"], name, len(f["args"])))
            self.emit(OP["CALL"], ("ref", self.prog_label(f)))
            self.calls.add(self.prog_label(f))
            results = f["results"]
            self.h += results - nargs
            return results
        e = self.externs.get(name)
        if e is None:
            raise Error("%s: unknown function %s" % (self.fn["name"], name))
        if e["nargs"] != nargs:
            raise Error("%s: %s takes %d arguments" % (self.fn["name"], name, e["nargs"]))
        if e["how"] == "vm":
            label = "__v_" + name
            self.refs.add(label)
            self.calls.add(label)
            self.emit(OP["CALL"], ("ref", label))
        else:
            label = e["label"] or name
            self.refs.add(label)
            if nargs == 1:
                self.emit(OP["CALLU2" if e["results"] == 2 else "CALLU"], ("ref", label))
            elif nargs == 2 and e["results"] == 1:
                self.emit(OP["CALLB"], ("ref", label))
            else:
                raise Error("%s: bad routine %s" % (self.fn["name"], name))
        self.h += e["results"] - nargs
        return e["results"]

    def prog_label(self, f):
        if f["private"]:
            return "_v_%s_%s" % (self.suffix, f["name"])
        return "__v_" + f["name"]

    def expr(self, e):
        k = e[0]
        if k == "num":
            self.const(e[1])
            self.h += 1
        elif k == "var":
            if e[1] not in self.vars:
                raise Error("%s: unknown variable %s" % (self.fn["name"], e[1]))
            self.pick(self.vars[e[1]])
        elif k == "neg":
            self.expr(e[1])
            self.emit(OP["NEG"])
        elif k == "bin":
            self.expr(e[2])
            self.expr(e[3])
            if e[1] == "/":
                self.refs.add("__fdiv")
                self.emit(OP["CALLB"], ("ref", "__fdiv"))
            else:
                self.emit(OP[{"+": "ADD", "-": "SUB", "*": "MUL"}[e[1]]])
            self.h -= 1
        elif k == "poly":
            self.expr(e[1])
            first, count = self.table(e[2])
            self.emit(OP["POLY"], first, count)
        elif k == "tbl":
            self.expr(e[2])
            first, count = self.table(e[1])
            self.emit(OP["TBL"], first)
        elif k == "call":
            name, args = e[1], e[2]
            if name in BUILTIN1:
                if len(args) != 1:
                    raise Error("%s takes one argument" % name)
                self.expr(args[0])
                self.emit(OP[BUILTIN1[name]])
                return
            for a in args:
                self.expr(a)
            if self.call_target(name, len(args), 1) != 1:
                raise Error("%s: %s gives two results" % (self.fn["name"], name))
        else:
            raise Error("bad expression " + repr(e))

    def table(self, name):
        if name not in self.tables:
            raise Error("%s: unknown table %s" % (self.fn["name"], name))
        self.code.append(("table", name))
        return ("tfirst", name), len(self.tables[name])

    def jump_if(self, c, want, label):
        k = c[0]
        if k == "cmp":
            self.expr(c[2])
            self.expr(c[3])
            op = RELOP[c[1]]
            self.jump(op if want else INVERSE[op], label)
            self.h -= 2
        elif k == "not":
            self.jump_if(c[1], not want, label)
        elif k == "and":
            if not want:
                self.jump_if(c[1], False, label)
                self.jump_if(c[2], False, label)
            else:
                skip = self.newlabel()
                self.jump_if(c[1], False, skip)
                self.jump_if(c[2], True, label)
                self.place(skip)
        elif k == "or":
            if want:
                self.jump_if(c[1], True, label)
                self.jump_if(c[2], True, label)
            else:
                skip = self.newlabel()
                self.jump_if(c[1], True, skip)
                self.jump_if(c[2], False, label)
                self.place(skip)

    def stmt(self, s, top=False):
        k = s[0]
        h0 = self.h
        if k == "decl":
            if not top:
                raise Error("%s: a declaration inside a block" % self.fn["name"])
            if s[1] in self.vars:
                raise Error("%s: %s declared twice" % (self.fn["name"], s[1]))
            if s[2] is None:
                self.const(0)               # any value
                self.h += 1
            else:
                self.expr(s[2])
            self.vars[s[1]] = self.h - 1
            return
        if k == "assign":
            if s[1] not in self.vars:
                raise Error("%s: unknown variable %s" % (self.fn["name"], s[1]))
            self.expr(s[2])
            self.put(self.vars[s[1]])
        elif k == "multi":
            names, e = s[1], s[2]
            name, args = e[1], e[2]
            for a in args:
                self.expr(a)
            if self.call_target(name, len(args), len(names)) != len(names):
                raise Error("%s: %s does not give %d results" % (self.fn["name"], name, len(names)))
            for n in reversed(names):
                if n not in self.vars:
                    raise Error("%s: unknown variable %s" % (self.fn["name"], n))
                self.put(self.vars[n])
        elif k == "block":
            for t in s[1]:
                self.stmt(t)
        elif k == "if":
            els, end = self.newlabel(), self.newlabel()
            self.jump_if(s[1], False, els if s[3] else end)
            self.stmt(s[2])
            if s[3]:
                self.jump("JMP", end)
                self.place(els)
                self.stmt(s[3])
            self.place(end)
        elif k == "return":
            if self.fn["results"] != 1:
                raise Error("%s: return in a two-result function" % self.fn["name"])
            self.expr(s[1])
            self.emit(OP["RET"], self.h - 1)
            self.h = h0
        elif k == "returnn":
            n = len(s[1])
            if self.fn["results"] != n:
                raise Error("%s: return%d in a function with %d results" % (self.fn["name"], n, self.fn["results"]))
            for e in s[1]:
                self.expr(e)
            self.emit(OP["RET%d" % n], self.h - n)
            self.h = h0
        else:
            raise Error("bad statement " + repr(s))
        if k not in ("return", "returnn") and self.h != h0:
            raise Error("%s: the stack height changed in a %s (%d to %d)"
                        % (self.fn["name"], k, h0, self.h))

    def run(self):
        for s in self.fn["body"]:
            self.stmt(s, top=True)


def assemble(gen):
    """Returns (code bytes with symbolic items, the pool as bit patterns).

    The pool holds the scalar constants (the most used first, so that more
    of them get the one-byte LDC) and then the tables."""
    scal = sorted((b for b in gen.consts if b not in GLOBALS),
                  key=lambda b: (-gen.consts[b], b))
    pool = list(scal)
    placed = {}
    for item in gen.code:
        if isinstance(item, tuple) and item[0] in ("table", "tfirst"):
            if item[1] not in placed:
                placed[item[1]] = len(pool)
                pool.extend(gen.tables[item[1]])
    if len(pool) > 255:
        raise Error("%s: the pool has %d entries" % (gen.fn["name"], len(pool)))
    index = {b: i for i, b in enumerate(pool)}
    items = []                      # ints, ("ref", label), ("label", n),
    for it in gen.code:             # ("jump", op, label)
        if isinstance(it, int):
            items.append(it)
        elif it[0] == "ldc":
            if it[1] in GLOBALS:
                items.append(LDC + GLOBAL_BASE + GLOBALS.index(it[1]))
                continue
            n = index[it[1]]
            if n < GLOBAL_BASE:
                items.append(LDC + n)
            else:
                items.extend([OP["LDCX"], n])
        elif it[0] == "table":
            pass
        else:
            items.append(it)
    # the table operands: replace ("tfirst", name)
    out = []
    for it in items:
        if isinstance(it, tuple) and it[0] == "tfirst":
            out.append(placed[it[1]])
        else:
            out.append(it)
    return out, pool


def resolve_jumps(items):
    """Return a list of ints and ("ref", label) with the jumps encoded.

    A conditional jump that is too far becomes the opposite jump over a
    long jump; an unconditional one becomes a long jump."""
    long_ = set()
    while True:
        size = []
        for n, it in enumerate(items):
            if isinstance(it, int):
                size.append(1)
            elif it[0] == "ref":
                size.append(2)
            elif it[0] == "label":
                size.append(0)
            else:                           # a jump
                if n in long_:
                    size.append(5 if it[1] != "JMP" else 3)
                else:
                    size.append(2)
        pos, labels = [], {}
        p = 0
        for n, it in enumerate(items):
            pos.append(p)
            if isinstance(it, tuple) and it[0] == "label":
                labels[it[1]] = p
            p += size[n]
        grew = False
        for n, it in enumerate(items):
            if isinstance(it, tuple) and it[0] == "jump" and n not in long_:
                off = labels[it[2]] - (pos[n] + 2)
                if not -128 <= off <= 127:
                    long_.add(n)
                    grew = True
        if not grew:
            break
    out = []
    for n, it in enumerate(items):
        if isinstance(it, int) or it[0] == "ref":
            out.append(it)
        elif it[0] == "label":
            pass
        else:
            op, label = it[1], it[2]
            if n in long_:
                if op == "JMP":
                    off = labels[label] - (pos[n] + 3)
                    out += [OP["JMPL"], off & 0xFF, (off >> 8) & 0xFF]
                else:
                    out += [OP[INVERSE[op]], 3, OP["JMPL"]]
                    off = labels[label] - (pos[n] + 5)
                    out += [off & 0xFF, (off >> 8) & 0xFF]
            else:
                off = labels[label] - (pos[n] + 2)
                out += [OP[op], off & 0xFF]
    return out


def db_lines(items, per=12):
    lines, cur = [], []

    def flush():
        if cur:
            lines.append("\tDB\t" + ",".join(cur))
            cur.clear()
    for it in items:
        if isinstance(it, int):
            cur.append("0%02XH" % it if it > 9 else str(it))
            if len(cur) == per:
                flush()
        else:
            flush()
            lines.append("\tDW\t" + it[1])
    flush()
    return lines


def pool_lines(pool):
    lines = []
    for i, bits in enumerate(pool):
        v = struct.unpack("<f", struct.pack("<I", bits))[0]
        lines.append("\tDW\t0%04XH,0%04XH\t; %d: %r" % (bits & 0xFFFF, bits >> 16, i, v))
    return lines


def compile_function(fn, tables, funcs, externs, suffix):
    g = Gen(fn, tables, funcs, externs, suffix)
    g.run()
    items, pool = assemble(g)
    code = resolve_jumps(items)
    lines = ["\tDB\t%d,%d" % (len(fn["args"]), len(pool))]
    lines += pool_lines(pool)
    ncode = sum(2 if not isinstance(c, int) else 1 for c in code)
    lines += db_lines(code)
    return lines, len(pool), ncode, g.refs, g.maxh, g.calls


def generate(name):
    suffix = name[3:] if name.startswith("lc_") else name
    if name == "lc_vm":
        tmpl = open(os.path.join(SRC, "lc_vm.mac.in")).read()
        table = []
        for i in range(0, len(LOW), 4):
            table.append("\tDW\t" + ", ".join("_vm_" + HANDLER[n] for n in LOW[i:i + 4]))
        for i in range(0, len(HIGH), 4):
            table.append("\tDW\t" + ", ".join("_vm_" + HANDLER[n] for n in HIGH[i:i + 4]))
        gk = []
        for bits in GLOBALS:
            v = struct.unpack("<f", struct.pack("<I", bits))[0]
            gk.append("\tDW\t0%04XH,0%04XH\t; %r" % (bits & 0xFFFF, bits >> 16, v))
        text = (tmpl.replace(";;SUBS\n", "")
                .replace(";;GLOBALS", "\n".join(gk))
                .replace(";;GBASE", str(GLOBAL_BASE))
                .replace(";;GOFF", str(4 * GLOBAL_BASE))
                .replace(";;TABLE", "\n".join(table))
                .replace(";;NLOW", str(len(LOW)))
                .replace(";;STACK", str(STACK_BYTES)))
        open(os.path.join(OUT, name + ".mac"), "w").write(text)
        print("  %s.mac" % name)
        return
    src_path = os.path.join(SRC, name + ".vc")
    text = open(src_path).read()
    tables, funcs, externs, aliases = parse_file(text)
    pub, ext, body = [], set(), []
    for fn in funcs:
        n = fn["name"]
        lines, npool, ncode, refs, maxh, calls = compile_function(
            fn, tables, funcs, externs, suffix)
        ext |= refs
        label = ("_v_%s_%s" % (suffix, n)) if fn["private"] else "__v_" + n
        if not fn["private"]:
            pub.append("\tPUBLIC\t" + label)
        body.append("; %s (%d constants, %d bytes of program)" % (n, npool, ncode))
        body.append(";STACK %s %d %s" % (label, maxh, " ".join(sorted(calls))))
        if fn["entry"]:
            names = [n] + aliases.get(n, [])
            for a in names:
                pub.append("\tPUBLIC\t_" + a)
                body.append("_%s:" % a)
            body.append("\tLD\tHL,%s" % label)
            body.append("\tJP\t__vm_run")
            ext.add("__vm_run")
        body.append(label + ":")
        body += lines
        body.append("")
    defined = {("_v_%s_%s" % (suffix, f["name"])) if f["private"] else "__v_" + f["name"]
               for f in funcs}
    head = ("; %s.mac - GENERATED from vmsrc/%s.vc by gen_vm_modules.py\n"
            "; Do not edit this file: edit the .vc file and run the script.\n"
            "\t.Z80\n\n\tCSEG\n" % (name, name))
    for e in sorted(ext - defined):
        head += "\tEXTRN\t%s\n" % e
    open(os.path.join(OUT, name + ".mac"), "w").write(
        head + "\n".join(pub) + "\n\n" + "\n".join(body) + "\n\tEND\n")
    print("  %s.mac" % name)


def main(argv):
    names = argv or ["lc_vm"] + sorted(f[:-3] for f in os.listdir(SRC)
                                       if f.endswith(".vc"))
    for n in names:
        generate(n)


if __name__ == "__main__":
    main(sys.argv[1:])
