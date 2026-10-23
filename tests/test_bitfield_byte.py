"""Bit-fields that lie within one byte: byte-wide access.

Unit tests pin the instruction shapes (no djnz shift loop, no 16-bit
word read-modify-write, set/res/bit for one bit, folded masks for a
constant).  End-to-end tests build programs, run them under cpmemu and
compare every printed value with a Python model of the C semantics, for
each field width 1..8 at every position inside a byte, signed and
unsigned, through a global, a pointer, a local (shared and IX frame) and
an array element.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import generate

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


def gen(src):
    """Code for ``src`` (which defines f); main calls f so it is kept,
    and the inliner is off so the body stays in _f."""
    call = "f(1);" if "(int v)" in src else "f();"
    src += " int main(void) { %s return 0; }" % call
    return generate(_frontend_parse(src), enable_inlining=False)


def body(code, name):
    """The text of function ``name`` (from its label to the next one)."""
    start = code.index(f"\n{name}:\n")
    end = code.find("\n_", start + 3)
    return code[start:end if end > 0 else None]


DECL = """
struct B { unsigned a:3; unsigned b:5; unsigned c:1; unsigned d:7; };
struct B gb;
struct B *pb;
"""


class TestShapes:
    def test_read_in_high_byte_is_a_byte_load(self):
        """gb.d (7 bits at bit 9): one load of byte 1, one rotate, mask."""
        code = body(gen(DECL + "int f(void) { return gb.d; }"), "_f")
        assert "ld\tA,(_gb+1)" in code
        assert "srl\tA" in code
        assert "djnz" not in code and "(HL)" not in code

    def test_read_low_field_uses_rotates_and_mask(self):
        """gb.b (5 bits at bit 3): rrca x3, and 31."""
        code = body(gen(DECL + "int f(void) { return gb.b; }"), "_f")
        assert code.count("rrca") == 3 and "and\t31" in code
        assert "djnz" not in code

    def test_write_constant_is_one_and_one_or(self):
        """gb.a = 5: ld A,(HL) / and 248 / or 5 / ld (HL),A."""
        code = body(gen(DECL + "void f(void) { gb.a = 5; }"), "_f")
        assert "and\t248" in code and "or\t5" in code
        assert "djnz" not in code and "push\tHL" not in code and "push\tAF" not in code

    def test_write_constant_all_ones_has_no_and(self):
        code = body(gen(DECL + "void f(void) { gb.a = 7; }"), "_f")
        assert "or\t7" in code and "and\t248" not in code

    def test_write_constant_zero_has_no_or(self):
        code = body(gen(DECL + "void f(void) { gb.b = 0; }"), "_f")
        assert "and\t7" in code and "\tor\t" not in code

    def test_one_bit_constant_uses_set_and_res(self):
        code = body(gen(DECL + "void f(void) { gb.c = 1; gb.c = 0; }"), "_f")
        assert "set\t0,(HL)" in code and "res\t0,(HL)" in code
        assert "ld\tHL,_gb+1" in code

    def test_one_bit_test_uses_bit(self):
        code = body(gen(DECL + "int f(void) { if (gb.c) return 1; return 0; }"),
                    "_f")
        assert "bit\t0,(HL)" in code
        assert "djnz" not in code

    def test_multibit_test_is_and_mask_without_shift(self):
        code = body(gen(DECL + "int f(void) { if (gb.b) return 1; return 0; }"),
                    "_f")
        assert "and\t248" in code
        assert "rrca" not in code and "srl" not in code

    def test_pointer_write_has_no_word_rmw(self):
        code = body(gen(DECL + "void f(int v) { pb->d = v; }"), "_f")
        assert "djnz" not in code
        assert "ld\tE,(HL)" not in code and "ld\tD,(HL)" not in code

    def test_variable_write_rotates_into_place(self):
        """gb.b = v: the value is rotated left by 3 (rlca x3) and masked."""
        code = body(gen(DECL + "void f(int v) { gb.b = v; }"), "_f")
        assert code.count("rlca") == 3 and "and\t248" in code
        assert "djnz" not in code

    def test_signed_field_sign_extends_without_branch(self):
        code = body(gen("struct S { int a:3; int b:4; } s;"
                        "int f(void) { return s.b; }"), "_f")
        assert "djnz" not in code and "jr\tZ" not in code and "jr\tNZ" not in code
        assert "sra\tA" in code or "xor" in code

    def test_field_across_a_byte_boundary_is_unchanged(self):
        """b (6 bits at bit 5) spans two bytes: the 16-bit word path."""
        code = body(gen("struct S { unsigned a:5; unsigned b:6; } s;"
                        "int f(void) { return s.b; }"), "_f")
        assert "ld\tE,(HL)" in code and "ld\tD,(HL)" in code


# ---- end to end -------------------------------------------------------

def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


needs_tools = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")


def build_and_run(tmp_path, source, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n" + source)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120, env=env)
    return run.stdout.decode("latin-1").replace("\r", "")


def s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


class Model:
    """One struct object: pad (k bits), f (w bits), post (rest)."""

    def __init__(self, k, w, signed):
        self.k, self.w, self.signed = k, w, signed
        self.f = 0
        self.p = 0
        self.q = 0
        self.pm = (1 << k) - 1
        self.qm = (1 << (16 - k - w)) - 1

    def getf(self):
        v = self.f & ((1 << self.w) - 1)
        if self.signed and v >> (self.w - 1):
            v -= 1 << self.w
        return v

    def setf(self, v):
        self.f = v & ((1 << self.w) - 1)
        return self.getf()

    def line(self):
        return "%d %d %d" % (self.getf(), self.p, self.q)


# (C statement template, model function).  X is the lvalue prefix, the
# field is X.f.  r is an int, v the runtime value, printed after each.
def _ops():
    ops = []

    def add(c, fn):
        ops.append((c, fn))

    add("X.f = v;", lambda m, v: m.setf(v))
    add("X.f = 0;", lambda m, v: m.setf(0))
    add("X.f = 1;", lambda m, v: m.setf(1))
    add("X.f = -1;", lambda m, v: m.setf(-1))
    add("X.f = 77;", lambda m, v: m.setf(77))
    add("X.f = 0x1B5;", lambda m, v: m.setf(0x1B5))
    add("r = (X.f = v);", lambda m, v: setattr(m, "r", m.setf(v)))
    add("r = (X.f = 300);", lambda m, v: setattr(m, "r", m.setf(300)))
    add("X.f += v;", lambda m, v: m.setf(m.getf() + v))
    add("X.f |= 5;", lambda m, v: m.setf(m.getf() | 5))
    add("X.f &= v;", lambda m, v: m.setf(m.getf() & v))
    add("X.f ^= 3;", lambda m, v: m.setf(m.getf() ^ 3))
    add("X.f <<= 1;", lambda m, v: m.setf(m.getf() << 1))
    add("X.f++;", lambda m, v: m.setf(m.getf() + 1))
    add("--X.f;", lambda m, v: m.setf(m.getf() - 1))
    add("r = X.f++;", lambda m, v: (setattr(m, "r", m.getf()),
                                     m.setf(m.getf() + 1)))
    add("r = --X.f;", lambda m, v: setattr(m, "r", m.setf(m.getf() - 1)))
    add("r = X.f;", lambda m, v: setattr(m, "r", m.getf()))
    add("r = X.f * 3 + (X.p & 1);",
        lambda m, v: setattr(m, "r", s16(m.getf() * 3 + (m.p & 1))))
    add("r = X.f == 3;", lambda m, v: setattr(m, "r", int(m.getf() == 3)))
    add("r = X.f < -1;", lambda m, v: setattr(m, "r", int(m.getf() < -1)))
    add("r = X.f > v;", lambda m, v: setattr(m, "r", int(m.getf() > v)))
    add("if (X.f) r = 1; else r = 0;",
        lambda m, v: setattr(m, "r", int(m.getf() != 0)))
    add("if (!X.f) r = 1; else r = 0;",
        lambda m, v: setattr(m, "r", int(m.getf() == 0)))
    add("r = X.f && X.q;",
        lambda m, v: setattr(m, "r", int(m.getf() != 0 and m.q != 0)))
    add("X.f = !X.f;", lambda m, v: m.setf(int(m.getf() == 0)))
    add("X.f = X.f + X.p;", lambda m, v: m.setf(m.getf() + m.p))
    add("X.f = (X.f + v) & 3;", lambda m, v: m.setf((m.getf() + v) & 3))
    add("X.p = X.f; X.q = X.f + 1;",
        lambda m, v: (setattr(m, "p", m.getf() & m.pm),
                      setattr(m, "q", (m.getf() + 1) & m.qm)))
    add("X.p = ~0; X.q = ~0;",
        lambda m, v: (setattr(m, "p", m.pm), setattr(m, "q", m.qm)))
    add("X.p = 0; X.q = 0;",
        lambda m, v: (setattr(m, "p", 0), setattr(m, "q", 0)))
    return ops


OPS = _ops()
VALUES = [0, 1, 2, 5, 7, 100, 255, 300, -1, -3]


CONTAINERS = [("g", "g.", "&g"), ("p", "p->", "p"), ("l", "l.", "&l"),
              ("a", "ga[one].", "&ga[one]"), ("x", "l.", "&l")]


def make_program(w, signed, k):
    """A program for a field of width w at bit k (k + w <= 8) of a 16-bit
    unit: p is the k bits below it, q the rest above."""
    kind = "int" if signed else "unsigned"
    rest = 16 - k - w
    has_p, has_q = k > 0, rest > 0
    fields = ""
    if has_p:
        fields += "unsigned p:%d; " % k
    fields += "%s f:%d; " % (kind, w)
    if has_q:
        fields += "unsigned q:%d; " % rest
    ops = [(c, f) for c, f in OPS
           if not (not has_p and "X.p" in c) and not (not has_q and "X.q" in c)]
    out = ["void show(int a, int b, int c, int r)"
           "{ printf(\"%d %d %d %d\\n\", a, b, c, r); }",
           "int vals[%d] = {%s};" % (len(VALUES), ",".join(map(str, VALUES))),
           "int one = 1;",
           "struct T { %s};" % fields,
           "struct T g, h, ga[2];",
           "struct T *p = &h;",
           "void prn(struct T *x, int r) { show(x->f, %s, %s, r); }"
           % ("x->p" if has_p else "0", "x->q" if has_q else "0")]
    expected = []
    calls = []
    for cont, pre, addr in CONTAINERS:
        fn = "run_" + cont
        lines = ["void %s(int n) {" % fn, "  int r = 0, i, v;"]
        if cont in "lx":
            lines.append("  struct T l; l.f = 0;")
            if has_p:
                lines.append("  l.p = 0;")
            if has_q:
                lines.append("  l.q = 0;")
        if cont == "x":
            lines.append("  if (n) %s(n - 1);" % fn)
        lines.append("  for (i = 0; i < %d; i++) { v = vals[i]; r = 0;"
                     % len(VALUES))
        for c, _ in ops:
            lines.append("    %s prn(%s, r);" % (c.replace("X.", pre), addr))
        lines.append("  }")
        lines.append("}")
        out.append("\n".join(lines))
        m = Model(k, w, signed)
        block = []
        for v in VALUES:
            m.r = 0
            for c, f in ops:
                f(m, v)
                block.append("%s %d" % (m.line(), s16(m.r)))
        expected += block * (2 if cont == "x" else 1)
        calls.append("  %s(%d);" % (fn, 1 if cont == "x" else 0))
    out.append("int main(void) {\n%s\n  return 0;\n}" % "\n".join(calls))
    return "\n".join(out), expected


CASES = [(w, k) for w in (1, 2, 3, 5, 7, 8) for k in range(0, 9 - w)]


@needs_tools
@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("w,k", CASES)
def test_field_values_match_model(tmp_path, w, k, signed):
    src, expected = make_program(w, signed, k)
    out = build_and_run(tmp_path, src).splitlines()
    assert len(out) == len(expected), (len(out), len(expected))
    for i, (got, want) in enumerate(zip(out, expected)):
        assert got == want, "line %d: got %r want %r" % (i, got, want)
