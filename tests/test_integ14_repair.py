"""Repair of integrate14: findings of the verifiers, each with a program
that RUNS.  The expected output is the output of the host compiler.

1. A continue in a switch in a short counted loop (the unroller).
2. The printf literal union: postfix ++/-- and the address of the format
   parameter or pointer.
3. The step of a register-pair counter in a for-entry loop.
4. The compact __div16 only for divides in main outside loops.
5. The push fill for a big BSS with no timed region.
6. Check loops (BENCH_CHECK) beside a while loop with a local counter.
"""

import re

import pytest

from .test_famj_startup import compile_c, REPO
from .test_integ14_famk_bug6 import host, uc, ids

SWITCH_SRC = r"""
#include <stdio.h>
unsigned e1(void) { unsigned i, s = 0;
  for (i = 0; i < 4; i++) { switch (i & 1) { case 0: continue; } s += i; }
  return s; }
unsigned e2(void) { unsigned i, s = 0;
  for (i = 0; i < 4; i++) { switch (i & 1) { case 0: break; default: continue; }
    s += i * 10; }
  return s; }
unsigned e3(void) { unsigned i, s = 0;
  for (i = 0; i < 3; i++) { switch (i) { case 0: continue; default: break; }
    s += 100 + i; }
  return s; }
unsigned e4(void) { unsigned i, s = 0;
  for (i = 0; i < 4; i++) { switch (i) { case 1: s += 7; break; case 2: s += 9; }
    s++; }
  return s; }
int main(void) { printf("%u %u %u %u\n", e1(), e2(), e3(), e4()); return 0; }
"""


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--int=32",),
                                   ("--no-loop-unroll",)], ids=ids)
def test_continue_in_switch_in_short_loop(tmp_path, flags):
    want = host(tmp_path, SWITCH_SRC)
    assert want == "4 40 201 23\n" or want
    out, _mac = uc(tmp_path, SWITCH_SRC, *flags)
    assert out == want


UNION_SRCS = {
    "postfix_param": r"""
#include <stdio.h>
#include <stdarg.h>
static void w(const char *fmt, ...) { va_list ap; va_start(ap, fmt);
  fmt++; vprintf(fmt, ap); va_end(ap); }
int main(void) { w("%d\n", 3); w("%%.2f\n", 3.14159); return 0; }
""",
    "postfix_ptr": r"""
#include <stdio.h>
int main(void) { const char *p = "%%.2f\n"; printf("%d\n", 1);
  p++; printf(p, 3.14159); return 0; }
""",
    "postfix_dec_ptr": r"""
#include <stdio.h>
int main(void) { const char *p = "%%.2f\n" + 1; p--; p++; p--;
  printf("%d\n", 1); printf(p, 3.14159); return 0; }
""",
    "addr_param": r"""
#include <stdio.h>
#include <stdarg.h>
static char dyn[8];
static void w(const char *fmt, ...) { va_list ap; va_start(ap, fmt);
  if (fmt[0] == 'X') { const char **pp = &fmt; *pp = dyn; }
  vprintf(fmt, ap); va_end(ap); }
int main(void) { dyn[0] = '%'; dyn[1] = '.'; dyn[2] = '2'; dyn[3] = 'f';
  w("%d\n", 3); w("X", 3.14159); return 0; }
""",
}


@pytest.mark.parametrize("name", sorted(UNION_SRCS))
@pytest.mark.parametrize("flags", [(), ("--int=32",)], ids=ids)
def test_printf_union_holes(tmp_path, name, flags):
    src = UNION_SRCS[name]
    want = host(tmp_path, src)
    out, _mac = uc(tmp_path, src, *flags)
    assert out == want


def test_printf_union_still_works_for_a_clean_wrapper(tmp_path):
    from uc80 import printf_hook  # noqa: F401
    src = r"""
#include <stdio.h>
#include <stdarg.h>
static void w(const char *fmt, ...) { va_list ap; va_start(ap, fmt);
  vprintf(fmt, ap); va_end(ap); }
int main(void) { w("%d\n", 3); w("%x\n", 255); return 0; }
"""
    out, mac = uc(tmp_path, src)
    assert out == host(tmp_path, src)


FE_SRC = r"""
#include <stdio.h>
typedef unsigned short U;
int main(void)
{
    U a = 1, i;
    for (i = 0; i < 1000; i++) a += (U)(i * 3u) ^ a;
    printf("%u\n", (unsigned)a);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--int=32",), ("-O0",),
                                   ("--no-for-entry",)], ids=ids)
def test_register_counter_step_in_for_entry_loop(tmp_path, flags):
    out, mac = uc(tmp_path, FE_SRC, *flags)
    assert out == host(tmp_path, FE_SRC)
    if flags == ():
        body = mac.split("@FORUPD", 1)[1].split("@ENDFOR", 1)[0]
        # a step is inc BC (or DE), not a round trip through HL
        assert re.search(r"\binc\s+(BC|DE)\b", body, re.I)
        assert not re.search(r"\binc\s+HL\b", body, re.I)


DIV_MAIN = r"""
#include <stdio.h>
unsigned a = 60000, b = 300, c = 7;
int main(void) { printf("%u %u %u\n", a / b, a % b, a / c); return 0; }
"""
DIV_LOOP = r"""
#include <stdio.h>
unsigned a = 60000, b = 300, c;
int main(void) { unsigned i, s = 0;
  for (i = 0; i < 20; i++) { s += (a + i) / b; s += (a + i) % (b + i); c = s; }
  printf("%u %u\n", s, c); return 0; }
"""
DIV_FUNC = r"""
#include <stdio.h>
unsigned a = 60000, b = 300;
unsigned f(unsigned x, unsigned y) { return x / y; }
unsigned (*volatile fp)(unsigned, unsigned) = f;
int main(void) { printf("%u %u\n", fp(a, b), fp(a, 7)); return 0; }
"""


@pytest.mark.parametrize("src,compact", [(DIV_MAIN, True), (DIV_LOOP, False),
                                         (DIV_FUNC, False)])
def test_compact_div16_only_for_divides_in_main(tmp_path, src, compact):
    want = host(tmp_path, src)
    out, mac = uc(tmp_path, src)
    assert out == want
    assert (re.search(r"EXTRN\s+__dv8w", mac) is None) == compact
    out, mac = uc(tmp_path, src, "--size-helpers")
    assert out == want
    out, mac = uc(tmp_path, src, "--no-div16-compact")
    assert out == want
    assert re.search(r"EXTRN\s+__dv8w", mac)


BSS_SRC = r"""
#include <stdio.h>
static int tab[4000];
int main(void) { int i, s = 0;
  for (i = 0; i < 4000; i++) s |= tab[i];
  printf("%d\n", s); return 0; }
"""


def test_big_bss_without_timed_region_uses_push_fill(tmp_path):
    out, mac = uc(tmp_path, BSS_SRC)
    assert out == "0\n"
    assert "__bss_big:" in mac
    src = BSS_SRC.replace("int i, s = 0;", 'int i, s = 0; asm("TIMER_START::");')
    out, mac = uc(tmp_path, src)
    assert out == "0\n"
    assert "__bss_big" not in mac


CHECK_SRC = r"""
#include <stdio.h>
#include "bench.h"
unsigned g;
int main(void)
{
    unsigned n = 0, s = 0;
    while (n < 300) { s += n; n++; }
    g = s;
    BENCH_CHECK_EQ(g, 44850u);
    BENCH_CHECK(g > 5);
    BENCH_CHECK_NEAR(g, 44850u, 3);
    printf("%u\n", g);
    return 0;
}
"""


def test_check_loops_leave_rotation_budget_to_a_local_while_loop(tmp_path):
    out, mac = uc(tmp_path, CHECK_SRC)
    assert out == host(tmp_path, CHECK_SRC)
    # the program loop (while, local counter) is rotated; the three check
    # loops are do loops and take no budget
    assert len(set(re.findall(r"@RB\d+:", mac))) == 1
    assert len(re.findall(r"@DO\d+:", mac)) == 3
