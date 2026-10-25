"""Byte-width code paths and the small peephole rules (speed family E).

Each test builds a program, runs it under cpmemu and compares every
value with a Python model; the code-shape tests look at the .mac text.

  * an argument for a char parameter of a function in the program goes
    in L only (``ld L,K``, or a value computed in A): no zero or sign
    extension, because uc80's callee reads the low byte only;
  * a switch on an int-typed value known to be 0..255 compares bytes;
  * ``cp (HL)``, ``sub (HL)`` for a ``*p`` operand: no push AF;
  * ``(uint8_t)(int8_t)c``: one extension;
  * ``(int8_t)x >= 0`` and ``< 0`` are a test of bit 7;
  * peep_local: dead register writes, ``ld L,S / ld H,L / ld L,0``, and
    ``call X / ret`` as ``jp X``.
"""

import re
import subprocess
import sys

import pytest

from tests.test_const_muldiv import REPO, build_and_run, needs_toolchain
from uc80 import peep_local


def lines_of(out):
    return [l.strip() for l in out.splitlines()
            if l.strip() and not l.startswith(("CPU mode", "Loaded",
                                               "Program exit"))]


def compile_mac(tmp_path, source, extra=()):
    c_file = tmp_path / "shape.c"
    c_file.write_text(source)
    mac = tmp_path / "shape.mac"
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac), *extra], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return mac.read_text()


def body(mac, name):
    start = mac.index(f"\n_{name}:")
    end = mac.index(f"@{name}_ret:", start)
    return mac[start:end]


# ------------------------------------------------------- end to end

@needs_toolchain
def test_char_arguments_to_a_local_function(tmp_path):
    src = """
    #include <stdio.h>
    volatile int vi = 1;
    int f(signed char a, unsigned char b, char c) {
        return a * 100 + b + (c == 'x' ? 1000 : 0) + (a < 0 ? 7 : 0);
    }
    int g(unsigned char n) { return n; }
    int main(void) {
        int i; unsigned char u = 200; signed char s = -3; int x = vi;
        for (i = 0; i < 2; i++) {
            printf("%d\\n", f(-5, 250, 'x'));
            printf("%d\\n", f(300, -1, 'y'));
            printf("%d\\n", f(s, u + 100, 'x'));
            printf("%d\\n", f(x - 3, u + x, x + 119));
            printf("%d\\n", f((signed char) (u + 3), g(u + 70), 'x'));
            printf("%d\\n", g(x * 65535) + g(-x) + g(65536 + 7 + x));
            u += 30; s -= 40; x += 100;
        }
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "args")

    def sc(v):
        v &= 255
        return v - 256 if v > 127 else v

    def f(a, b, c):
        a, b, c = sc(a), b & 255, c & 255
        return a * 100 + b + (1000 if c == ord("x") else 0) + (7 if a < 0 else 0)

    def g(n):
        return n & 255
    want = []
    u, s, x = 200, -3, 1
    for i in range(2):
        want += [f(-5, 250, ord("x")), f(300, -1, ord("y")),
                 f(s, u + 100, ord("x")), f(x - 3, u + x, x + 119),
                 f(sc(u + 3), g(u + 70), ord("x")),
                 g(x * 65535) + g(-x) + g(65536 + 7 + x)]
        u = (u + 30) & 255
        s = sc(s - 40)
        x += 100
    assert lines_of(out) == [str(v) for v in want]


@needs_toolchain
def test_switch_on_a_byte_valued_int(tmp_path):
    src = """
    #include <stdio.h>
    volatile unsigned char vc;
    int sw1(unsigned char c) {
        switch (c >> 4) {
        case 0: return 10; case 1: return 11; case 2: return 12;
        case 3: return 13; case 5: return 15; case 14: return 24;
        case 15: return 25; case 300: return 99; case -1: return 98;
        default: return 0;
        }
    }
    int sw2(unsigned char c) {
        switch (c & 7) {
        case 0: return 1; case 1: return 2; case 7: return 3;
        case 256: return 9; default: return 0;
        }
    }
    int sw3(signed char c) {
        switch (c >> 3) {
        case -16: return 1; case -1: return 2; case 0: return 3;
        case 15: return 4; case 200: return 8; case -200: return 7;
        default: return 0;
        }
    }
    int main(void) {
        int i, a = 0, b = 0, d = 0;
        for (i = 0; i < 256; i++) {
            vc = (unsigned char) i;
            a = a * 3 + sw1(vc); b = b * 5 + sw2(vc);
            d = d * 7 + sw3((signed char) vc);
            if ((i & 31) == 31) { printf("%d %d %d\\n", a, b, d); }
        }
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "sw")

    def w16(v):
        v &= 0xFFFF
        return v - 65536 if v > 32767 else v

    def sw1(c):
        return {0: 10, 1: 11, 2: 12, 3: 13, 5: 15, 14: 24, 15: 25}.get(c >> 4, 0)

    def sw2(c):
        return {0: 1, 1: 2, 7: 3}.get(c & 7, 0)

    def sw3(c):
        s = c - 256 if c > 127 else c
        return {-16: 1, -1: 2, 0: 3, 15: 4}.get(s >> 3, 0)
    want, a = [], 0
    a = b = d = 0
    for i in range(256):
        a = w16(a * 3 + sw1(i))
        b = w16(b * 5 + sw2(i))
        d = w16(d * 7 + sw3(i))
        if i & 31 == 31:
            want.append(f"{a} {b} {d}")
    assert lines_of(out) == want


@needs_toolchain
def test_char_operand_through_a_pointer(tmp_path):
    """``a op *p`` and ``a op p->m``: the second operand is read as (HL)."""
    src = """
    #include <stdio.h>
    struct s { unsigned char m; signed char n; };
    unsigned char tab[8] = {0, 1, 100, 127, 128, 200, 254, 255};
    signed char stab[8] = {-128, -100, -1, 0, 1, 50, 127, 5};
    int main(void) {
        int i, j; int c1 = 0, c2 = 0, c3 = 0, c4 = 0;
        unsigned char *p; signed char *q; struct s st, *sp = &st;
        for (i = 0; i < 8; i++) {
            for (j = 0; j < 8; j++) {
                unsigned char a = tab[i]; signed char b = stab[j];
                p = &tab[j]; q = &stab[i]; st.m = tab[j]; st.n = stab[j];
                c1 = c1 * 3 + (a == *p) + 2 * (a < *p) + 4 * (a >= *p);
                c2 = c2 * 3 + (unsigned char) (a - *p) + (unsigned char) (a ^ sp->m);
                c3 = c3 * 3 + (b < *q) + 2 * (b >= *q) + 4 * (b > sp->n);
                c4 = c4 * 3 + (signed char) (b + *q) + (signed char) (b - sp->n);
            }
        }
        printf("%d %d %d %d\\n", c1, c2, c3, c4);
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "cphl")

    def w16(v):
        v &= 0xFFFF
        return v - 65536 if v > 32767 else v

    def s8(v):
        v &= 255
        return v - 256 if v > 127 else v
    tab = [0, 1, 100, 127, 128, 200, 254, 255]
    stab = [-128, -100, -1, 0, 1, 50, 127, 5]
    c1 = c2 = c3 = c4 = 0
    for i in range(8):
        for j in range(8):
            a, b = tab[i], stab[j]
            pj, qi, m, n = tab[j], stab[i], tab[j], stab[j]
            c1 = w16(c1 * 3 + (a == pj) + 2 * (a < pj) + 4 * (a >= pj))
            c2 = w16(c2 * 3 + ((a - pj) & 255) + ((a ^ m) & 255))
            c3 = w16(c3 * 3 + (b < qi) + 2 * (b >= qi) + 4 * (b > n))
            c4 = w16(c4 * 3 + s8(b + qi) + s8(b - n))
    assert lines_of(out) == [f"{c1} {c2} {c3} {c4}"]


@needs_toolchain
def test_char_casts_and_sign_tests(tmp_path):
    src = """
    #include <stdio.h>
    #include <stdint.h>
    volatile unsigned char vc;
    int main(void) {
        int i, a = 0, b = 0, c = 0, d = 0;
        for (i = 0; i < 256; i++) {
            unsigned char u; signed char s; int x;
            vc = (unsigned char) i; u = vc; s = (signed char) vc; x = vc * 3;
            a = a * 3 + (uint8_t) (int8_t) u + (uint8_t) s;
            b = b * 5 + (int8_t) (uint8_t) x + (int8_t) u;
            if ((int8_t) x >= 0) c = c * 3 + 1; else c = c * 3 + 2;
            if ((int8_t) u < 0) d = d * 3 + 1; else d = d * 3 + 2;
            if (s < 0) d = d + 1000;
            if (s >= 0) c = c + 1000;
        }
        printf("%d %d %d %d\\n", a, b, c, d);
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "casts")

    def w16(v):
        v &= 0xFFFF
        return v - 65536 if v > 32767 else v

    def s8(v):
        v &= 255
        return v - 256 if v > 127 else v
    a = b = c = d = 0
    for i in range(256):
        u, s, x = i, s8(i), i * 3
        a = w16(a * 3 + (s8(u) & 255) + (s & 255))
        b = w16(b * 5 + s8(x) + s8(u))
        c = w16(c * 3 + (1 if s8(x) >= 0 else 2))
        d = w16(d * 3 + (1 if s8(u) < 0 else 2))
        if s < 0:
            d = w16(d + 1000)
        if s >= 0:
            c = w16(c + 1000)
    assert lines_of(out) == [f"{a} {b} {c} {d}"]


@needs_toolchain
def test_int_locals_that_hold_bytes(tmp_path):
    """A local int that only receives byte values compares as a byte;
    one that receives a signed char in an unsigned variable, a counter,
    or a value that is not a byte does not."""
    src = """
    #include <stdio.h>
    volatile unsigned char vc;
    unsigned char tab[8] = {3, 200, 7, 255, 0, 128, 127, 1};
    int main(void) {
        int i, acc = 0, acc2 = 0, prev, cur;
        for (i = 0; i < 256; i++) {
            unsigned char c; signed char s; int x, y, z; unsigned w;
            int big, cnt;
            vc = (unsigned char) i; c = vc; s = (signed char) vc;
            x = c; y = c ^ 0x55; z = s; w = s;
            big = c; if (i & 1) big = c + 300;
            cnt = c; cnt++;
            if (x == y) acc += 1;
            if (x < y) acc += 2;
            if (z < 0) acc += 4;
            if (z > y) acc += 8;
            if (w > x) acc += 16;
            if (z == x) acc += 32;
            if (big > 255) acc += 64;
            if (cnt == 256) acc += 128;
            if (z >= -3 && z <= 3) acc += 256;
            acc2 = acc2 * 3 + (x < 100) + (y >= 200) + 2 * (z < 5);
        }
        prev = 0;
        for (i = 0; i < 8; i++) {
            cur = tab[i];
            if (cur > prev) acc2 += 1000;
            if (cur == prev) acc2 += 5000;
            prev = cur;
        }
        printf("%d %d\\n", acc, acc2);
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "holders")

    def w16(v):
        v &= 0xFFFF
        return v - 65536 if v > 32767 else v

    def s8(v):
        return v - 256 if v > 127 else v
    acc = acc2 = 0
    for i in range(256):
        c = i
        s = s8(c)
        x, y, z, w = c, c ^ 0x55, s, s & 0xFFFF
        big = c + 300 if i & 1 else c
        cnt = c + 1
        acc += (x == y) + 2 * (x < y) + 4 * (z < 0) + 8 * (z > y)
        acc += 16 * (w > x) + 32 * (z == x) + 64 * (big > 255)
        acc += 128 * (cnt == 256) + 256 * (-3 <= z <= 3)
        acc2 = w16(acc2 * 3 + (x < 100) + (y >= 200) + 2 * (z < 5))
    tab = [3, 200, 7, 255, 0, 128, 127, 1]
    prev = 0
    for cur in tab:
        if cur > prev:
            acc2 += 1000
        if cur == prev:
            acc2 += 5000
        prev = cur
    assert lines_of(out) == [f"{w16(acc)} {w16(acc2)}"]


@needs_toolchain
def test_tail_calls_return_to_the_callers_caller(tmp_path):
    src = """
    #include <stdio.h>
    int depth(int n);
    int leaf(int n) { return n * 2 + 1; }
    int mid(int n) { return leaf(n + 1); }
    void note(int n) { if (n) printf("n=%d\\n", n); }
    void top(int n) { note(n); }
    int count(int n, int acc) { return n ? count(n - 1, acc + n) : acc; }
    int main(void) {
        top(5); top(0);
        printf("%d %d\\n", mid(4), count(100, 0));
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "tail")
    assert lines_of(out) == ["n=5", "11 5050"]


# ------------------------------------------------------- code shape

def test_byte_argument_has_no_extension(tmp_path):
    mac = compile_mac(tmp_path, """
        #include <stdio.h>
        unsigned char a;
        void out(unsigned char port, unsigned char v) { a = port + v; }
        int main(void) { a = getchar(); out(7, a + 1); return a; }""",
                      ["--no-inlining"])
    main = body(mac, "main")
    seq = main[main.index("call\t_getchar"):main.index("call\t_out")]
    assert "ld\tL,7" in seq
    assert not re.search(r"ld\tH,0", seq)
    assert "ld\tHL,7" not in seq


def test_argument_for_an_external_function_keeps_the_word(tmp_path):
    mac = compile_mac(tmp_path, """
        void ext(unsigned char);
        int main(void) { ext(7); return 0; }""")
    main = body(mac, "main")
    assert "ld\tHL,7" in main and "ld\tL,7" not in main


def test_pointer_operand_is_read_in_place(tmp_path):
    mac = compile_mac(tmp_path, """
        unsigned char *p; unsigned char v;
        int main(void) { return v < *p; }""")
    main = body(mac, "main")
    assert re.search(r"cp\t\(HL\)", main), main
    assert "push\tAF" not in main


def test_signed_byte_against_zero_tests_bit_7(tmp_path):
    mac = compile_mac(tmp_path, """
        #include <stdint.h>
        int x;
        int main(void) { if ((int8_t) x >= 0) return 1; return 2; }""")
    main = body(mac, "main")
    assert "bit\t7,A" in main and "xor\t128" not in main


# ------------------------------------------------------- peep_local

def opt(*lines):
    return peep_local.optimize("\n".join(lines)).split("\n")


def test_dead_register_write_is_deleted():
    assert opt("\tld\tH,0", "\tld\tH,L", "\tret") == ["\tld\tH,L", "\tret"]
    assert opt("\tld\tH,0", "\tld\tHL,5") == ["\tld\tHL,5"]
    # The next instruction reads H: kept.
    assert opt("\tld\tH,0", "\tld\tH,(HL)") == ["\tld\tH,0", "\tld\tH,(HL)"]
    assert opt("\tld\tL,A", "\tld\tA,L") == ["\tld\tL,A", "\tld\tA,L"]
    # A label between: kept.
    assert opt("\tld\tH,0", "@x:", "\tld\tH,L") == ["\tld\tH,0", "@x:",
                                                     "\tld\tH,L"]
    # A memory read might be volatile: kept.
    assert opt("\tld\tA,(_p)", "\tld\tA,1") == ["\tld\tA,(_p)", "\tld\tA,1"]


def test_byte_moved_to_the_high_half():
    assert opt("\tld\tL,(IX+4)", "\tld\tH,L", "\tld\tL,0") == [
        "\tld\tH,(IX+4)", "\tld\tL,0"]
    assert opt("\tld\tL,A", "\tld\tH,L", "\tld\tL,0") == [
        "\tld\tH,A", "\tld\tL,0"]
    assert opt("\tld\tL,(HL)", "\tld\tH,L", "\tld\tL,0") == [
        "\tld\tL,(HL)", "\tld\tH,L", "\tld\tL,0"]


def test_call_then_ret_is_a_jump():
    assert opt("\tcall\t_f", "\tret") == ["\tjp\t_f", "\tret"]
    assert opt("\tcall\t_f", "@f_ret:", "\tret") == ["\tjp\t_f", "@f_ret:",
                                                      "\tret"]
    assert opt("\tcall\t_f", "\tpop\tDE", "\tret") == [
        "\tcall\t_f", "\tpop\tDE", "\tret"]
    assert opt("\tcall\t_setjmp", "\tret") == ["\tcall\t_setjmp", "\tret"]
    assert opt("\tcall\tZ,_f", "\tret") == ["\tcall\tZ,_f", "\tret"]
