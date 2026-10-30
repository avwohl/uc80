"""Interactions of bug round 4 (enumerator expressions and fixed-type
enumerations, the default ISR save of library work areas, memset to
device memory, printf precision above 255) with family I (LICM, value
ranges, index narrowing, register locals, decimal conversion) and with
the integrate10 repairs (--int 32 short arithmetic, enumerators that
see earlier enumerators).  Every CP/M program runs in cpmemu and is
compared with the host C compiler; the ROM programs run in bench/romrun
in both flows.
"""

import shutil
import subprocess

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401
from tests.rom_flows import FLOWS, build_flow, machine
from tests.test_famh_rom_integration import libs  # noqa: F401

HOST_CC = shutil.which("cc")
LOOPS_OFF = ("--no-licm", "--no-range-cmp", "--no-narrow-index")
CONFIGS = [
    pytest.param((), id="default"),
    pytest.param(("--int", "32"), id="int32"),
    pytest.param(LOOPS_OFF, id="loops-off"),
    pytest.param(("--int", "32") + LOOPS_OFF, id="int32-loops-off"),
    pytest.param(("-O0",), id="O0"),
    pytest.param(("--no-inlining",), id="no-inlining"),
]


def host(tmp_path, src):
    c = tmp_path / "h.c"
    c.write_text(src)
    exe = tmp_path / "h"
    subprocess.run([HOST_CC, "-w", "-std=c2x", "-o", str(exe), str(c)],
                   check=True, capture_output=True, text=True, timeout=120)
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=60).stdout


ENUMS = r'''#include <stdio.h>
enum A { GA = 1, GB = GA << 3, GC = GB | 1, GD };
enum B { V = 5, U = 7, W = V + U, X = sizeof (char) * 4,
         Y = sizeof (long) > 2 ? 100 : 200, Z = (W > 10) ? W * 2 : 0 };
enum F : unsigned char { F0 = 3, F1 = F0 * 2, F2 = F1 + F0,
                         F3 = sizeof (enum A) > 1 ? 250 : 5 };
enum G : short { G0 = -5, G1 = G0 * -3, G2 = G1 + 100 };
enum H : long { H0 = 0x7fff, H1 = H0 + 1, H2 = H1 * 2 };
enum Big { BG0 = 1LL << 40, BG1 = BG0 + 5 };
static enum F bump (int x) { return (enum F) x; }
static enum F pass (enum F f) { return f; }
static int cnt;
int main (void)
{
  int i, t = 0;
  unsigned char b;
  long long ll = BG1;
  printf ("a %d %d %d %d\n", GA, GB, GC, GD);
  printf ("b %d %d %d %d %d %d\n", V, U, W, X, Y, Z);
  printf ("f %d %d %d %d\n", F0, F1, F2, F3);
  printf ("g %d %d %d\n", G0, G1, G2);
  printf ("h %ld %ld %ld\n", (long) H0, (long) H1, (long) H2);
  printf ("big %d %d\n", (int) (ll - BG0), (int) (BG1 >> 40));
  for (i = 0; i < GC; i++) t += GB * 2 + i;
  printf ("l1 %d\n", t);
  for (b = 0; b < F2; b++) t += F1 * W + b;
  printf ("l2 %d\n", t);
  for (i = 0; i < Z; i += U) { t += W - V + i; cnt++; }
  printf ("l3 %d %d\n", t, cnt);
  for (b = F0; b < F3; b += 50) t += b;
  printf ("l4 %d\n", t);
  { enum { L1 = 4, L2 = L1 * 3, L3 = sizeof (long) > 3 ? L2 : L1 };
    for (i = 0; i < L3; i++) t += L1 * 7 + i; }
  printf ("l5 %d\n", t);
  { enum Q : unsigned char { Q0 = 2, Q1 = Q0 * 3, Q2 = Q1 | 8, Q3 = sizeof (long) > 3 ? 200 : 1 };
    enum Q q = (enum Q) 7; for (b = Q0; b < Q2; b += Q0) t += b * Q1 + q;
    printf ("q %d %d %d %d\n", Q1, Q2, Q3, t); q += Q3; printf ("qq %d\n", q); }
  printf ("c %d %d %d\n", bump (300), bump (F3 + 10), pass (bump (513)));
  { enum F q = F3; q += 10; printf ("q %d\n", q); }
  { enum G s = G2; s = (enum G) (s * 400); printf ("s %d\n", s); }
  { enum F u = F3; int r = u + F3; printf ("r %d\n", r); }
  return 0;
}
'''


@pytest.mark.parametrize("flags", CONFIGS)
def test_enumerators_match_host(tmp_path, flags):
    want = host(tmp_path, ENUMS)
    got = build_and_run(tmp_path, ENUMS, flags=flags)
    assert got == want


PRINTF = r'''#include <stdio.h>
int main (void)
{
  int i; unsigned long u = 4000000000UL; long s = -2147483647L;
  printf ("%.300f\n", 0.375);
  printf ("%.256f|\n", 2.5);
  printf ("%10.3f|%-12.5f|%+.2f\n", 3.25, 0.5, 1.75);
  printf ("%lu %ld %d %u %5d|%-6u|\n", u, s, -32767, 65535U, 42, 7U);
  for (i = 0; i < 6; i++) printf ("%d:%lu ", i * 1111, u / (i + 3));
  printf ("\n%.270f\n", 1.0);
  printf ("%x %o %c %s\n", 48879U, 511, 'q', "end");
  return 0;
}
'''


@pytest.mark.parametrize("flags", [pytest.param((), id="default"),
                                   pytest.param(("--int", "32"), id="int32")])
def test_printf_precision_and_decimal_match_host(tmp_path, flags):
    want = host(tmp_path, PRINTF)
    got = build_and_run(tmp_path, PRINTF, flags=flags)
    assert got == want


FIXED32 = r'''#include <stdio.h>
enum U8 : unsigned char { A8 = 200, B8 = 5 };
enum S8 : signed char { P8 = 100, M8 = -100 };
static enum U8 narrow (int x) { return (enum U8) x; }
static enum S8 snarrow (int x) { return (enum S8) x; }
static inline enum U8 inl (int x) { return (enum U8) (x + 1); }
int main (void)
{
  int i, t = 0;
  enum U8 u = A8; enum S8 s = P8;
  for (i = 0; i < 5; i++) { u += 20; s += 30; t += u + s; }
  printf ("a %d %d %d\n", u, s, t);
  for (i = 250; i < 262; i += 3) t += narrow (i) + snarrow (i);
  printf ("b %d\n", t);
  for (i = 0; i < 4; i++) t += inl (i * 100);
  printf ("c %d %d\n", t, inl (255));
  u = (enum U8) (u * 3); s = (enum S8) (s * 3);
  printf ("d %d %d %d\n", u, s, u + s);
  return 0;
}
'''


@pytest.mark.parametrize("flags", CONFIGS)
def test_fixed_enums_short_arithmetic_and_inliner(tmp_path, flags):
    want = host(tmp_path, FIXED32)
    got = build_and_run(tmp_path, FIXED32, flags=flags)
    assert got == want


ISR_LIBM = r'''
#include <math.h>
volatile unsigned char done;
volatile float out[6];
volatile unsigned isr_n;
volatile float iacc;
#pragma uc80 nmi isr
void isr (void)
{
    unsigned char i;
    register unsigned char c = 0;
    int k = (int) isr_n * 3;
    float a = 0;
    for (i = 0; i < 2; i++) {
        a += sin ((float) (k + 1) * 0.1f) + sqrt ((float) (k * 2 + i));
        c += (unsigned char) (k + i);
    }
    iacc = a;
    isr_n += 1 + (c & 0);
}
int main (void)
{
    unsigned char i;
    register unsigned char j = 0;
    unsigned char round;
    float s = 0;
    for (round = 0; round < 12; round++) {
        s = 0;
        j = 0;
        for (i = 0; i < 6; i++) {
            float x = (float) (i + 1) * 0.37f;
            s += sin (x) + cos (x) + sqrt (x * 4.0f);
            out[i] = s;
            j += i;
        }
    }
    done = j == 15;
    for (;;) { }
}
'''


def run_isr(paths, nmi):
    m = machine(paths)
    if nmi:
        m.add_nmi_every(900007, start=3000)
    m.run(max_tstates=300_000_000)
    return m, m.peek("_out", 24), m.peek("_done", 1)


@pytest.mark.parametrize("extra", [(), LOOPS_OFF, ("--int", "32")],
                         ids=["default", "loops-off", "int32"])
@pytest.mark.parametrize("flow", FLOWS)
def test_default_isr_save_with_loops_and_register_locals(
        flow, extra, tmp_path, libs):
    paths, err = build_flow(tmp_path, {"t.c": ISR_LIBM}, flow, libs,
                            extra=extra)
    assert "warning" not in err
    _m, ref, rdone = run_isr(paths, False)
    m, got, done = run_isr(paths, True)
    assert rdone == b"\x01" and done == b"\x01"
    assert m.peek16("_isr_n") >= 3
    assert got == ref
    assert m.rom_writes == []


def test_the_isr_proof_has_teeth(tmp_path):
    paths, _err = build_flow(tmp_path, {"t.c": ISR_LIBM}, "um80", None,
                             extra=("--no-isr-save-workareas",))
    _m, ref, _d = run_isr(paths, False)
    _m, got, _d = run_isr(paths, True)
    assert got != ref


DEVSET = r'''
#include <string.h>
volatile unsigned char *dev = (volatile unsigned char *) 0x4C00;
volatile unsigned sum;
int main (void)
{
    unsigned char i;
    unsigned long j;
    unsigned t = 0;
    int k = 5;
    for (i = 0; i < 6; i++) {
        memset ((void *) 0x4D00, k * 3 + i, 8);
        t += k * 7;
    }
    for (j = 0; j < 4; j++)
        memset ((unsigned char *) 0x4E00 + (3UL * 16 - 40UL + 70000UL - 70000UL), 0x5A, 4 + j);
    for (j = 0; j < 3; j++)
        memset (dev + j * 20, (int) j + 1, 10);
    sum = t;
    return 0;
}
'''


def devset_expected():
    exp = []
    for i in range(6):
        exp += [(0x4D00 + n, (15 + i) & 255) for n in range(8)]
    for j in range(4):
        exp += [(0x4E08 + n, 0x5A) for n in range(4 + j)]
    for j in range(3):
        exp += [(0x4C00 + j * 20 + n, j + 1) for n in range(10)]
    return exp


@pytest.mark.parametrize("extra", [(), LOOPS_OFF, ("--int", "32"),
                                   ("--no-blockops",), ("-O0",)],
                         ids=["default", "loops-off", "int32", "no-blockops",
                              "O0"])
@pytest.mark.parametrize("flow", FLOWS)
def test_device_memset_in_loops_stores_only(flow, extra, tmp_path, libs):
    paths, _err = build_flow(tmp_path, {"t.c": DEVSET}, flow, libs,
                             extra=extra)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    assert m.peek16("_sum") == 210
    assert [(a, v) for (_t, a, v, _pc) in m.rom_writes] == devset_expected()
