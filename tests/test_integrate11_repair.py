"""Repairs after the integrate11 verification: --int 32 arithmetic and
comparisons of char and short operands (an operand that is itself an int
expression, an unsigned short against a signed operand), the integer
constant evaluator (casts, truncating / and %, unsigned conversions, the
operands that are not evaluated, _Alignof), invalid enumerators, duplicate
labels and case values, and an interrupt test that must corrupt the 32-bit
work area when the save is off.  Every program that runs is compared with
the host C compiler (or with the value the test states).
"""

import random
import shutil
import subprocess

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401
from tests.test_struct_return_abi import REPO
from tests.test_integrate11_interactions import host, CONFIGS, LOOPS_OFF
from tests.rom_flows import build_flow, machine

import sys

INT32_CONFIGS = [
    pytest.param(("--int", "32"), id="int32"),
    pytest.param(("--int", "32") + LOOPS_OFF, id="int32-loops-off"),
    pytest.param(("--int", "32", "-O0"), id="int32-O0"),
    pytest.param(("--int", "32", "--no-inlining"), id="int32-no-inlining"),
]


def host_wrapv(tmp_path, src):
    c = tmp_path / "h.c"
    c.write_text(src)
    exe = tmp_path / "h"
    subprocess.run([shutil.which("cc"), "-w", "-std=c2x", "-fwrapv",
                    "-o", str(exe), str(c)],
                   check=True, capture_output=True, text=True, timeout=120)
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=60).stdout


# ---- --int 32: char and short operands ------------------------------------

REPROS = r'''#include <stdio.h>
static unsigned char buf[2] = {0xF4, 0x80};
volatile unsigned char va = 255, vb = 100, vc = 200;
volatile unsigned short vw = 65000, vw2 = 40000;
volatile short vq = -30000, vq2 = 32767, vqm = -1;
int main (void)
{
  unsigned char a = va, b = vb, c = vc;
  unsigned short w = vw, w2 = vw2;
  short q = vq, q2 = vq2, qm = vqm;
  unsigned x = (buf[0] << 8) | buf[1];
  printf ("%u\n", x);
  { int t = a << 12; printf ("%d\n", t); }
  printf ("%d\n", a << (a & 15));
  printf ("%d\n", (c + b) * c);
  printf ("%d\n", (c + b) << 8);
  printf ("%d\n", (c + b) + w);
  printf ("%d\n", c + c + w);
  printf ("%d\n", w2 + (b > 0));
  printf ("%d\n", w2 * (b > 0));
  printf ("%d\n", q2 + (b > 0));
  printf ("%d\n", (unsigned short) q2 + (7 && q2));
  printf ("%d %d\n", q > w2, q < w2);
  printf ("%d %d\n", qm > w2, w2 > qm);
  printf ("%d\n", w2 <= (~a));
  if (w2 > qm) printf ("yes\n"); else printf ("no\n");
  printf ("%d\n", (a + b) - (c + c));
  printf ("%d\n", (a << 8) + (b << 8));
  printf ("%d\n", ~a + w);
  printf ("%d\n", -c * 300);
  printf ("%d\n", (a & b) + (c | a));
  printf ("%d %d\n", w2 / qm, w2 % (q2 - 32770));
  printf ("%d %d\n", (w2 + b) / -3, (w2 + b) % -7);
  printf ("%d\n", w2 > -5);
  printf ("%d\n", w2 > 5);
  return 0;
}
'''


@pytest.mark.parametrize("flags", INT32_CONFIGS)
def test_int32_repro_expressions_match_host(tmp_path, flags):
    assert build_and_run(tmp_path, REPROS, flags) == host(tmp_path, REPROS)


def grid_program(seed, n):
    rnd = random.Random(seed)
    operands = ["a", "s", "w", "q", "(b > 0)", "(a + b)", "(a << 8)", "~a",
                "-s", "(w >> 1)", "(a & b)", "300", "-1", "40000",
                "(unsigned short) q", "(short) w", "(a * b)", "(q > w)",
                "(s + w)", "(q - qq)"]
    pos = ["a", "w", "(a + b)", "(b > 0)", "(a & b)", "300", "(a * b)",
           "(unsigned short) q", "(w >> 1)"]
    nz = ["7", "(b | 1)", "(a | 1)", "(w | 1)", "(q | 1)", "-3", "(s | 1)",
          "(a + 1)", "(qq | 1)"]
    exprs = []
    while len(exprs) < n:
        op = rnd.choice(["+", "-", "*", "&", "|", "^", "<<", "/", "%",
                         "<", ">", "<=", ">=", "==", "!="])
        if op == "<<":
            l, r = rnd.choice(pos), rnd.choice(["1", "7", "8", "12", "(b & 15)"])
        elif op in "/%":
            l, r = rnd.choice(operands), rnd.choice(nz)
        else:
            l, r = rnd.choice(operands), rnd.choice(operands)
        if op == "*" and (("w" in l or "40000" in l) and ("w" in r or "40000" in r)):
            continue
        exprs.append(f"{l} {op} {r}")
    rows = [(255, 100, -128, 65535, 32767, 32767, -32768),
            (0, 1, 127, 40000, -30000, 0, -1),
            (200, 200, -1, 1, -1, 5, 7),
            (1, 255, 0, 32768, 32768 - 65536, -5, 30000),
            (128, 127, -100, 0, 12345, 32767, -32768),
            (77, 3, 5, 65000, -32768, -1, 1)]
    out = ["#include <stdio.h>",
           "static const struct { unsigned char a, b; signed char s; "
           "unsigned short w; short q; short qq; short z; } T[] = {"]
    out += ["{%d,%d,%d,%d,%d,%d,%d}," % r for r in rows]
    out += ["};", "int main (void) { int r;",
            "for (r = 0; r < %d; r++) {" % len(rows),
            "unsigned char a = T[r].a, b = T[r].b; signed char s = T[r].s;",
            "unsigned short w = T[r].w; short q = T[r].q, qq = T[r].qq;"]
    for i, e in enumerate(exprs):
        out.append(f'printf ("{i} %d\\n", (int) ({e}));')
    out += ["}", "return 0; }"]
    return "\n".join(out)


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("flags", INT32_CONFIGS[:2] + INT32_CONFIGS[2:3])
def test_int32_expression_grid_matches_host(tmp_path, flags, seed):
    src = grid_program(seed, 150)
    assert build_and_run(tmp_path, src, flags) == host_wrapv(tmp_path, src)


# ---- the integer constant evaluator ---------------------------------------

def consts_program(wide):
    """The wide parts need a 32-bit int (65535 does not fit a 16-bit int)."""
    w1 = "E1 = (unsigned short) -1, E2," if wide else ""
    w2 = ("UG1 = (unsigned) -1 >> 28, UG2 = ~0u >> 30, UI3 = -1 == 0xffffffffu,"
          if wide else "")
    p1 = 'printf ("%d %d\\n", E1, E2);' if wide else ""
    p2 = 'printf ("%d %d %d\\n", UG1, UG2, UI3);' if wide else ""
    return r"""#include <stdio.h>
enum { X0 = 40,
  A1 = (unsigned char) -1, A2, B1 = (char) 300, B2, C1 = (short) 70000, C2,
  D1 = (signed char) 200, D2, %s
  F1 = -7 / 2, F2 = -7 %% 3, F3 = 7 / -2, F4 = 7 %% -3,
  H1 = 0 && (1 / 0), H2 = 1 || (1 / 0), H3 = 5 ? 6 : (1 / 0), H4 = (5, 3),
  I2 = 3 > 2u };
enum { %s UG3 = -1 < 0u, UG4 = -1 < 0u ? 1 : 2, UI1 = 0u - 1 > 0 };
enum F : unsigned char { FX = (unsigned char) 300, FY };
int main (void)
{
  printf ("%%d %%d %%d %%d %%d %%d %%d %%d\n", A1, A2, B1, B2, C1, C2, D1, D2);
  %s
  printf ("%%d %%d %%d %%d\n", F1, F2, F3, F4);
  printf ("%%d %%d %%d %%d\n", H1, H2, H3, H4);
  printf ("%%d\n", I2);
  %s
  printf ("%%d %%d %%d\n", UG3, UG4, UI1);
  printf ("%%d %%d\n", FX, FY);
  {
    static char arr[(-7 / 2) + 100 + (unsigned char) 300];
    printf ("%%d\n", (int) sizeof arr);
    switch (44) { case (unsigned char) 300: puts ("case"); break;
                  default: puts ("default"); }
    static int v = 1 || (1 / 0);
    printf ("%%d\n", v);
  }
  return 0;
}
""" % (w1, w2, p1, p2)


@pytest.mark.parametrize("flags", [
    pytest.param(("--int", "32"), id="int32"),
    pytest.param(("--int", "32", "--no-inlining"), id="int32-no-inlining")])
def test_constant_evaluator_matches_host(tmp_path, flags):
    src = consts_program(True)
    assert build_and_run(tmp_path, src, flags) == host(tmp_path, src)


def test_constant_evaluator_16_bit_int(tmp_path):
    src = consts_program(False)
    assert build_and_run(tmp_path, src) == host(tmp_path, src)


ALIGN = r"""#include <stdio.h>
struct S { char c; long l; };
static int v = _Alignof (char) + 2;
int main (void)
{
  int r = _Alignof (int);
  printf ("%d %d %d\n", r, (int) _Alignof (struct S), (int) alignof (long));
  printf ("%d %d\n", v, (int) sizeof (char[_Alignof (int) + 1]));
  return 0;
}
"""


def test_alignof_is_one_without_stdalign(tmp_path):
    # The Z80 has no alignment rule: every type has alignment 1 (as in
    # <stdalign.h>), so no include is needed for _Alignof and alignof.
    assert build_and_run(tmp_path, ALIGN) == "1 1 1\n3 2\n"
    assert build_and_run(tmp_path, ALIGN, ("--int", "32")) == "1 1 1\n3 2\n"


# ---- diagnostics ----------------------------------------------------------

def compile_error(tmp_path, src):
    c = tmp_path / "bad.c"
    c.write_text(src)
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c), "-o",
                        str(tmp_path / "bad.mac")],
                       capture_output=True, text=True, cwd=str(REPO))
    return r.returncode, r.stderr


@pytest.mark.parametrize("src,msg", [
    ("int f (int x) { int a = 0; L: a++; L: a++; return a + x; }\n"
     "int main (void) { return f (1); }\n", "duplicate label 'L'"),
    ("int f (int x) { switch (x) { case 1: return 1; case 0 + 1: return 2; }"
     " return 0; }\nint main (void) { return f (1); }\n",
     "duplicate case value 1"),
    ("enum E { A = 1 / 0 };\nint main (void) { return A; }\n",
     "division by zero"),
    ("enum E { A = 7 % 0 };\nint main (void) { return A; }\n",
     "division by zero"),
    ("enum E { A = 1.5 };\nint main (void) { return A; }\n",
     "not an integer constant expression"),
    ("int g;\nenum E { A = 1, B = g };\nint main (void) { return B; }\n",
     "not an integer constant expression"),
], ids=["label", "case", "enum-div0", "enum-mod0", "enum-float", "enum-var"])
def test_invalid_programs_are_errors(tmp_path, src, msg):
    rc, err = compile_error(tmp_path, src)
    assert rc != 0 and msg in err, err


def test_a_dead_division_by_zero_is_no_error(tmp_path):
    src = ("#include <stdio.h>\nenum E { A = 0 && (1 / 0), B = 1 ? 2 : (3 / 0) };\n"
           "int main (void) { printf (\"%d %d\\n\", A, B); return 0; }\n")
    assert build_and_run(tmp_path, src) == "0 2\n"


def test_same_label_in_two_functions_is_fine(tmp_path):
    src = ("#include <stdio.h>\n"
           "static int f (int x) { L: if (x++ < 3) goto L; return x; }\n"
           "static int g (int x) { L: if (x++ < 5) goto L; return x; }\n"
           "int main (void) { printf (\"%d %d\\n\", f (0), g (0)); return 0; }\n")
    assert build_and_run(tmp_path, src) == host(tmp_path, src)


# ---- uc_core defects (the frontend is read-only here) -----------------------

BLOCK_ENUM = r"""#include <stdio.h>
enum { A = 5 };
int main (void)
{
  { enum { A = A * 2 }; printf ("%d\n", A); }
  printf ("%d\n", A);
  return 0;
}
"""

BLOCK_TYPEDEF = r"""#include <stdio.h>
typedef int T;
static int h (void) { typedef char T; enum { T3 = sizeof (T) }; return T3; }
int main (void) { printf ("%d %d\n", h (), (int) sizeof (T)); return 0; }
"""


@pytest.mark.xfail(reason="uc_core frontend.py _rename_block_scope_tags renames "
                   "an enumerator in its own initializer; see CHANGELOG",
                   strict=False)
def test_block_enumerator_in_its_own_initializer(tmp_path):
    assert build_and_run(tmp_path, BLOCK_ENUM) == host(tmp_path, BLOCK_ENUM)


@pytest.mark.xfail(reason="uc_core frontend.py _rename_block_scope_typedefs "
                   "skips a name inside an enumeration body; see CHANGELOG",
                   strict=False)
def test_block_typedef_in_an_enumerator_initializer(tmp_path):
    assert build_and_run(tmp_path, BLOCK_TYPEDEF) == "1 2\n"


# ---- the default ISR save of the 32-bit work area, with teeth -------------

ISR_LONG = r"""
volatile unsigned long out[8];
volatile unsigned char done;
volatile unsigned long hacc;
volatile unsigned isr_n;
#pragma uc80 nmi isr
void isr (void)
{
    unsigned long h = hacc;
    h = h * 40503UL + 12345UL;
    h ^= h >> 3;
    hacc = h;
    isr_n++;
}
int main (void)
{
    unsigned char i, round;
    unsigned long acc = 1;
    for (round = 0; round < 40; round++) {
        for (i = 0; i < 8; i++) {
            acc = acc * 69069UL + 1UL + i;
            acc ^= acc / 13UL;
            acc += acc % 97UL;
            out[i] = acc;
        }
    }
    done = 1;
    for (;;) { }
}
"""


def run_isr_long(paths, nmi, period):
    m = machine(paths)
    if nmi:
        m.add_nmi_every(period, start=1500)
    m.run(max_tstates=60_000_000)
    return m, m.peek("_out", 32), m.peek("_done", 1)


@pytest.mark.parametrize("period", [4001, 6007, 9001])
def test_isr_save_of_the_32bit_work_area_has_teeth(tmp_path, period):
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    on, _e = build_flow(tmp_path / "on", {"t.c": ISR_LONG}, "um80", None)
    off, _e = build_flow(tmp_path / "off", {"t.c": ISR_LONG}, "um80", None,
                         extra=("--no-isr-save-workareas",))
    _m, ref, rdone = run_isr_long(on, False, period)
    m1, got, done1 = run_isr_long(on, True, period)
    m2, bad, done2 = run_isr_long(off, True, period)
    assert rdone == b"\x01" and done1 == b"\x01"
    assert m1.peek16("_isr_n") > 20          # interrupts landed in the loop
    assert got == ref                        # the default save is enough
    assert bad != ref                        # without it the work area breaks
