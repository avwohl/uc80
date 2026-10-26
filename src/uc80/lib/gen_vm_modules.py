#!/usr/bin/env python3
"""Compile the float kernels in vmsrc/ to library modules in lc/.

Each vmsrc/lc_NAME.vc becomes lc/lc_NAME.mac.  A kernel is a function in a
small subset of C; this script turns it into a program for the float
interpreter of lc/lc_vm.mac (see the comment at the top of that file and
docs/libm.md).  The generated .mac files are checked in, so a normal library
build needs only um80 and ulib80.  Run this script after you edit a file in
vmsrc/:

    python3 gen_vm_modules.py            # all modules
    python3 gen_vm_modules.py lc_m_ksin  # one module

Language of a .vc file:

    static const float NAME[] = { 1.0f, 2.0f };       a table of constants
    /* ALIAS: sin = sinf sinl */                      more entry points
    float NAME(float a, float b, float *p, float *q)  a kernel
    {
        float x, y = a * b;       locals (at the top level of the body only)
        x = y + 1.0f;             assignment
        *p = x;                   store through a pointer argument
        *q = toint(x);            store int(x) as 2 bytes
        (s, e) = twosum(a, b);    two results (twosum, twoprod)
        if (a < b && !(x > y)) ... else ...
        return poly(x, NAME) + trunc(y) + fabs(z);
    }

Only float values.  The float arguments come first, then the pointers.
A local that is assigned in an if branch needs a value before the if.
The file lc_vm.vc holds the programs that lc_vm.mac runs itself:

    sub NAME(a, b) { ...; return2(s, e); }
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "vmsrc")
OUT = os.environ.get("GEN_VM_OUT") or os.path.join(HERE, "lc")

OP = {"RET": 0x00, "ADD": 0x01, "SUB": 0x02, "MUL": 0x03, "DIV": 0x04,
      "NEG": 0x05, "ABS": 0x06, "DROP": 0x07, "POLY": 0x08, "TRUNC": 0x09,
      "2SUM": 0x0A, "2PROD": 0x0B, "FIN": 0x0C,
      "JLT": 0x80, "JLE": 0x81, "JGT": 0x82, "JGE": 0x83, "JEQ": 0x84,
      "JNE": 0x85, "JMP": 0x86, "STP0": 0x87, "STP1": 0x88, "STI0": 0x89,
      "STI1": 0x8A}
PICK, PUT, LDC = 0x20, 0x40, 0x60
INVERSE = {"JLT": "JGE", "JLE": "JGT", "JGT": "JLE", "JGE": "JLT",
           "JEQ": "JNE", "JNE": "JEQ"}
RELOP = {"<": "JLT", "<=": "JLE", ">": "JGT", ">=": "JGE", "==": "JEQ",
         "!=": "JNE"}


class Error(Exception):
    pass


# ---------------------------------------------------------------- lexer
TOKEN = re.compile(r"""
    (?P<ws>\s+|/\*.*?\*/|//[^\n]*)
  | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?[fF]?)
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
            raise Error("expected %r, found %r" % (val, tok[1]))
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

    # expressions: ("num", v) ("var", n) ("neg", e) ("bin", op, a, b)
    # ("poly", e, table) ("trunc", e) ("fabs", e)
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
                return ("num", -e[1])
            return ("neg", e)
        return self.primary()

    def primary(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            return ("num", float(val.rstrip("fF")))
        if kind == "id":
            self.take()
            if self.accept("("):
                if val == "poly":
                    x = self.expr()
                    self.take(",")
                    table = self.ident()
                    self.take(")")
                    return ("poly", x, table)
                if val in ("trunc", "fabs"):
                    x = self.expr()
                    self.take(")")
                    return (val, x)
                raise Error("unknown function " + val)
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
            if self.accept(";"):
                return ("return", None)
            e = self.expr()
            self.take(";")
            return ("return", e)
        if val == "return2":
            self.take()
            self.take("(")
            a = self.ident()
            self.take(",")
            b = self.ident()
            self.take(")")
            self.take(";")
            return ("return2", a, b)
        if val == "*":
            self.take()
            p = self.ident()
            self.take("=")
            if self.peek()[1] == "toint":
                self.take()
                self.take("(")
                e = self.expr()
                self.take(")")
                self.take(";")
                return ("sti", p, e)
            e = self.expr()
            self.take(";")
            return ("stp", p, e)
        if val == "(":
            self.take()
            a = self.ident()
            self.take(",")
            b = self.ident()
            self.take(")")
            self.take("=")
            fn = self.ident()
            if fn not in ("twosum", "twoprod"):
                raise Error("unknown function " + fn)
            self.take("(")
            x = self.expr()
            self.take(",")
            y = self.expr()
            self.take(")")
            self.take(";")
            return ("two", a, b, fn, x, y)
        if kind == "id":
            name = self.ident()
            self.take("=")
            e = self.expr()
            self.take(";")
            return ("assign", name, e)
        raise Error("bad statement near %r" % val)

    def func(self):
        is_sub = self.accept("sub")
        if is_sub:
            ret = "sub"
        else:
            ret = self.take()[1]            # float or void
            if ret not in ("float", "void"):
                raise Error("a kernel returns float or void")
        name = self.ident()
        self.take("(")
        floats, ptrs = [], []
        if self.peek()[1] != ")":
            while True:
                if is_sub:
                    floats.append(self.ident())
                else:
                    self.take("float")
                    if self.accept("*"):
                        ptrs.append(self.ident())
                    else:
                        if ptrs:
                            raise Error("a float argument after a pointer")
                        floats.append(self.ident())
                if not self.accept(","):
                    break
        self.take(")")
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
        return {"name": name, "ret": ret, "floats": floats, "ptrs": ptrs,
                "body": body, "sub": is_sub}


def parse_file(text):
    """Returns (tables, functions, aliases)."""
    aliases = {}
    for m in re.finditer(r"/\*\s*ALIAS:\s*(\w+)\s*=\s*([\w ]+?)\s*\*/", text):
        aliases[m.group(1)] = m.group(2).split()
    p = Parser(text)
    tables, funcs = {}, []
    while p.peek()[0] != "eof":
        if p.peek()[1] == "static":
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
                v = float(tok[1].rstrip("fF"))
                vals.append(-v if neg else v)
                if not p.accept(","):
                    break
                if p.peek()[1] == "}":
                    break
            p.take("}")
            p.take(";")
            tables[name] = vals
        else:
            funcs.append(p.func())
    return tables, funcs, aliases


# -------------------------------------------------------------- codegen
def f32bits(v):
    return struct.unpack("<I", struct.pack("<f", v))[0]


class Gen:
    def __init__(self, fn, tables):
        self.fn = fn
        self.tables = tables
        self.code = []              # bytes and ("label", name)/("jump", ...)
        self.consts = []            # bit patterns of the scalar constants
        self.labels = 0
        self.vars = {}
        self.h = 0                  # the height of the stack
        for n in fn["floats"]:
            self.vars[n] = self.h
            self.h += 1

    def emit(self, *b):
        self.code.extend(b)

    def newlabel(self):
        self.labels += 1
        return "L%d" % self.labels

    def place(self, label):
        self.code.append(("label", label))

    def jump(self, op, label):
        self.code.append(("jump", OP[op], label))

    def const(self, v):
        bits = f32bits(v)
        if bits not in self.consts:
            self.consts.append(bits)
        k = self.consts.index(bits)
        if k > 31:
            raise Error("%s: more than 32 constants" % self.fn["name"])
        return k

    def pick(self, slot):
        n = self.h - 1 - slot
        if n > 31:
            raise Error("%s: the stack is too deep" % self.fn["name"])
        self.emit(PICK + n)
        self.h += 1

    def expr(self, e):
        k = e[0]
        if k == "num":
            self.emit(LDC + self.const(e[1]))
            self.h += 1
        elif k == "var":
            if e[1] not in self.vars:
                raise Error("%s: unknown variable %s" % (self.fn["name"], e[1]))
            self.pick(self.vars[e[1]])
        elif k == "neg":
            self.expr(e[1])
            self.emit(OP["NEG"])
        elif k == "fabs":
            self.expr(e[1])
            self.emit(OP["ABS"])
        elif k == "trunc":
            self.expr(e[1])
            self.emit(OP["TRUNC"])
        elif k == "bin":
            self.expr(e[2])
            self.expr(e[3])
            self.emit(OP[{"+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV"}[e[1]]])
            self.h -= 1
        elif k == "poly":
            self.expr(e[1])
            self.emit(OP["POLY"], ("poly", e[2]))
        else:
            raise Error("bad expression " + repr(e))

    def put(self, slot):
        """Pop the top into a variable."""
        n = self.h - 2 - slot
        if n < 0 or n > 31:
            raise Error("%s: bad PUT %d" % (self.fn["name"], n))
        self.emit(PUT + n)
        self.h -= 1

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
                if self.h == 0:
                    raise Error("%s: no value to copy for %s" % (self.fn["name"], s[1]))
                self.pick(self.h - 1)       # a copy of the top: any value
            else:
                self.expr(s[2])
            self.vars[s[1]] = self.h - 1
            return
        if k == "assign":
            if s[1] not in self.vars:
                raise Error("%s: unknown variable %s" % (self.fn["name"], s[1]))
            self.expr(s[2])
            self.put(self.vars[s[1]])
        elif k in ("stp", "sti"):
            if s[1] not in self.fn["ptrs"]:
                raise Error("%s: %s is not a pointer argument" % (self.fn["name"], s[1]))
            self.expr(s[2])
            idx = self.fn["ptrs"].index(s[1])
            self.emit(OP[("STP" if k == "stp" else "STI") + str(idx)])
            self.h -= 1
        elif k == "two":
            self.expr(s[4])
            self.expr(s[5])
            self.emit(OP["2SUM" if s[3] == "twosum" else "2PROD"])
            self.put(self.vars[s[2]])
            self.put(self.vars[s[1]])
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
            if s[1] is not None:
                self.expr(s[1])
            self.emit(OP["RET"])
            self.h = h0
        elif k == "return2":
            self.pick(self.vars[s[1]])
            self.pick(self.vars[s[2]])
            self.emit(OP["FIN"], self.h - 2)
            self.h = h0
        else:
            raise Error("bad statement " + repr(s))
        if k not in ("return", "return2") and self.h != h0:
            raise Error("%s: the stack height changed in a %s" % (self.fn["name"], k))

    def run(self):
        for s in self.fn["body"]:
            self.stmt(s, top=True)
        if not self.fn["sub"]:
            self.emit(OP["RET"])


def assemble(gen, tables):
    """Returns (bytes of the code, the pool list of 32-bit patterns, the
    table placement).  A table is added to the pool after the scalars."""
    pool = list(gen.consts)
    placed = {}
    for item in gen.code:
        if isinstance(item, tuple) and item[0] == "poly":
            name = item[1]
            if name not in tables:
                raise Error("%s: unknown table %s" % (gen.fn["name"], name))
            if name not in placed:
                placed[name] = len(pool)
                pool.extend(f32bits(v) for v in tables[name])
    out, labels, fix = [], {}, []
    for item in gen.code:
        if isinstance(item, int):
            out.append(item)
        elif item[0] == "label":
            labels[item[1]] = len(out)
        elif item[0] == "jump":
            out.append(item[1])
            fix.append((len(out), item[2]))
            out.append(0)
        elif item[0] == "poly":
            if placed[item[1]] > 255 or len(tables[item[1]]) > 255:
                raise Error("pool too large")
            out.append(placed[item[1]])
            out.append(len(tables[item[1]]))
        else:
            raise Error("bad item " + repr(item))
    for pos, name in fix:
        off = labels[name] - (pos + 1)
        if not -128 <= off <= 127:
            raise Error("%s: a jump of %d bytes" % (gen.fn["name"], off))
        out[pos] = off & 0xFF
    return out, pool


def db_lines(data, per=12):
    lines = []
    for i in range(0, len(data), per):
        lines.append("\tDB\t" + ",".join("0%02XH" % b if b > 9 else str(b)
                                          for b in data[i:i + per]))
    return lines


def pool_lines(pool, tables_inv):
    lines = []
    for i, bits in enumerate(pool):
        v = struct.unpack("<f", struct.pack("<I", bits))[0]
        lines.append("\tDW\t0%04XH,0%04XH\t; %d: %r" % (bits & 0xFFFF, bits >> 16, i, v))
    return lines


def compile_function(fn, tables, label):
    g = Gen(fn, tables)
    g.run()
    code, pool = assemble(g, tables)
    lines = []
    if not fn["sub"]:
        lines.append("\tDB\t%d,%d,%d" % (len(fn["floats"]), len(fn["ptrs"]), len(pool)))
    lines += pool_lines(pool, None)
    lines += db_lines(code)
    return lines, len(pool), len(code)


def generate(name):
    src_path = os.path.join(SRC, name + ".vc")
    text = open(src_path).read()
    tables, funcs, aliases = parse_file(text)
    suffix = name[3:] if name.startswith("lc_") else name
    out = []
    if name == "lc_vm":
        tmpl = open(os.path.join(SRC, "lc_vm.mac.in")).read()
        subs = []
        for fn in funcs:
            if not fn["sub"]:
                raise Error("lc_vm.vc holds sub programs only")
            lines, _, _ = compile_function(fn, tables, fn["name"])
            subs.append("_vm_%s:" % fn["name"])
            subs += lines
        open(os.path.join(OUT, name + ".mac"), "w").write(
            tmpl.replace(";;SUBS", "\n".join(subs)))
        print("  %s.mac" % name)
        return
    pub = []
    body = []
    for fn in funcs:
        if fn["sub"]:
            raise Error("only lc_vm.vc has sub programs")
        n = fn["name"]
        label = "_" + n
        prog = "_%s_%s_prog" % (suffix, n.lstrip("_"))
        lines, npool, ncode = compile_function(fn, tables, label)
        names = [n] + aliases.get(n, [])
        for a in names:
            pub.append("\tPUBLIC\t_" + a)
        body.append("; %s (%d constants, %d bytes of program)" % (n, npool, ncode))
        for a in names:
            body.append("_%s:" % a)
        body.append("\tLD\tHL,%s" % prog)
        body.append("\tJP\t__vm_run")
        body.append(prog + ":")
        body += lines
        body.append("")
    head = ("; %s.mac - GENERATED from vmsrc/%s.vc by gen_vm_modules.py\n"
            "; Do not edit this file: edit the .vc file and run the script.\n"
            "\t.Z80\n\n\tCSEG\n\n\tEXTRN\t__vm_run\n" % (name, name))
    open(os.path.join(OUT, name + ".mac"), "w").write(
        head + "\n".join(pub) + "\n\n" + "\n".join(body) + "\n\tEND\n")
    print("  %s.mac" % name)


def main(argv):
    names = argv or sorted(f[:-3] for f in os.listdir(SRC) if f.endswith(".vc"))
    for n in names:
        generate(n)


if __name__ == "__main__":
    main(sys.argv[1:])
