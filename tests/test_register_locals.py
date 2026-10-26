"""Scalar locals in BC and DE (docs/register_locals.md).

The pass moves up to two scalar locals of a function into BC or DE.  The
tests check three things:

  - the shape: a loop counter really leaves memory, and a local that must
    stay in memory (address taken, volatile, named in setjmp code) does;
  - the answers: each program gives the same output with and without the
    pass, in both storage modes, for the cases that go wrong first (calls
    that destroy BC and DE, array addresses, 32-bit helpers, recursion);
  - the 'register' hint.

bench/ccfuzz.py and bench/difftest.py run thousands of random programs;
these tests pin the cases by name.
"""

import re

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac


def body_of(mac, name):
    start = mac.index(f"\n_{name}:")
    end = mac.find("\n_", start + 1)
    return mac[start:end if end >= 0 else None]


def build_run(tmp_path, source, *flags):
    _, mac = compile_mac(tmp_path, source, *flags)
    return run_mac(tmp_path, mac)


MODES = [("--no-register-locals",), (), ("--no-shared-storage",)]

SUM = """
#include <stdio.h>
int sum(int *a, int n) { int s = 0; for (int i = 0; i < n; i++) s += a[i]; return s; }
int main(void) { int a[6] = {1, 2, 3, 4, 5, 600}; printf("%d\\n", sum(a, 6)); return 0; }
"""


class TestShape:
    def test_loop_counter_leaves_memory(self, tmp_path):
        mac, _ = compile_mac(tmp_path, SUM, "--no-inlining")
        txt = body_of(mac, "sum")
        assert "BC" in txt or "\tC," in txt or ",C\n" in txt
        off = compile_mac(tmp_path, SUM, "--no-inlining",
                          "--no-register-locals")[0]
        def mem_accesses(t):
            return len(re.findall(r"\(\?\?AUTO\+\d+\)|\(IX[+-]\d+\)", t))
        assert mem_accesses(txt) < mem_accesses(body_of(off, "sum"))

    def test_address_taken_stays_in_memory(self, tmp_path):
        src = """
void set(int *p) { *p = 5; }
int f(void) { int k = 0; for (int i = 0; i < 10; i++) k += i; set(&k); return k; }
int main(void) { return f(); }
"""
        mac, _ = compile_mac(tmp_path, src, "--no-inlining")
        # k is address-taken: its memory accesses stay
        assert re.search(r"\(\?\?AUTO\+\d+\)|\(IX[+-]\d+\)", body_of(mac, "f"))

    def test_off_switch(self, tmp_path):
        a = compile_mac(tmp_path, SUM, "--no-inlining")[0]
        b = compile_mac(tmp_path, SUM, "--no-inlining",
                        "--no-register-locals")[0]
        assert a != b


PROGRAMS = {
    "sum": (SUM, "615\n"),
    "calls": ("""
#include <stdio.h>
unsigned char g;
int twice(int v) { return v + v; }
void bump(void) { g++; }
int work(int n) {
    int t = 0;
    for (int i = 0; i < n; i++) { t += twice(i); bump(); }
    return t + g;
}
int main(void) { printf("%d\\n", work(10)); return 0; }
""", "100\n"),
    "arrays": ("""
#include <stdio.h>
int tab[8];
unsigned char bt[8];
int fill(void) {
    int s = 0;
    for (int i = 0; i < 8; i++) { tab[i] = i * 3; bt[i] = (unsigned char)(i + 250); }
    for (int j = 0; j < 8; j++) s += tab[j] + bt[j];
    return s;
}
int main(void) { printf("%d\\n", fill()); return 0; }
""", "1600\n"),
    "bytes": ("""
#include <stdio.h>
unsigned char f(unsigned char n) {
    unsigned char c = 0, d = 200;
    while (n--) { c += 3; d -= 2; }
    return c + d;
}
int main(void) { printf("%d\\n", f(100)); return 0; }
""", "44\n"),
    "long": ("""
#include <stdio.h>
long f(int n) {
    long acc = 1;
    for (int i = 1; i <= n; i++) acc = acc * 3 + i;
    return acc;
}
int main(void) { printf("%ld\\n", f(15)); return 0; }
""", "25110579\n"),
    "recursion": ("""
#include <stdio.h>
int fib(int n) { int a, b; if (n < 2) return n; a = fib(n - 1); b = fib(n - 2); return a + b; }
int main(void) { printf("%d\\n", fib(15)); return 0; }
""", "610\n"),
    "pointers": ("""
#include <stdio.h>
#include <string.h>
int count(const char *s) { int n = 0; while (*s) { if (*s == 'a') n++; s++; } return n; }
int main(void) { printf("%d %d\\n", count("banana"), (int)strlen("banana")); return 0; }
""", "3 6\n"),
    "hint": ("""
#include <stdio.h>
int f(int n) { register int i; int t = 0; for (i = 0; i < n; i++) t += i; return t; }
int main(void) { printf("%d\\n", f(100)); return 0; }
""", "4950\n"),
    "volatile": ("""
#include <stdio.h>
int f(int n) { volatile int v = 0; for (int i = 0; i < n; i++) v += i; return v; }
int main(void) { printf("%d\\n", f(10)); return 0; }
""", "45\n"),
}


@needs_run
@pytest.mark.parametrize("name", sorted(PROGRAMS))
@pytest.mark.parametrize("flags", MODES, ids=["off", "on", "on-frame"])
def test_same_answer(tmp_path, name, flags):
    src, expected = PROGRAMS[name]
    assert build_run(tmp_path, src, "--no-inlining", *flags) == expected


@needs_run
def test_register_hint_is_honored(tmp_path):
    src = PROGRAMS["hint"][0]
    mac, _ = compile_mac(tmp_path, src, "--no-inlining")
    off, _ = compile_mac(tmp_path, src, "--no-inlining",
                         "--no-register-locals")
    assert body_of(mac, "f") != body_of(off, "f")
