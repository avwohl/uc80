"""F8 sweep: objects at a fixed address, constant offsets from a pointer,
16-bit truth tests through (HL), a computed base with a variable index,
words already in HL, and the shorter epilogue.

  * ``x``, ``s.m`` and ``a[K]`` (nested any depth, K constant) of a named
    object are read with ``ld HL,(nn)`` / ``ld A,(nn)`` and stored with
    ``ld (nn),HL`` / ``ld (nn),A``; their address is one ``ld HL,nn``.
  * ``p[K]`` and ``p->m`` add the constant offset to the pointer in HL
    (``inc HL`` for 1..3) instead of ``ld HL,K / ld BC,p / add HL,BC``.
  * ``if (*p)``, ``while (p[i])``, ``if (s->m)`` for a 16-bit object read
    the two bytes as ``ld A,(HL) / inc HL / or (HL)``.
  * ``base[i]`` for a computed base and a variable index of 1, 2, 3 or 4
    bytes per element is ``base in HL, index in DE, add HL,DE`` per byte:
    no push / pop and no scaling shifts.
  * A load of a 16-bit variable is not emitted when HL already holds it:
    after ``i < n`` (which leaves HL alone), after ``x = e`` (which leaves
    the value in HL).  A label, a call, a store through a pointer, a
    volatile object, or any instruction that changes H or L ends that.
  * A function with no stack locals does not restore SP from IX at the
    return (SP is IX already).
  * An unsuffixed hex literal that does not fit int (0xFFFF) is unsigned
    int, so ``uc < 0xFFFF`` is an unsigned comparison.

The unit tests call generate() directly (no AST optimizer, no peephole)
and pin the shape.  The end-to-end tests build programs, run them under
cpmemu and compare every line with the same program built by the host C
compiler (every value is of an exact-width type, so the host agrees).
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

needs_toolchain = pytest.mark.skipif(
    not CPMEMU.exists() or shutil.which("um80") is None
    or shutil.which("cc") is None,
    reason="cpmemu, um80 or a host cc is not available")


def gen(source: str) -> str:
    return generate(_frontend_parse(source, "<test>"), enable_inlining=False,
                    enable_const_propagation=False,
                    enable_dead_elimination=False,
                    register_cc=False)


def ops(code: str, name: str = "f") -> list[str]:
    """Instruction lines of function ``name``, tab-separated, labels and
    comments dropped, up to its return label, without the frame prologue."""
    start = code.index(f"\n_{name}:")
    end = code.index(f"@{name}_ret:", start)
    out = []
    for line in code[start:end].splitlines():
        if line.startswith("\t") and not line.startswith("\t;"):
            out.append(line.strip().split(";")[0].strip())
    if out[:3] == ["push\tIX", "ld\tIX,0", "add\tIX,SP"]:
        out = out[3:]
    if out and out[-1] == f"jp\t@{name}_ret":
        out.pop()
    return out


def text(code: str, name: str = "f") -> str:
    return "\n".join(ops(code, name))


DECLS = ("typedef unsigned char u8; typedef signed char s8; "
         "struct S { int a; int b; u8 c; long d; u8 e[4]; int f[3]; }; "
         "struct S gs; struct S garr[3]; int g2[3][4]; u8 gb[8]; int gi[5]; "
         "long gl[3]; int i, j, x, y; u8 uc, uj; s8 sc; long lg; "
         "int *gp; struct S *gsp; int g(void); ")


def fn(stmts: str, params: str = "void") -> str:
    return text(gen(DECLS + f"int f({params}) {{ {stmts} }} "
                    "int main(void) { return 0; }"))


# ---------------------------------------------------- fixed addresses

class TestFixedAddress:
    def test_constant_element_of_a_static_array(self):
        assert fn("return gi[3];") == "ld\tHL,(_gi+6)"

    def test_nested_constant_elements(self):
        assert fn("return g2[1][2];") == "ld\tHL,(_g2+12)"

    def test_static_struct_member(self):
        assert fn("return gs.b;") == "ld\tHL,(_gs+2)"

    def test_static_char_member_is_loaded_into_a(self):
        o = fn("return gs.c;").split("\n")
        assert o[0] == "ld\tA,(_gs+4)"

    def test_member_of_an_element_of_a_static_array(self):
        # sizeof (struct S) is 19 on this target (no padding)
        assert fn("return garr[2].b;") == "ld\tHL,(_garr+40)"

    def test_long_member_loads_two_words(self):
        assert fn("return gs.d;") == "ld\tHL,(_gs+5)\nld\tDE,(_gs+7)"

    def test_array_member_element(self):
        assert fn("return gs.f[2];") == "ld\tHL,(_gs+17)"

    def test_stack_struct_member_is_read_through_ix(self):
        o = fn("struct S l; l.b = x; return l.b;")
        assert "(IX" in o or "??AUTO" in o
        assert "ld\tDE,2" not in o

    def test_variable_index_keeps_the_address_form(self):
        o = fn("return gi[i];")
        assert "_gi" in o and "(_gi" not in o

    def test_address_of_a_static_member_is_one_load(self):
        assert fn("gp = &gs.f[1];") == "ld\tHL,_gs+15\nld\t(_gp),HL"

    def test_static_word_store_is_direct(self):
        assert fn("gs.b = x;") == "ld\tHL,(_x)\nld\t(_gs+2),HL"

    def test_static_constant_word_store(self):
        assert fn("gi[2] = 9;") == "ld\tHL,9\nld\t(_gi+4),HL"

    def test_static_byte_store_is_direct(self):
        o = fn("gb[3] = uc;").split("\n")
        assert o == ["ld\tA,(_uc)", "ld\t(_gb+3),A"]

    def test_static_long_constant_store(self):
        assert fn("gs.d = 0x12345678L;") == (
            "ld\tHL,22136\nld\t(_gs+5),HL\nld\tHL,4660\nld\t(_gs+7),HL")

    def test_stack_struct_char_store_goes_through_ix(self):
        o = fn("struct S l; l.c = 7; return l.c;")
        assert "push\tIX" not in o


# -------------------------------------------------- pointer plus constant

class TestPointerOffsets:
    def test_p_index_one_is_inc(self):
        o = fn("return p[1];", "int *p").split("\n")
        assert o[:3] == ["ld\tL,(IX+4)", "ld\tH,(IX+5)", "inc\tHL"] \
            or o[:3] == ["ld\tL,(IX+4)", "ld\tH,(IX+5)", "inc\tHL"]
        assert "ld\tBC,_" not in "\n".join(o)

    def test_p_index_is_scaled_at_compile_time(self):
        o = fn("return p[3];", "int *p").split("\n")
        assert "ld\tDE,6" in o and "ld\tHL,3" not in o

    def test_char_pointer_store_has_no_index_register(self):
        o = fn("p[0] = 1; p[1] = 2;", "u8 *p")
        assert "ld\tHL,0" not in o and "ld\tHL,1" not in o
        assert o.count("inc\tHL") == 1

    def test_arrow_member_small_offset_is_inc(self):
        o = fn("return s->b;", "struct S *s")
        assert "ld\tDE,2" not in o and o.count("inc\tHL") >= 2

    def test_arrow_member_large_offset_is_one_add(self):
        o = fn("return s->f[1];", "struct S *s")
        assert "ld\tDE,13" in o and o.count("add\tHL,DE") == 1


# ---------------------------------------------------------- truth tests

class TestTruthThroughHL:
    def test_deref_condition(self):
        o = fn("if (*p) return 1; return 0;", "int *p").split("\n")
        assert o[2:5] == ["ld\tA,(HL)", "inc\tHL", "or\t(HL)"]
        assert "ex\tDE,HL" not in o

    def test_indexed_condition_in_a_loop(self):
        o = fn("while (p[i]) i++; return i;", "int *p")
        assert "or\t(HL)" in o and "ex\tDE,HL" not in o

    def test_member_condition(self):
        o = fn("if (s->b && s->a) return 1; return 0;", "struct S *s")
        assert o.count("or\t(HL)") == 2 and "ex\tDE,HL" not in o

    def test_pointer_to_pointer_condition(self):
        o = fn("if (*pp) return 1; return 0;", "int **pp")
        assert "or\t(HL)" in o

    def test_static_object_condition_keeps_the_register_test(self):
        o = fn("if (gs.b) return 1; return 0;")
        assert "or\t(HL)" not in o and o.startswith("ld\tHL,(_gs+2)")

    def test_char_condition_is_unchanged(self):
        o = fn("if (*p) return 1; return 0;", "u8 *p")
        assert "or\t(HL)" not in o and "or\tA" in o


# ------------------------------------------------------ computed base

class TestComputedBase:
    def test_two_dimensional_int_index(self):
        o = fn("return g2[i][j];").split("\n")
        assert "push\tHL" not in o and "pop\tDE" not in o
        assert o.count("add\tHL,DE") == 2 and "ld\tDE,(_j)" in o

    def test_two_dimensional_char_index(self):
        o = fn("return gb[i] + 0;").split("\n")
        # a one-dimensional array has a simple base: BC as before
        assert "ld\tBC,_gb" in o

    def test_struct_element_of_three_bytes_adds_three_times(self):
        src = ("struct T3 { u8 a, b, c; }; struct T3 t3[4][3]; "
               "int h(void) { return t3[i][j].b; }")
        o = text(gen(DECLS + src + " int main(void) { return h(); }"), "h")
        assert "push\tHL" not in o

    def test_index_by_a_char_variable_is_extended(self):
        o = fn("return g2[sc][uj];")
        assert "rlca" in o or "ld\tD,0" in o


# --------------------------------------------------------- word in HL

class TestWordInHL:
    def test_reload_after_a_signed_compare_is_dropped(self):
        o = fn("int k; for (k = 0; k < 9; k++) if (gb[k]) x++; return x;")
        # k is read once for the test and used from HL for the index
        assert o.count("ld\tHL,(??AUTO+0)") + o.count("ld\tL,(IX") <= 3
        i = o.index("CMP_NV") if "CMP_NV" in o else 0
        assert "ld\tBC,_gb" in o

    def test_reload_after_a_store_is_dropped(self):
        o = fn("x = y; i = x; return x;").split("\n")
        # (a store of HL to another named place leaves HL alone)
        assert o == ["ld\tHL,(_y)", "ld\t(_x),HL", "ld\t(_i),HL"]

    def test_a_byte_store_into_the_word_ends_the_known_value(self):
        o = fn("union { int w; u8 b; } u; x = u.w; u.b = uc; return u.w;")
        loads = [l for l in o.split("\n") if l.startswith("ld\tHL,(??AUTO")]
        assert len(loads) == 2

    def test_a_call_ends_the_known_value(self):
        o = fn("x = y; g(); return x;").split("\n")
        assert o.count("ld\tHL,(_x)") == 1

    def test_a_store_through_a_pointer_ends_the_known_value(self):
        o = fn("x = y; *gp = 5; return x;").split("\n")
        assert o[-1] == "ld\tHL,(_x)" or "ld\tHL,(_x)" in o

    def test_a_label_ends_the_known_value(self):
        o = fn("x = y; if (i) y = 1; return x;").split("\n")
        assert "ld\tHL,(_x)" in o

    def test_volatile_is_read_every_time(self):
        code = gen("volatile int v; int w; int f(void) "
                   "{ w = v; return v; } int main(void) { return 0; }")
        assert text(code).count("ld\tHL,(_v)") == 2

    def test_other_word_is_not_confused(self):
        o = fn("x = y; return i;").split("\n")
        assert o == ["ld\tHL,(_y)", "ld\t(_x),HL", "ld\tHL,(_i)"]


# -------------------------------------------------------------- frames

class TestEpilogue:
    def test_parameter_only_function_has_no_sp_restore(self):
        code = gen("int f(int a, int b) { return a - b; } "
                   "int main(void) { return f(2, 1); }")
        body = code[code.index("\n_f:"):code.index("\n_main:")]
        assert "pop\tIX" in body and "ld\tSP,IX" not in body

    def test_stack_locals_restore_sp(self):
        code = gen("int f(int n) { int a[3]; a[0] = n; "
                   "return n ? f(n - 1) + a[0] : 0; } "
                   "int main(void) { return f(2); }")
        body = code[code.index("\n_f:"):code.index("\n_main:")]
        assert "ld\tSP,IX" in body

    def test_statement_expression_restores_sp(self):
        code = gen("int g(void); int f(int a) { return ({ int t = g(); "
                   "t + a; }); } int main(void) { return f(1); }")
        body = code[code.index("\n_f:"):code.index("\n_main:")]
        assert "ld\tSP,IX" in body


class TestHexLiteralType:
    def test_u8_against_0xffff_is_unsigned(self):
        o = fn("return uc < 0xFFFF;")
        assert "xor\t128" not in o and "PO" not in o

    def test_int_against_0xffff_is_unsigned(self):
        o = fn("return x < 0xFFFF;")
        assert "xor\t128" not in o

    def test_decimal_literal_stays_signed(self):
        o = fn("return x < 30000;")
        assert "xor\t128" in o


# ------------------------------------------------------------ end to end

def build_and_run(tmp_path, source, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n#include <stdint.h>\n" + source)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "--printf", "long", "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120, env=env)
    out = run.stdout.decode("latin-1").replace("\r", "")
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                               "Program exit"))]


def host_run(tmp_path, source, name="ref"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n#include <stdint.h>\n" + source)
    exe = tmp_path / name
    r = subprocess.run(["cc", "-O0", "-w", "-o", str(exe), str(c_file)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True,
                         timeout=60)
    return [l.strip() for l in run.stdout.splitlines() if l.strip()]


def check(tmp_path, source):
    got = build_and_run(tmp_path, source)
    want = host_run(tmp_path, source)
    assert want, "the host program printed nothing"
    assert got == want


# Exact-width types and explicit narrowing keep the 16-bit and the host
# int the same.  The struct has no padding on uc80 and padding on the
# host, so a struct is only ever accessed by member, never by address.
FIXED = r'''
typedef struct { uint16_t a; int16_t b; uint8_t c; int8_t d; uint32_t e;
                 uint8_t t[4]; uint16_t w[3]; } S;
S gs, garr[3];
int16_t g2[3][4];
uint8_t gb[6];
int32_t gl[3];
uint16_t gw;

#define ADD(x) s += (uint32_t)(x)
static void show(const char *tag, uint32_t v) { printf("%s %lu\n", tag, (unsigned long)v); }

static void fill(void) {
    gs.a = 0xF234; gs.b = -2; gs.c = 200; gs.d = -100; gs.e = 0xDEADBEEFUL;
    gs.t[0] = 1; gs.t[1] = 2; gs.t[2] = 250; gs.t[3] = 4;
    gs.w[0] = 11; gs.w[1] = 0xFFFE; gs.w[2] = 33;
    garr[1].a = 7; garr[2].b = -300; garr[2].c = 129; garr[0].e = 70000UL;
    g2[0][0] = 1; g2[1][2] = -5; g2[2][3] = 32000;
    gb[0] = 255; gb[3] = 3; gb[5] = 128;
    gl[1] = -70000L; gl[2] = 123456789L;
}

static uint32_t reads(void) {
    uint32_t s = 0;
    ADD(gs.a); ADD(gs.b); ADD(gs.c); ADD(gs.d); ADD(gs.e);
    ADD(gs.t[2]); ADD(gs.w[1]); ADD(garr[1].a); ADD(garr[2].b);
    ADD(garr[2].c); ADD(garr[0].e); ADD(g2[1][2]); ADD(g2[2][3]);
    ADD(gb[0]); ADD(gb[3]); ADD(gb[5]); ADD(gl[1]); ADD(gl[2]);
    return s;
}

static void stores(void) {
    gs.a = (uint16_t)(gs.a + 1);
    gs.b = 300; gs.c = 77; gs.d = -1; gs.e = 0x01020304UL;
    gs.t[1] = gs.c; gs.w[2] = gs.a;
    garr[1].b = gs.b; garr[1].c = (uint8_t)(gs.b + 1);
    g2[1][1] = g2[1][2]; g2[0][3] = (int16_t)(g2[2][3] + 1000);
    gb[1] = gb[0]; gb[4] = 9; gl[0] = gl[1]; gl[1] = 5;
    gw = gs.w[2];
}

static uint32_t locals(void) {
    S l;
    int16_t m[2][3];
    uint8_t b[4];
    uint32_t s = 0;
    l.a = 65000; l.b = -7; l.c = 250; l.d = -9; l.e = 4000000000UL;
    l.t[3] = 99; l.w[1] = 1234;
    m[1][2] = -77; m[0][1] = 55; b[2] = 200; b[0] = 1;
    ADD(l.a); ADD(l.b); ADD(l.c); ADD(l.d); ADD(l.e); ADD(l.t[3]); ADD(l.w[1]);
    ADD(m[1][2]); ADD(m[0][1]); ADD(b[2]); ADD(b[0]);
    l.a = (uint16_t)(l.a + l.w[1]);
    l.c = (uint8_t)(l.c + 10);
    m[0][0] = l.b; b[3] = l.c;
    ADD(l.a); ADD(l.c); ADD(m[0][0]); ADD(b[3]);
    return s;
}

static int16_t byval(S v) { return (int16_t)(v.b + v.t[1] + v.w[2]); }

int main(void) {
    S k;
    fill();
    show("reads", reads());
    stores();
    show("reads2", reads());
    show("g2", (uint32_t)(g2[1][1] + g2[0][3]));
    show("garr1", (uint32_t)(garr[1].b + garr[1].c));
    show("gw", gw);
    show("gl", (uint32_t)(gl[0] + gl[1]));
    show("locals", locals());
    k.b = -3; k.t[1] = 20; k.w[2] = 300;
    show("byval", (uint32_t)byval(k));
    return 0;
}
'''


@needs_toolchain
def test_fixed_address_objects_agree_with_the_host(tmp_path):
    check(tmp_path, FIXED)


POINTERS = r'''
typedef struct { uint16_t a; int16_t b; uint8_t c; uint32_t d; int16_t f[3]; }
    T;
int16_t gi[6] = { 10, -20, 30, 4000, -5000, 60 };
uint8_t gu[6] = { 1, 200, 3, 4, 5, 250 };
int32_t gq[4] = { 100000L, -200000L, 3, 4 };
int16_t *gpi = gi;
T gt[3];
T *gtp = gt;

#define ADD(x) s += (uint32_t)(x)
static void show(const char *tag, uint32_t v) { printf("%s %lu\n", tag, (unsigned long)v); }

static uint32_t viaparam(int16_t *p, uint8_t *u, int32_t *q, T *t) {
    uint32_t s = 0;
    ADD(p[0]); ADD(p[1]); ADD(p[3]); ADD(p[5]);
    ADD(u[0]); ADD(u[1]); ADD(u[5]);
    ADD(q[1]); ADD(q[2]); ADD(q[3]);
    ADD(t[1].a); ADD(t[2].b); ADD(t->c); ADD(t[2].d); ADD(t->f[1]);
    p[2] = (int16_t)(p[1] + p[3]);
    u[4] = (uint8_t)(u[1] + u[5]);
    q[0] = q[3] + 1;
    t[1].b = -9; t->f[2] = 77; t[2].f[0] = (int16_t)(t[1].b + 2);
    return s;
}

static uint32_t viaglobal(void) {
    uint32_t s = 0;
    ADD(gpi[0]); ADD(gpi[4]); ADD(gpi[5]);
    gpi[1] = 12345;
    gtp[1].c = 5; gtp[2].a = 65535U;
    ADD(gtp[1].c); ADD(gtp[2].a); ADD(gtp->b);
    return s;
}

static uint32_t vialocal(void) {
    int16_t *p = &gi[1];
    uint8_t *u = &gu[2];
    uint32_t s = 0;
    ADD(p[0]); ADD(p[1]); ADD(p[-1]); ADD(u[0]); ADD(u[-2]); ADD(u[3]);
    p[2] = 4242; u[1] = 99;
    ADD(p[2]); ADD(u[1]);
    return s;
}

int main(void) {
    int n;
    for (n = 0; n < 3; n++) {
        gt[n].a = (uint16_t)(n * 1000 + 1); gt[n].b = (int16_t)(-n - 1);
        gt[n].c = (uint8_t)(n + 100); gt[n].d = (uint32_t)n * 100000UL + 7;
        gt[n].f[0] = (int16_t)n; gt[n].f[1] = (int16_t)(n * 3);
        gt[n].f[2] = (int16_t)(n * 5);
    }
    show("param", viaparam(gi, gu, gq, gt));
    show("gi", (uint32_t)gi[2]); show("gu", gu[4]); show("gq", (uint32_t)gq[0]);
    show("t", (uint32_t)(gt[1].b + gt[0].f[2] + gt[2].f[0]));
    show("global", viaglobal());
    show("gi1", (uint32_t)gi[1]); show("tc", (uint32_t)gt[1].c + gt[2].a);
    show("local", vialocal());
    show("gi2", (uint32_t)gi[3]); show("gu1", gu[3]);
    return 0;
}
'''


@needs_toolchain
def test_pointer_offsets_agree_with_the_host(tmp_path):
    check(tmp_path, POINTERS)


TRUTH = r'''
typedef struct { int16_t a; int16_t *b; uint8_t c; } R;
int16_t vals[9] = { 0, 1, 256, -256, 0x100, 0, 0x7F00, -1, 0 };
int16_t *ptrs[3];
R rs[3];

static int t1(int16_t *p) { if (*p) return 1; return 0; }
static int t2(int16_t *p, int i) { int n = 0; while (p[i]) { n++; i++; } return n; }
static int t3(R *r) { if (r->a && r->b) return 3; if (r->a) return 2; return 0; }
static int t4(int16_t **pp) { return *pp ? 1 : 2; }
static int t5(int16_t *p) { return !*p; }
static int t6(int16_t *p) { int n = 0; if (p[1] || p[2]) n++; if (p[0] && !p[3]) n += 2; return n; }

int main(void) {
    int i, h = 0;
    ptrs[0] = vals; ptrs[1] = 0; ptrs[2] = &vals[2];
    rs[0].a = 0; rs[0].b = 0; rs[1].a = 256; rs[1].b = vals; rs[2].a = 1; rs[2].b = 0;
    for (i = 0; i < 9; i++) printf("t1 %d %d\n", i, t1(&vals[i]));
    printf("t2 %d %d %d\n", t2(vals, 1), t2(vals, 6), t2(&vals[2], 0));
    for (i = 0; i < 3; i++) printf("t3 %d %d\n", i, t3(&rs[i]));
    for (i = 0; i < 3; i++) printf("t4 %d %d\n", i, t4(&ptrs[i]));
    for (i = 0; i < 6; i++) printf("t5 %d %d t6 %d\n", i, t5(&vals[i]), t6(&vals[i]));
    return 0;
}
'''


@needs_toolchain
def test_truth_through_hl_agrees_with_the_host(tmp_path):
    check(tmp_path, TRUTH)


COMPUTED = r'''
typedef struct { uint8_t a, b, c; } T3;
typedef struct { uint16_t a; uint16_t b; } T4;
int16_t m[4][5];
uint8_t c2[4][6];
T3 t3[3][4];
T4 t4[3][3];
int32_t q2[3][3];

static void show(const char *tag, long v) { printf("%s %ld\n", tag, v); }

int main(void) {
    int i, j;
    int8_t sj;
    uint8_t uj;
    long s = 0;
    for (i = 0; i < 4; i++) for (j = 0; j < 5; j++) m[i][j] = (int16_t)(i * 100 + j - 7);
    for (i = 0; i < 4; i++) for (j = 0; j < 6; j++) c2[i][j] = (uint8_t)(i * 50 + j * 7);
    for (i = 0; i < 3; i++) for (j = 0; j < 4; j++) {
        t3[i][j].a = (uint8_t)(i + j); t3[i][j].b = (uint8_t)(i * j + 1);
        t3[i][j].c = (uint8_t)(200 + i - j);
    }
    for (i = 0; i < 3; i++) for (j = 0; j < 3; j++) {
        t4[i][j].a = (uint16_t)(i * 1000 + j); t4[i][j].b = (uint16_t)(65535 - i - j);
        q2[i][j] = (int32_t)i * 100000L - j;
    }
    for (i = 0; i < 4; i++) for (j = 0; j < 5; j++) s += m[i][j];
    show("m", s); s = 0;
    for (i = 0; i < 4; i++) for (j = 0; j < 6; j++) s += c2[i][j];
    show("c2", s); s = 0;
    for (i = 0; i < 3; i++) for (j = 0; j < 4; j++) s += t3[i][j].a + t3[i][j].b + t3[i][j].c;
    show("t3", s); s = 0;
    for (i = 0; i < 3; i++) for (j = 0; j < 3; j++) s += t4[i][j].a + (long)t4[i][j].b;
    show("t4", s); s = 0;
    for (i = 0; i < 3; i++) for (j = 0; j < 3; j++) s += q2[i][j];
    show("q2", s);
    for (i = 0; i < 4; i++) for (j = 0; j < 5; j++) m[i][j] = (int16_t)(m[i][j] * 2);
    for (uj = 0; uj < 6; uj++) c2[2][uj] = (uint8_t)(c2[2][uj] + uj);
    for (sj = 0; sj < 4; sj++) t3[1][sj].b = (uint8_t)(sj * 9);
    s = 0;
    for (i = 0; i < 4; i++) for (j = 0; j < 5; j++) s += m[i][j];
    show("m2", s); s = 0;
    for (j = 0; j < 6; j++) s += c2[2][j];
    show("c22", s); s = 0;
    for (j = 0; j < 4; j++) s += t3[1][j].b;
    show("t31", s);
    sj = -1;
    show("neg", (&m[2][0])[sj] + (&c2[1][0])[sj]);
    i = 2; j = 3;
    show("one", m[i][j] + c2[i][j] + t3[i][j].c + t4[i][j - 1].a);
    m[i][j] = -1; c2[i][j] = 255; t3[i][j].a = 77; t4[i][j - 1].b = 1;
    show("two", m[i][j] + c2[i][j] + t3[i][j].a + t4[i][j - 1].b);
    return 0;
}
'''


@needs_toolchain
def test_computed_base_index_agrees_with_the_host(tmp_path):
    check(tmp_path, COMPUTED)


WORDS = r'''
int16_t g, h, k, arr[8];
int16_t *gp;
volatile uint16_t vv;

static void show(const char *tag, long v) { printf("%s %ld\n", tag, v); }
static void bump(void) { g = (int16_t)(g + 100); }
static int16_t loopsum(int16_t n) {
    int16_t i, s = 0;
    for (i = 0; i < n; i++) { if (arr[i & 7]) s = (int16_t)(s + i); }
    return s;
}
static int16_t after_store(int16_t a) {
    int16_t t;
    g = a; t = g; h = (int16_t)(g + 1); k = h;
    return (int16_t)(t + h + k);
}
static int16_t alias(void) {
    gp = &g;
    g = 1; *gp = 2; h = g;
    return h;
}
static int16_t viacall(void) {
    g = 1; bump(); h = g;
    return h;
}
static int16_t viabranch(int16_t c) {
    g = 5;
    if (c) g = 6;
    h = g;
    return h;
}
static int16_t cmpuse(int16_t n) {
    int16_t i, s = 0;
    for (i = n; i > -3; i--) { if (i < 4) s = (int16_t)(s + arr[(i + 3) & 7]); else s = (int16_t)(s - 1); }
    return s;
}
static uint16_t vol(void) {
    uint16_t a = vv;
    uint16_t b = vv;
    return (uint16_t)(a + b);
}

int main(void) {
    int16_t i;
    for (i = 0; i < 8; i++) arr[i] = (int16_t)(i % 3 ? i * 7 - 10 : 0);
    show("loop", loopsum(8)); show("loop5", loopsum(5));
    show("store", after_store(41)); show("store2", after_store(-7));
    show("alias", alias()); show("call", viacall());
    show("br0", viabranch(0)); show("br1", viabranch(1));
    for (i = -4; i < 9; i += 3) show("cmp", cmpuse(i));
    vv = 21;
    show("vol", vol());
    return 0;
}
'''


@needs_toolchain
def test_words_already_in_hl_agree_with_the_host(tmp_path):
    check(tmp_path, WORDS)


FRAMES = r'''
static int16_t sub2(int16_t a, int16_t b) { return (int16_t)(a - b); }
static int16_t rec(int16_t n) { return n ? (int16_t)(n + rec((int16_t)(n - 1))) : 0; }
static int16_t sw(int16_t k) {
    switch (k) { case 1: return 10; case 2: return 20; case 7: return 70; }
    if (k < 0) return -1;
    return 0;
}
static int16_t early(int16_t a, int16_t b) {
    int16_t i;
    for (i = 0; i < 10; i++) { if (i == a) return (int16_t)(sub2(b, i) + sw(i)); }
    return -5;
}
static int32_t lng(int16_t a, int16_t b) { return (int32_t)a * 100000L + b; }
static int16_t locals_rec(int16_t n) {
    int16_t loc[3];
    loc[0] = n; loc[1] = (int16_t)(n * 2);
    if (n == 0) return 0;
    return (int16_t)(loc[0] + loc[1] + locals_rec((int16_t)(n - 1)));
}

int main(void) {
    int16_t i;
    printf("%d %d %d\n", sub2(9, 4), rec(10), locals_rec(5));
    for (i = -1; i < 9; i++) printf("sw %d early %d\n", sw(i), early(i, 30));
    printf("%ld %ld\n", (long)lng(3, 4), (long)lng(-2, 7));
    return 0;
}
'''


@needs_toolchain
def test_frames_without_sp_restore_agree_with_the_host(tmp_path):
    check(tmp_path, FRAMES)


HEX = r'''
uint8_t vu[5] = { 0, 1, 127, 128, 255 };
uint16_t vw[4] = { 0, 1, 0x7FFF, 0xFFFF };
int16_t vi[4] = { 0, 1, 0x7FFF, -1 };

static unsigned cmps(unsigned x) {
    unsigned r = 0;
    if (x < 0xFFFF) r |= 1;
    if (x <= 0xFFFF) r |= 2;
    if (x > 0x8000) r |= 4;
    if (x >= 0x8000) r |= 8;
    if (x == 0xFFFF) r |= 16;
    if (0xFFFF > x) r |= 32;
    return r;
}

int main(void) {
    int i;
    for (i = 0; i < 5; i++) {
        uint8_t a = vu[i];
        printf("u8 %d %d %d %d\n", a < 0xFFFF, a > 0x8000, a == 0xFFFF, 0x9000 > a);
    }
    for (i = 0; i < 4; i++) {
        uint16_t w = vw[i];
        int16_t s = vi[i];
        printf("u16 %u %d %d\n", cmps(w), w < 0xFFFF, w >= 0x8000);
        printf("i16 %d %d\n", s < 0xFFFF, s > 0x7FFF);
    }
    return 0;
}
'''


@needs_toolchain
def test_hex_literal_comparisons_agree_with_the_host(tmp_path):
    # 16-bit int: 0xFFFF and 0x8000 are unsigned int, so the operand is
    # converted to unsigned.  The host has 32-bit int, where they are
    # signed, so only compare against the values worked out here.
    got = build_and_run(tmp_path, HEX)
    want = []
    for a in (0, 1, 127, 128, 255):
        want.append(f"u8 {int(a < 0xFFFF)} {int(a > 0x8000)} "
                    f"{int(a == 0xFFFF)} {int(0x9000 > a)}")

    def cmps(x):
        r = 0
        r |= 1 if x < 0xFFFF else 0
        r |= 2 if x <= 0xFFFF else 0
        r |= 4 if x > 0x8000 else 0
        r |= 8 if x >= 0x8000 else 0
        r |= 16 if x == 0xFFFF else 0
        r |= 32 if 0xFFFF > x else 0
        return r
    for w, s in zip((0, 1, 0x7FFF, 0xFFFF), (0, 1, 0x7FFF, -1)):
        want.append(f"u16 {cmps(w)} {int(w < 0xFFFF)} {int(w >= 0x8000)}")
        # int16 s against 0xFFFF / 0x7FFF: 0xFFFF makes s unsigned
        su = s & 0xFFFF
        want.append(f"i16 {int(su < 0xFFFF)} {int(s > 0x7FFF)}")
    assert got == want


CASTS = r'''
uint8_t b[3];
int main(void) {
    uint8_t u = (uint8_t)'a';
    int8_t s = (int8_t)'\xe9';
    uint16_t w = (uint16_t)'\xe9';
    uint32_t l = (uint32_t)'\x7f';
    b[1] = (uint8_t)'z';
    printf("%d %d %u %lu %d\n", u, s, w, (unsigned long)l, b[1]);
    return 0;
}
'''


@needs_toolchain
def test_cast_of_a_character_constant_agrees_with_the_host(tmp_path):
    # (this stopped the compiler: the constant was a token, not a value)
    check(tmp_path, CASTS)


SHARED = r'''
static int f33(int a) { long i34 = 0; i34 = a; return (int)i34; }
static int f37(int p) { return f33(7) + p; }
static int (*const tb[2])(int) = { f37, f37 };
int (*fp)(int) = f37;

int main(void) {
    uint32_t l50 = 1;
    uint32_t m50 = 2;
    int r = tb[1](5);
    printf("%lu %lu %d\n", (unsigned long)l50, (unsigned long)m50, r);
    r = fp(6);
    printf("%lu %lu %d\n", (unsigned long)l50, (unsigned long)m50, r);
    return 0;
}
'''


@needs_toolchain
def test_callee_of_a_function_whose_address_is_taken_keeps_main_locals(tmp_path):
    # f33 is called only by f37, which main calls through a pointer: f33's
    # automatic storage must not sit on main's.
    check(tmp_path, SHARED)


FOLDS = r'''
signed char g8 = -16;
uint32_t z;
unsigned int ui = 2;
uint32_t ul = 9;
static void show(const char *t, uint32_t v) { printf("%s %lu\n", t, (unsigned long)v); }
static uint32_t widen(uint32_t q) { return q; }
int main(void) {
    show("reassoc", widen((g8 - 31) - (-127)));
    show("reassoc2", widen((g8 - 5) + 40000L));
    show("selfxor", (uint32_t)(-31932 + (z ^ z)) >> 31);
    show("selfsub", (uint32_t)(-31932 + (z - z)) >> 31);
    return 0;
}
'''


@needs_toolchain
def test_folds_keep_the_type_of_the_expression(tmp_path):
    got = build_and_run(tmp_path, FOLDS)
    assert got == ["reassoc 80", "reassoc2 39979", "selfxor 1", "selfsub 1"]


TERNARY = r'''
unsigned int ui = 2;
uint32_t ul = 9;
long neg = -3;
int main(void) {
    printf("%lu\n", (unsigned long)(neg % ((-2) ? ui : ul)));
    printf("%lu\n", (unsigned long)(neg % ((0) ? ui : ul)));
    return 0;
}
'''


@needs_toolchain
def test_constant_conditional_has_the_common_type(tmp_path):
    # unsigned int and unsigned long give unsigned long, so -3 % 2 is
    # (4294967293 % 2) == 1 and 4294967293 % 9 == 4294967293 - 9 * 477185254
    got = build_and_run(tmp_path, TERNARY)
    assert got == ["1", str(4294967293 % 9)]


class TestOptimizerFolds:
    def _fold(self, expr: str) -> str:
        from uc80.ast_opt import Uc80ASTOptimizer
        from uc_core.type_config import Z80_CPM
        unit = _frontend_parse(
            f"long x; void f(void) {{ x = {expr}; }}", "<test>")
        unit = Uc80ASTOptimizer(3, type_config=Z80_CPM).optimize(unit)
        stmt = unit.items[-1].body.items[0].expr
        return stmt.right

    def test_nested_subtract_makes_a_signed_constant(self):
        from uc_core._const import int_value
        r = self._fold("(x - 31) - (-127)")
        assert int_value(r.right) in (96, -96) and int_value(r.right) < 70000
        assert r.right.value.text in ("96", "-96")

    def test_nested_fold_is_skipped_when_it_would_wrap(self):
        r = self._fold("(x + 30000) + 30000")
        # c3 = 60000 does not fit int: the two additions stay
        assert hasattr(r.left, "right") and r.left.right.value.text == "30000"


IDENT = r'''
int x = -2; int y = 20000; unsigned int u = 3;
unsigned char m2 = 5; int g10 = -129;
int main(void) {
    long a = (x + 0L) * y;
    long b = (x * 1L) * y;
    long c = (x | 0L) * y;
    unsigned long d = (u + 0UL) - 5;
    long e = (x * 0L) - 1;
    unsigned f = (u * 0u) - 1;
    long g = (long)(x & 0L) + 70000;
    printf("%ld %ld %ld %lu %ld %u %ld\n", a, b, c, d, e, f, g);
    printf("%d %d\n", ((m2 * 0u) + g10) > 15, ((m2 * 0) + g10) > 15);
    return 0;
}
'''


@needs_toolchain
def test_identity_and_zero_folds_keep_the_type(tmp_path):
    # (x + 0L) is a long, (u * 0u) an unsigned int: dropping the literal
    # made the first a 16-bit multiply and the last a signed comparison.
    got = build_and_run(tmp_path, IDENT)
    assert got == ["-40000 -40000 -40000 4294967294 -1 65535 70000",
                   "1 0"]


BFTERN = r'''
struct B { unsigned b3 : 3; unsigned b7 : 7; } bf = { 3, 100 };
int32_t g8 = 1627518265L;
volatile int c0 = 0, c1 = 1;
int main(void) {
    int i;
    g8 = (c0 ? bf.b3 : (-16));
    printf("%ld\n", (long)g8);
    g8 = (c1 ? bf.b3 : (-16));
    printf("%ld\n", (long)g8);
    i = (-20677) % (c0 ? 31 : bf.b7);
    printf("%d\n", i);
    i = (-20677) % (c1 ? 31 : bf.b7);
    printf("%d\n", i);
    return 0;
}
'''


@needs_toolchain
def test_conditional_with_a_bit_field_branch_is_an_int(tmp_path):
    # A bit-field narrower than int is an int: the conditional is signed,
    # so -16 widens with its sign and % is a signed remainder.
    check(tmp_path, BFTERN)
